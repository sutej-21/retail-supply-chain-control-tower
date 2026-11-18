"""Alert evaluation, de-duplication, history logging and Slack / email delivery.

Alert rules (thresholds in config/settings.py, overridable via env vars)
------------------------------------------------------------------------
CRITICAL_INVENTORY_RISK        stock below safety stock                         [SIM]
PROJECTED_BELOW_SAFETY_STOCK   stock-out projected within the supplier lead time [SIM]
STOCK_BELOW_REORDER_POINT      net available <= reorder point                   [SIM]
FORECAST_EXCEEDS_INVENTORY     Prophet 30-day demand > available + on order     [SIM stock]
SUPPLIER_LEAD_TIME_BREACH      supplier lead time > threshold / supplier on watch [SIM]
DELIVERY_DELAY_ANOMALY         seller/order delay anomalies (recent history)
SALES_DEMAND_ANOMALY           demand spikes, sales drops, rapid depletion, unusual orders

De-duplication: an alert's *fingerprint* is (alert_type, entity). While an
ACTIVE alert with the same fingerprint exists, repeats only bump
``occurrences``/``last_seen_at``; it is re-notified only if its severity
escalates or the cooldown (``ALERT_COOLDOWN_HOURS``) has expired. Conditions
that disappear are RESOLVED automatically.

Delivery modes (``ALERT_MODE``): ``local`` (log file + JSONL outbox, default,
no credentials needed), ``slack``, ``email`` or ``all``. Credentials only come
from environment variables. Delivery failures are logged, recorded in
``alert_history.channels`` and never crash the pipeline.
"""
from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage

import pandas as pd
import requests
from sqlalchemy.engine import Engine

from config.database import get_engine, raw_connection, read_sql
from config.settings import SEVERITY_ORDER, AlertSettings, get_settings
from src.utils.helpers import json_default, stable_hash, utc_now
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

ALERT_TITLES = {
    "CRITICAL_INVENTORY_RISK": "Critical Inventory Risk",
    "PROJECTED_BELOW_SAFETY_STOCK": "Projected Stock-out Within Lead Time",
    "STOCK_BELOW_REORDER_POINT": "Stock Below Reorder Point",
    "FORECAST_EXCEEDS_INVENTORY": "Forecast Demand Exceeds Inventory",
    "SUPPLIER_LEAD_TIME_BREACH": "Supplier Lead Time Breach",
    "DELIVERY_DELAY_ANOMALY": "Delivery Delay Anomaly",
    "SALES_DEMAND_ANOMALY": "Sales / Demand Anomaly",
}


@dataclass
class Alert:
    alert_type: str
    severity: str
    entity_type: str
    entity_id: str
    explanation: str
    recommended_action: str
    product_id: str | None = None
    metrics: dict = field(default_factory=dict)
    is_simulated_input: bool = False

    @property
    def fingerprint(self) -> str:
        return stable_hash(self.alert_type, self.entity_type, self.entity_id, length=20)

    @property
    def title(self) -> str:
        return ALERT_TITLES.get(self.alert_type, self.alert_type)


def severity_at_least(severity: str, minimum: str) -> bool:
    return SEVERITY_ORDER.get(severity, 0) >= SEVERITY_ORDER.get(minimum, 0)


# --------------------------------------------------------------------------- #
# Rule evaluation (pure functions on DataFrames)
# --------------------------------------------------------------------------- #
def inventory_alerts(inv: pd.DataFrame) -> list[Alert]:
    """inv: rows of analytics.vw_current_inventory. One (most severe) alert per SKU-location."""
    out = []
    for r in inv.itertuples(index=False):
        metrics = {"available_stock": int(r.available_stock), "reserved_stock": int(r.reserved_stock),
                   "net_available": int(r.net_available), "safety_stock": int(r.safety_stock),
                   "reorder_point": int(r.reorder_point), "on_order_qty": int(r.on_order_qty),
                   "forecast_demand_lead_time": float(r.forecast_demand_lead_time or 0),
                   "supplier_lead_time_days": float(r.lead_time_days), "recommended_reorder_qty": int(r.reorder_qty),
                   "warehouse_id": r.warehouse_id, "supplier_id": r.supplier_id}
        entity = f"{r.product_id}|{r.warehouse_id}"
        if r.risk_status == "Critical":
            out.append(Alert("CRITICAL_INVENTORY_RISK", "CRITICAL", "sku_location", entity,
                             f"Net available stock ({r.net_available}) is below safety stock ({r.safety_stock}); "
                             f"lead-time demand is {metrics['forecast_demand_lead_time']:.1f} units. "
                             "Risk: projected inventory shortage.", r.recommended_action, r.product_id, metrics, True))
        elif r.risk_status == "High Risk":
            out.append(Alert("PROJECTED_BELOW_SAFETY_STOCK", "HIGH", "sku_location", entity,
                             f"Stock covers {r.days_of_cover:.1f} days but the supplier lead time is "
                             f"{r.lead_time_days:.1f} days; inventory is projected to fall below safety stock "
                             "before replenishment can arrive.", r.recommended_action, r.product_id, metrics, True))
        elif r.risk_status == "Warning" and r.net_available <= r.reorder_point:
            out.append(Alert("STOCK_BELOW_REORDER_POINT", "MEDIUM", "sku_location", entity,
                             f"Net available stock ({r.net_available}) is at/below the reorder point "
                             f"({r.reorder_point}).", r.recommended_action, r.product_id, metrics, True))
    return out


def forecast_alerts(fvi: pd.DataFrame, on_order: pd.Series | None = None) -> list[Alert]:
    """fvi: analytics.vw_forecast_vs_inventory rows (only Prophet-based demand is used)."""
    out = []
    for r in fvi[fvi["demand_source"] == "prophet"].itertuples(index=False):
        incoming = float(on_order.get(r.product_id, 0)) if on_order is not None else 0.0
        gap = float(r.demand_next_30d) - (float(r.net_available) + incoming)
        if gap <= 0 or r.demand_next_30d < 1:
            continue
        sev = "HIGH" if gap >= 0.5 * float(r.demand_next_30d) else "MEDIUM"
        out.append(Alert("FORECAST_EXCEEDS_INVENTORY", sev, "sku_location", f"{r.product_id}|{r.warehouse_id}",
                         f"Prophet forecasts {r.demand_next_30d:.0f} units over the next 30 days but only "
                         f"{r.net_available} are available and {incoming:.0f} on order (gap {gap:.0f}).",
                         "Increase the replenishment quantity or expedite open purchase orders.", r.product_id,
                         {"demand_next_30d": float(r.demand_next_30d), "net_available": int(r.net_available),
                          "on_order": incoming, "gap_units": round(gap, 1)}, True))
    return out


def supplier_alerts(sup: pd.DataFrame, threshold_days: float) -> list[Alert]:
    """sup: analytics.vw_supplier_kpis joined with dim_supplier.supplier_status."""
    out = []
    for r in sup.itertuples(index=False):
        lt = r.avg_actual_lead_time_days
        breach = pd.notna(lt) and float(lt) > threshold_days
        watch = r.supplier_status in ("ON_WATCH", "SUSPENDED")
        if not (breach or watch):
            continue
        sev = "CRITICAL" if r.supplier_status == "SUSPENDED" else "HIGH"
        out.append(Alert("SUPPLIER_LEAD_TIME_BREACH", sev, "supplier", r.supplier_id,
                         f"{r.supplier_name}: average simulated lead time {lt if pd.notna(lt) else 'n/a'} days "
                         f"(threshold {threshold_days:g}), delay rate {r.delay_rate_pct}%, status {r.supplier_status}; "
                         f"{r.skus_at_replenishment_risk} SKUs at replenishment risk.",
                         "Escalate with the supplier, expedite open POs and qualify the secondary supplier.",
                         None, {"avg_lead_time_days": None if pd.isna(lt) else float(lt),
                                "delay_rate_pct": None if pd.isna(r.delay_rate_pct) else float(r.delay_rate_pct),
                                "open_purchase_orders": int(r.open_purchase_orders)}, True))
    return out


def anomaly_alerts(anomalies: pd.DataFrame, min_severity: str = "HIGH") -> list[Alert]:
    """anomalies: recent rows of analytics.anomaly_events."""
    out = []
    for r in anomalies.itertuples(index=False):
        if not severity_at_least(r.severity, min_severity):
            continue
        a_type = "DELIVERY_DELAY_ANOMALY" if r.anomaly_type.startswith("DELIVERY_DELAY") else (
            "SUPPLIER_LEAD_TIME_BREACH" if r.anomaly_type == "SUPPLIER_LEAD_TIME" else "SALES_DEMAND_ANOMALY")
        product = None
        if r.anomaly_type == "RAPID_DEPLETION":
            product = r.record_id.split("|")[0]
        out.append(Alert(a_type, r.severity, r.anomaly_type.lower(), r.record_id,
                         f"{r.anomaly_type} detected by {r.detection_method}: value {r.metric_value} vs expected "
                         f"{r.expected_value} (score {r.anomaly_score}) on {r.event_date}.",
                         r.recommended_action, product,
                         {"anomaly_id": r.anomaly_id, "score": float(r.anomaly_score)},
                         r.data_source == "SIMULATED"))
    return out


# --------------------------------------------------------------------------- #
# De-duplication
# --------------------------------------------------------------------------- #
def reconcile(candidates: list[Alert], active: pd.DataFrame, now, cooldown_hours: int,
              min_notify_severity: str) -> dict:
    """Decide what to insert, update, resolve and notify.

    ``active``: ACTIVE rows of alert_history (fingerprint, alert_id, severity, created_at, last_notified_at).
    """
    by_fp: dict[str, Alert] = {}
    for a in candidates:  # keep the most severe candidate per fingerprint
        if a.fingerprint not in by_fp or SEVERITY_ORDER[a.severity] > SEVERITY_ORDER[by_fp[a.fingerprint].severity]:
            by_fp[a.fingerprint] = a
    active = active.set_index("fingerprint") if not active.empty else active
    inserts, updates, notify = [], [], []
    for fp, a in by_fp.items():
        if not active.empty and fp in active.index:
            row = active.loc[fp]
            escalated = SEVERITY_ORDER[a.severity] > SEVERITY_ORDER.get(row["severity"], 0)
            last = row.get("last_notified_at")
            cooled = pd.isna(last) or (pd.Timestamp(now) - pd.Timestamp(last)) >= pd.Timedelta(hours=cooldown_hours)
            should = severity_at_least(a.severity, min_notify_severity) and (escalated or cooled)
            updates.append((row["alert_id"], a, should))
            if should:
                notify.append((row["alert_id"], a))
        else:
            alert_id = "ALT-" + stable_hash(fp, now, length=14)
            should = severity_at_least(a.severity, min_notify_severity)
            inserts.append((alert_id, a, should))
            if should:
                notify.append((alert_id, a))
    resolve = [] if active.empty else [active.loc[fp, "alert_id"] for fp in active.index if fp not in by_fp]
    return {"inserts": inserts, "updates": updates, "resolve": resolve, "notify": notify}


# --------------------------------------------------------------------------- #
# Formatting & delivery
# --------------------------------------------------------------------------- #
def format_alert(alert_id: str, a: Alert, ts) -> str:
    m = a.metrics
    lines = ["SUPPLY CHAIN ALERT", "", f"Alert ID: {alert_id}", f"Timestamp (UTC): {ts:%Y-%m-%d %H:%M}",
             f"Severity: {a.severity}", f"Alert Type: {a.title}"]
    if a.product_id:
        lines.append(f"Product: {a.product_id}")
    else:
        lines.append(f"Entity: {a.entity_type} {a.entity_id}")
    if "available_stock" in m:
        lines += [f"Available Stock: {m['available_stock']} units (net {m['net_available']})",
                  f"Forecast Demand (lead time): {m['forecast_demand_lead_time']:.0f} units",
                  f"Supplier Lead Time: {m['supplier_lead_time_days']:.1f} days"]
    if "demand_next_30d" in m:
        lines.append(f"Forecast Demand (30d): {m['demand_next_30d']:.0f} units")
    lines += [f"Explanation: {a.explanation}", f"Recommended Action: {a.recommended_action}"]
    if a.is_simulated_input:
        lines.append("Note: based on SIMULATED operational data (inventory/supplier).")
    return "\n".join(lines)


class NotificationService:
    def __init__(self, cfg: AlertSettings | None = None):
        self.cfg = cfg or get_settings().alerts
        self.paths = get_settings().paths

    @property
    def channels(self) -> list[str]:
        mode = (self.cfg.mode or "local").lower()
        return ["local", "slack", "email"] if mode == "all" else [mode]

    def send_local(self, subject: str, body: str, payload: dict | None = None) -> str:
        out_dir = self.paths.reports_dir / "alerts"
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "alerts_outbox.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": utc_now(), "subject": subject, "body": body, **(payload or {})},
                                default=json_default) + "\n")
        logger.warning("ALERT [local]\n%s", body)
        return "sent"

    def send_slack(self, body: str) -> str:
        if not self.cfg.slack_webhook_url:
            return "skipped: SLACK_WEBHOOK_URL not set"
        try:
            resp = requests.post(self.cfg.slack_webhook_url, json={"text": f"```{body}```"}, timeout=10)
            resp.raise_for_status()
            return "sent"
        except requests.RequestException as exc:
            logger.error("Slack delivery failed: %s", exc)
            return f"failed: {exc.__class__.__name__}"

    def send_email(self, subject: str, body: str) -> str:
        c = self.cfg
        if not (c.smtp_host and c.email_from and c.email_to):
            return "skipped: SMTP_HOST / ALERT_EMAIL_FROM / ALERT_EMAIL_TO not set"
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, c.email_from, c.email_to
        msg.set_content(body)
        try:
            with smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=20) as smtp:
                if c.smtp_use_tls:
                    smtp.starttls()
                if c.smtp_user and c.smtp_password:
                    smtp.login(c.smtp_user, c.smtp_password)
                smtp.send_message(msg)
            return "sent"
        except (smtplib.SMTPException, OSError) as exc:
            logger.error("Email delivery failed: %s", exc)
            return f"failed: {exc.__class__.__name__}"

    def dispatch(self, subject: str, body: str, payload: dict | None = None) -> dict:
        status = {}
        for ch in self.channels:
            if ch == "local":
                status["local"] = self.send_local(subject, body, payload)
            elif ch == "slack":
                status["slack"] = self.send_slack(body)
            elif ch == "email":
                status["email"] = self.send_email(subject, body)
        return status

    def notify(self, items: list[tuple[str, Alert]], ts) -> dict[str, dict]:
        """Send the top-N alerts individually and the rest as one digest."""
        ordered = sorted(items, key=lambda x: -SEVERITY_ORDER[x[1].severity])
        individual, rest = ordered[: self.cfg.max_individual_alerts], ordered[self.cfg.max_individual_alerts:]
        result = {}
        for alert_id, a in individual:
            result[alert_id] = self.dispatch(f"[{a.severity}] {a.title} - {a.product_id or a.entity_id}",
                                             format_alert(alert_id, a, ts), {"alert_id": alert_id})
        if rest:
            lines = [f"{len(rest)} additional alerts (see Superset 'Alerts & Scenarios' dashboard):"]
            lines += [f"- {aid} [{a.severity}] {a.title}: {a.product_id or a.entity_id}" for aid, a in rest[:50]]
            status = self.dispatch(f"Supply chain alert digest: {len(rest)} alerts", "\n".join(lines))
            for aid, _ in rest:
                result[aid] = {**status, "digest": True}
        return result


# --------------------------------------------------------------------------- #
# Database orchestration
# --------------------------------------------------------------------------- #
def collect_candidates(engine: Engine, lookback_days_hist: int = 30) -> list[Alert]:
    s = get_settings()
    inv = read_sql("SELECT * FROM analytics.vw_current_inventory", engine=engine)
    fvi = read_sql("SELECT * FROM analytics.vw_forecast_vs_inventory", engine=engine)
    on_order = inv.groupby("product_id")["on_order_qty"].sum() if not inv.empty else None
    sup = read_sql("SELECT * FROM analytics.vw_supplier_kpis", engine=engine)
    anomalies = read_sql("""
        SELECT a.* FROM analytics.anomaly_events a
        WHERE (a.data_source = 'OLIST_HISTORICAL'
               AND a.event_date > (SELECT max_date FROM analytics.vw_data_window) - :hist)
           OR (a.data_source = 'SIMULATED' AND a.detection_ts > (now() AT TIME ZONE 'utc') - INTERVAL '48 hours')
    """, {"hist": lookback_days_hist}, engine=engine)
    return (inventory_alerts(inv) + forecast_alerts(fvi, on_order)
            + supplier_alerts(sup, s.anomaly.supplier_lead_time_threshold_days)
            + anomaly_alerts(anomalies, "HIGH"))


def evaluate_and_record_alerts(engine: Engine | None = None, run_id: str | None = None, send: bool = True) -> dict:
    """Evaluate rules, reconcile with history, notify, and persist. Returns a summary."""
    engine = engine or get_engine()
    cfg = get_settings().alerts
    now = utc_now()
    candidates = collect_candidates(engine)
    active = read_sql("""SELECT alert_id, fingerprint, severity, created_at,
                                (channels ->> 'last_notified_at')::timestamp AS last_notified_at
                         FROM analytics.alert_history WHERE status = 'ACTIVE'""", engine=engine)
    plan = reconcile(candidates, active, now, cfg.cooldown_hours, cfg.min_severity_to_notify)
    pending_ids = {aid for aid, _ in plan["notify"]}
    delivery = NotificationService(cfg).notify(plan["notify"], now) if (send and plan["notify"]) else {}

    def channels_json(alert_id):
        st = delivery.get(alert_id)
        if st:
            return json.dumps({"last_notified_at": now.isoformat(), "pending": False, **st})
        if alert_id in pending_ids:  # queued for the send_notifications task
            return json.dumps({"pending": True})
        return None

    with raw_connection(engine) as conn, conn.cursor() as cur:
        for alert_id, a, _ in plan["inserts"]:
            cur.execute("""INSERT INTO analytics.alert_history (alert_id, fingerprint, alert_type, severity, entity_type,
                               entity_id, product_id, title, explanation, recommended_action, metrics, status, notified,
                               channels, occurrences, created_at, last_seen_at, run_id, is_simulated_input)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s,1,%s,%s,%s,%s)
                           ON CONFLICT (alert_id) DO NOTHING""",
                        (alert_id, a.fingerprint, a.alert_type, a.severity, a.entity_type, a.entity_id, a.product_id,
                         a.title, a.explanation, a.recommended_action, json.dumps(a.metrics, default=json_default),
                         alert_id in delivery, channels_json(alert_id), now, now, run_id, a.is_simulated_input))
        for alert_id, a, _ in plan["updates"]:
            ch = channels_json(alert_id)
            cur.execute("""UPDATE analytics.alert_history
                           SET severity = %s, explanation = %s, recommended_action = %s, metrics = %s,
                               occurrences = occurrences + 1, last_seen_at = %s, run_id = %s,
                               notified = notified OR %s, channels = COALESCE(%s::jsonb, channels)
                           WHERE alert_id = %s""",
                        (a.severity, a.explanation, a.recommended_action, json.dumps(a.metrics, default=json_default),
                         now, run_id, alert_id in delivery, ch, alert_id))
        if plan["resolve"]:
            cur.execute("UPDATE analytics.alert_history SET status = 'RESOLVED', resolved_at = %s "
                        "WHERE alert_id = ANY(%s)", (now, list(plan["resolve"])))
    summary = {"candidates": len(candidates), "new": len(plan["inserts"]), "updated": len(plan["updates"]),
               "resolved": len(plan["resolve"]), "notified": len(delivery), "mode": cfg.mode,
               "by_severity": pd.Series([a.severity for _, a, _ in plan["inserts"] + plan["updates"]])
               .value_counts().to_dict()}
    logger.info("Alert evaluation: %s", summary)
    return summary


def _alert_from_row(r) -> Alert:
    metrics = r.metrics if isinstance(r.metrics, dict) else json.loads(r.metrics or "{}")
    return Alert(r.alert_type, r.severity, r.entity_type, r.entity_id, r.explanation, r.recommended_action,
                 r.product_id, metrics, bool(r.is_simulated_input))


def send_pending_notifications(engine: Engine | None = None) -> dict:
    """Deliver alerts queued by ``evaluate_and_record_alerts(send=False)`` (Airflow 'send_notifications' task)."""
    engine = engine or get_engine()
    now = utc_now()
    pending = read_sql("""SELECT * FROM analytics.alert_history
                          WHERE status = 'ACTIVE' AND (channels ->> 'pending')::boolean IS TRUE""", engine=engine)
    if pending.empty:
        logger.info("No pending notifications")
        return {"notified": 0}
    items = [(r.alert_id, _alert_from_row(r)) for r in pending.itertuples(index=False)]
    delivery = NotificationService().notify(items, now)
    with raw_connection(engine) as conn, conn.cursor() as cur:
        for alert_id, status in delivery.items():
            cur.execute("UPDATE analytics.alert_history SET notified = TRUE, channels = %s WHERE alert_id = %s",
                        (json.dumps({"last_notified_at": now.isoformat(), "pending": False, **status}), alert_id))
    failed = sum(1 for st in delivery.values() if any(str(v).startswith("failed") for v in st.values()))
    logger.info("Notifications delivered: %d (channel failures: %d)", len(delivery), failed)
    return {"notified": len(delivery), "channel_failures": failed}


def send_failure_notification(context: dict) -> None:
    """Airflow ``on_failure_callback``: notify about a failed task."""
    ti = context.get("task_instance")
    body = (f"PIPELINE FAILURE\n\nDAG: {getattr(ti, 'dag_id', '?')}\nTask: {getattr(ti, 'task_id', '?')}\n"
            f"Run: {context.get('run_id')}\nTry: {getattr(ti, 'try_number', '?')}\n"
            f"Error: {context.get('exception')}\nLog: {getattr(ti, 'log_url', '')}")
    try:
        NotificationService().dispatch("[CRITICAL] Retail supply chain ETL failure", body)
    except Exception as exc:  # pragma: no cover - never raise from a callback
        logger.error("Failure notification could not be sent: %s", exc)


if __name__ == "__main__":
    print(evaluate_and_record_alerts())
