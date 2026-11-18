"""Alert rules, de-duplication, formatting, delivery channels and history logging."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
import pytest
import requests

from config.settings import AlertSettings
from src.alerts import notification_service as ns


def _inv_row(**kw):
    base = dict(product_id="P1", warehouse_id="WH-SP", supplier_id="SUP-001", available_stock=25, reserved_stock=0,
                net_available=25, safety_stock=30, reorder_point=80, on_order_qty=0, reorder_qty=90,
                forecast_demand_lead_time=80.0, lead_time_days=7.0, days_of_cover=2.2, risk_status="Critical",
                recommended_action="Initiate replenishment immediately.")
    base.update(kw)
    return base


def test_inventory_alert_rules():
    inv = pd.DataFrame([_inv_row(), _inv_row(product_id="P2", risk_status="High Risk", net_available=40,
                                             safety_stock=10), _inv_row(product_id="P3", risk_status="Warning",
                                                                       net_available=70, safety_stock=10),
                        _inv_row(product_id="P4", risk_status="Healthy", net_available=500)])
    alerts = ns.inventory_alerts(inv)
    assert [(a.product_id, a.alert_type, a.severity) for a in alerts] == [
        ("P1", "CRITICAL_INVENTORY_RISK", "CRITICAL"), ("P2", "PROJECTED_BELOW_SAFETY_STOCK", "HIGH"),
        ("P3", "STOCK_BELOW_REORDER_POINT", "MEDIUM")]
    assert all(a.is_simulated_input for a in alerts)


def test_forecast_and_supplier_rules():
    fvi = pd.DataFrame({"product_id": ["A", "B", "C"], "warehouse_id": "W", "net_available": [10, 100, 5],
                        "demand_next_30d": [80.0, 50.0, 30.0], "demand_source": ["prophet", "prophet", "historical_avg"]})
    fa = ns.forecast_alerts(fvi)
    assert [a.product_id for a in fa] == ["A"] and fa[0].severity == "HIGH"
    sup = pd.DataFrame({"supplier_id": ["S1", "S2"], "supplier_name": ["n1", "n2"], "avg_actual_lead_time_days": [20.0, 5.0],
                        "delay_rate_pct": [40.0, 0.0], "supplier_status": ["ON_WATCH", "ACTIVE"],
                        "skus_at_replenishment_risk": [3, 0], "open_purchase_orders": [2, 1]})
    sa = ns.supplier_alerts(sup, threshold_days=14)
    assert [a.entity_id for a in sa] == ["S1"]


def test_format_contains_required_fields():
    a = ns.inventory_alerts(pd.DataFrame([_inv_row()]))[0]
    text = ns.format_alert("ALT-1", a, datetime(2026, 1, 1, 10))
    for token in ("SUPPLY CHAIN ALERT", "Alert ID: ALT-1", "Timestamp", "Severity: CRITICAL", "Product: P1",
                  "Available Stock: 25 units", "Forecast Demand", "Supplier Lead Time: 7.0 days",
                  "Explanation:", "Recommended Action:", "SIMULATED"):
        assert token in text, token


def test_reconcile_deduplicates_and_respects_cooldown():
    now = datetime(2026, 1, 1, 12)
    a = ns.inventory_alerts(pd.DataFrame([_inv_row()]))[0]
    first = ns.reconcile([a, a], pd.DataFrame(), now, 24, "HIGH")
    assert len(first["inserts"]) == 1 and len(first["notify"]) == 1
    active = pd.DataFrame([{"alert_id": "ALT-x", "fingerprint": a.fingerprint, "severity": "CRITICAL",
                            "created_at": now, "last_notified_at": now - timedelta(hours=1)}])
    second = ns.reconcile([a], active, now, 24, "HIGH")
    assert not second["inserts"] and len(second["updates"]) == 1 and not second["notify"]
    active.loc[0, "last_notified_at"] = now - timedelta(hours=30)
    assert len(ns.reconcile([a], active, now, 24, "HIGH")["notify"]) == 1   # cooldown expired
    resolved = ns.reconcile([], active, now, 24, "HIGH")
    assert resolved["resolve"] == ["ALT-x"]


def test_escalation_triggers_notification():
    now = datetime(2026, 1, 1, 12)
    a = ns.inventory_alerts(pd.DataFrame([_inv_row()]))[0]
    active = pd.DataFrame([{"alert_id": "ALT-x", "fingerprint": a.fingerprint, "severity": "HIGH", "created_at": now,
                            "last_notified_at": now}])
    assert len(ns.reconcile([a], active, now, 24, "HIGH")["notify"]) == 1


def test_local_mode_writes_outbox(tmp_root):
    svc = ns.NotificationService(AlertSettings(mode="local"))
    a = ns.inventory_alerts(pd.DataFrame([_inv_row()]))[0]
    res = svc.notify([("ALT-L", a)], datetime(2026, 1, 1))
    assert res["ALT-L"]["local"] == "sent"
    lines = (tmp_root / "reports" / "alerts" / "alerts_outbox.jsonl").read_text().strip().splitlines()
    assert json.loads(lines[-1])["alert_id"] == "ALT-L"


def test_slack_failure_is_handled(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(ns.requests, "post", boom)
    svc = ns.NotificationService(AlertSettings(mode="slack", slack_webhook_url="https://hooks.slack.invalid/x"))
    assert svc.send_slack("hi").startswith("failed")


def test_slack_and_email_skip_without_credentials():
    svc = ns.NotificationService(AlertSettings(mode="all", slack_webhook_url=None, smtp_host=None))
    status = svc.dispatch("s", "b")
    assert status["local"] == "sent" and status["slack"].startswith("skipped") and status["email"].startswith("skipped")


def test_slack_success(monkeypatch):
    calls = {}

    class Resp:
        def raise_for_status(self):
            return None

    def fake_post(url, json=None, timeout=None):
        calls["url"], calls["json"] = url, json
        return Resp()
    monkeypatch.setattr(ns.requests, "post", fake_post)
    svc = ns.NotificationService(AlertSettings(mode="slack", slack_webhook_url="https://hooks.slack.test/x"))
    assert svc.send_slack("SUPPLY CHAIN ALERT") == "sent" and "SUPPLY CHAIN ALERT" in calls["json"]["text"]


def test_digest_for_many_alerts():
    svc = ns.NotificationService(AlertSettings(mode="local", max_individual_alerts=2))
    alerts = ns.inventory_alerts(pd.DataFrame([_inv_row(product_id=f"P{i}") for i in range(5)]))
    res = svc.notify([(f"A{i}", a) for i, a in enumerate(alerts)], datetime(2026, 1, 1))
    assert sum(1 for v in res.values() if v.get("digest")) == 3


def test_failure_callback_never_raises():
    class TI:
        dag_id, task_id, try_number, log_url = "d", "t", 1, ""
    ns.send_failure_notification({"task_instance": TI(), "run_id": "r", "exception": RuntimeError("x")})


# ----------------------------- integration ---------------------------------
@pytest.mark.integration
def test_alerts_are_generated_recorded_and_not_duplicated(simulated_db):
    from config.database import read_sql
    from src.ml.anomaly_detection import run_anomaly_detection
    run_anomaly_detection(simulated_db)
    s1 = ns.evaluate_and_record_alerts(simulated_db, run_id="t1", send=False)
    assert s1["new"] > 0
    pending = ns.send_pending_notifications(simulated_db)
    assert pending["notified"] > 0
    n1 = read_sql("SELECT COUNT(*) FROM analytics.alert_history", engine=simulated_db).iloc[0, 0]
    s2 = ns.evaluate_and_record_alerts(simulated_db, run_id="t2", send=False)
    n2 = read_sql("SELECT COUNT(*) FROM analytics.alert_history", engine=simulated_db).iloc[0, 0]
    assert s2["new"] == 0 and n2 == n1, "repeated evaluation must not duplicate alerts"
    assert ns.send_pending_notifications(simulated_db)["notified"] == 0, "no re-notification within cooldown"
    occ = read_sql("SELECT MAX(occurrences) FROM analytics.alert_history", engine=simulated_db).iloc[0, 0]
    assert occ == 2
