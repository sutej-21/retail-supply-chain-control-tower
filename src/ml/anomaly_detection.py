"""Anomaly detection with rolling Z-scores and Isolation Forest.

Detectors
---------
=========================  ==================  ================  ================
anomaly_type               method              data              grain
=========================  ==================  ================  ================
DEMAND_SPIKE               rolling z-score     Olist history     category x day
SALES_DROP                 rolling z-score     Olist history     day
DELIVERY_DELAY_SELLER      robust z (MAD)      Olist history     seller x week
DELIVERY_DELAY_ORDER       Isolation Forest    Olist history     order
UNUSUAL_ORDER_QUANTITY     IForest + z-score   Olist history     order
SUPPLIER_LEAD_TIME         z-score/threshold   SIMULATED         purchase order
RAPID_DEPLETION            Poisson z + ratio   SIMULATED         SKU x 24h
=========================  ==================  ================  ================

Every rolling statistic uses only PAST observations (window shifted by one),
so a day is never compared with itself or with the future.

False positives: a statistically unusual value is not automatically a business
problem (e.g. Black Friday spikes, the incomplete Olist tail). Thresholds,
minimum volumes and severity cut-offs are configurable (``ANOMALY_*``
environment variables); only HIGH/CRITICAL anomalies generate alerts by default.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sqlalchemy.engine import Engine

from config.database import get_engine, read_sql, upsert_dataframe
from config.settings import AnomalySettings, get_settings
from src.utils.helpers import effective_data_end, json_default, stable_hash, utc_now
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

OUTPUT_COLUMNS = ["anomaly_id", "record_id", "anomaly_type", "detection_method", "anomaly_score", "metric_value",
                  "expected_value", "event_date", "detection_ts", "severity", "recommended_action", "data_source",
                  "details"]

RECOMMENDED_ACTIONS = {
    "DEMAND_SPIKE": "Verify the demand driver (promotion, marketplace campaign, data error) and check stock cover "
                    "for the category's top SKUs.",
    "SALES_DROP": "Check platform availability, payment gateway and catalogue issues for the day; confirm data "
                  "completeness before escalating.",
    "DELIVERY_DELAY_SELLER": "Contact the seller and carrier; review handling time and consider restricting "
                             "the seller's delivery promise.",
    "DELIVERY_DELAY_ORDER": "Investigate the shipment with the carrier and proactively inform the customer.",
    "UNUSUAL_ORDER_QUANTITY": "Validate the order (possible B2B/reseller purchase or fraud) before fulfilment.",
    "SUPPLIER_LEAD_TIME": "Escalate with the supplier, expedite open POs and evaluate the secondary supplier.",
    "RAPID_DEPLETION": "Review the demand surge, re-run replenishment and expedite inbound stock if cover is short.",
}


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #
def rolling_zscore(series: pd.Series, window: int = 28, min_periods: int = 14) -> tuple[pd.Series, pd.Series]:
    """Z-score of each point versus the PREVIOUS ``window`` points. Returns (z, expected mean)."""
    past = series.shift(1)
    mean = past.rolling(window, min_periods=min_periods).mean()
    std = past.rolling(window, min_periods=min_periods).std()
    z = (series - mean) / std.replace(0, np.nan)
    return z, mean


def robust_zscore(values: pd.Series) -> pd.Series:
    """(x - median) / (1.4826 * MAD); robust to the outliers we are trying to find."""
    med = values.median()
    mad = (values - med).abs().median() * 1.4826
    if not mad or np.isnan(mad):
        std = values.std()
        mad = std if std and not np.isnan(std) else 1.0
    return (values - med) / mad


def severity_from_z(abs_z, cfg: AnomalySettings) -> np.ndarray:
    a = np.abs(np.asarray(abs_z, dtype=float))
    return np.select([a >= cfg.severity_critical_z, a >= cfg.severity_high_z, a >= cfg.zscore_threshold],
                     ["CRITICAL", "HIGH", "MEDIUM"], default="LOW")


def isolation_forest_scores(features: pd.DataFrame, contamination: float = 0.01,
                            random_state: int = 42) -> tuple[np.ndarray, np.ndarray]:
    """Return (anomaly_score, is_anomaly). Higher score = more anomalous."""
    x = features.astype(float).fillna(features.median(numeric_only=True)).values
    if len(x) < 50:
        return np.zeros(len(x)), np.zeros(len(x), dtype=bool)
    model = IsolationForest(n_estimators=200, contamination=contamination, random_state=random_state)
    model.fit(x)
    return -model.decision_function(x), model.predict(x) == -1


def _frame(records: pd.DataFrame, anomaly_type: str, method: str, data_source: str) -> pd.DataFrame:
    if records.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    out = records.copy()
    out["anomaly_type"] = anomaly_type
    out["detection_method"] = method
    out["data_source"] = data_source
    out["detection_ts"] = utc_now()
    out["anomaly_id"] = [stable_hash(anomaly_type, r, length=24) for r in out["record_id"]]
    out["recommended_action"] = RECOMMENDED_ACTIONS[anomaly_type]
    if "details" not in out:
        out["details"] = None
    out["details"] = out["details"].map(lambda d: json.dumps(d, default=json_default) if isinstance(d, dict) else d)
    for c in ("anomaly_score", "metric_value", "expected_value"):
        out[c] = pd.to_numeric(out[c], errors="coerce").round(4)
    return out[OUTPUT_COLUMNS]


# --------------------------------------------------------------------------- #
# Detectors (pure functions on DataFrames)
# --------------------------------------------------------------------------- #
def detect_demand_spikes(daily: pd.DataFrame, cfg: AnomalySettings) -> pd.DataFrame:
    """daily: [date, category, units] (continuous dates per category, zeros filled)."""
    rows = []
    for cat, g in daily.sort_values("date").groupby("category"):
        s = g.set_index("date")["units"].astype(float)
        z, mean = rolling_zscore(s, cfg.zscore_window, cfg.zscore_min_periods)
        hit = (z >= cfg.zscore_threshold) & (s >= cfg.min_daily_volume)
        for d in s.index[hit.fillna(False).values]:
            rows.append({"record_id": f"{cat}|{d:%Y-%m-%d}", "anomaly_score": z[d], "metric_value": s[d],
                         "expected_value": mean[d], "event_date": d.date(),
                         "details": {"category": cat, "window_days": cfg.zscore_window}})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["severity"] = severity_from_z(df["anomaly_score"], cfg)
    return _frame(df, "DEMAND_SPIKE", "zscore", "OLIST_HISTORICAL")


def detect_sales_drops(daily_sales: pd.DataFrame, cfg: AnomalySettings) -> pd.DataFrame:
    """daily_sales: [date, revenue, orders]. Flags a day when revenue OR order count drops below
    -threshold z versus the previous window (order counts are less noisy than revenue at low volume).
    Days after the incomplete data tail are ignored."""
    d = daily_sales.set_index("date").sort_index()
    end = effective_data_end(d["orders"])
    d = d[d.index <= end]
    z_rev, mean_rev = rolling_zscore(d["revenue"].astype(float), cfg.zscore_window, cfg.zscore_min_periods)
    z_ord, _ = rolling_zscore(d["orders"].astype(float), cfg.zscore_window, cfg.zscore_min_periods)
    score = pd.concat([-z_rev, -z_ord], axis=1).max(axis=1)
    hit = (score >= cfg.zscore_threshold).fillna(False)
    idx = d.index[hit.values]
    df = pd.DataFrame({"record_id": [f"ALL|{x:%Y-%m-%d}" for x in idx], "anomaly_score": score[hit].values,
                       "metric_value": d.loc[idx, "revenue"].values, "expected_value": mean_rev[hit].values,
                       "event_date": [x.date() for x in idx]})
    if not df.empty:
        df["severity"] = severity_from_z(df["anomaly_score"], cfg)
        df["details"] = [{"effective_data_end": str(end.date()), "orders": int(d.loc[x, "orders"]),
                          "revenue_drop_pct": round(100 * (1 - m / e), 1) if e else None}
                         for x, m, e in zip(idx, df["metric_value"], df["expected_value"])]
    return _frame(df, "SALES_DROP", "zscore", "OLIST_HISTORICAL")


def detect_seller_delay_anomalies(shipments: pd.DataFrame, cfg: AnomalySettings, min_deliveries: int = 5) -> pd.DataFrame:
    """shipments: delivered outbound shipments [seller_id, delivered_date, delay_days].

    Seller-week mean delay vs. the robust distribution of all seller-weeks.
    """
    d = shipments.dropna(subset=["delay_days"]).copy()
    if d.empty:
        return _frame(pd.DataFrame(), "DELIVERY_DELAY_SELLER", "robust_zscore", "OLIST_HISTORICAL")
    d["week"] = pd.to_datetime(d["delivered_date"]).dt.to_period("W-SUN").dt.start_time
    wk = d.groupby(["seller_id", "week"]).agg(n=("delay_days", "size"), mean_delay=("delay_days", "mean"),
                                              late_share=("delay_days", lambda x: (x > 0).mean())).reset_index()
    wk = wk[wk["n"] >= min_deliveries]
    if wk.empty:
        return _frame(pd.DataFrame(), "DELIVERY_DELAY_SELLER", "robust_zscore", "OLIST_HISTORICAL")
    wk["z"] = robust_zscore(wk["mean_delay"])
    hit = wk[(wk["z"] >= cfg.zscore_threshold) & (wk["mean_delay"] > 0)]
    df = pd.DataFrame({
        "record_id": hit["seller_id"] + "|" + hit["week"].dt.strftime("%Y-%m-%d"),
        "anomaly_score": hit["z"], "metric_value": hit["mean_delay"],
        "expected_value": wk["mean_delay"].median(), "event_date": hit["week"].dt.date,
        "details": [{"seller_id": s, "deliveries": int(n), "late_share": round(float(l), 3)}
                    for s, n, l in zip(hit["seller_id"], hit["n"], hit["late_share"])],
    })
    if not df.empty:
        df["severity"] = severity_from_z(df["anomaly_score"], cfg)
    return _frame(df, "DELIVERY_DELAY_SELLER", "robust_zscore", "OLIST_HISTORICAL")


def detect_order_delay_anomalies(orders: pd.DataFrame, cfg: AnomalySettings, max_records: int = 500) -> pd.DataFrame:
    """Isolation Forest on delivered orders; only LATE outliers are kept (fast deliveries are not problems)."""
    d = orders.dropna(subset=["delivery_days", "delay_days"]).reset_index(drop=True)
    if d.empty:
        return _frame(pd.DataFrame(), "DELIVERY_DELAY_ORDER", "isolation_forest", "OLIST_HISTORICAL")
    score, is_anom = isolation_forest_scores(d[["delivery_days", "delay_days", "freight_value", "item_count"]],
                                             cfg.iforest_contamination)
    d["score"] = score
    hit = d[is_anom & (d["delay_days"] > 0)].nlargest(max_records, "score")
    df = pd.DataFrame({"record_id": hit["order_id"], "anomaly_score": hit["score"], "metric_value": hit["delay_days"],
                       "expected_value": float(d["delay_days"].median()),
                       "event_date": pd.to_datetime(hit["purchase_date"]).dt.date,
                       "severity": np.select([hit["delay_days"] >= 30, hit["delay_days"] >= 14], ["HIGH", "MEDIUM"],
                                             default="LOW"),
                       "details": [{"delivery_days": float(a), "customer_state": s}
                                   for a, s in zip(hit["delivery_days"], hit["customer_state"])]})
    return _frame(df, "DELIVERY_DELAY_ORDER", "isolation_forest", "OLIST_HISTORICAL")


def detect_unusual_order_quantities(orders: pd.DataFrame, cfg: AnomalySettings) -> pd.DataFrame:
    """Isolation Forest on [item_count, distinct_products, items_value] AND item_count z >= threshold."""
    d = orders[orders["item_count"] > 0].reset_index(drop=True)
    if d.empty:
        return _frame(pd.DataFrame(), "UNUSUAL_ORDER_QUANTITY", "isolation_forest", "OLIST_HISTORICAL")
    score, is_anom = isolation_forest_scores(d[["item_count", "distinct_products", "items_value"]],
                                             cfg.iforest_contamination)
    z = (d["item_count"] - d["item_count"].mean()) / (d["item_count"].std() or 1.0)
    hit = is_anom & (z >= max(cfg.zscore_threshold, 5.0))   # quantity must be extreme, not just unusual value
    h = d[hit]
    df = pd.DataFrame({"record_id": h["order_id"], "anomaly_score": z[hit], "metric_value": h["item_count"],
                       "expected_value": float(d["item_count"].mean()),
                       "event_date": pd.to_datetime(h["purchase_date"]).dt.date,
                       "details": [{"iforest_score": round(float(s), 4), "items_value": float(v)}
                                   for s, v in zip(score[hit], h["items_value"])]})
    if not df.empty:
        # severity relative to the 99th percentile basket size (z alone overstates severity for skewed counts)
        p99 = max(float(d["item_count"].quantile(0.99)), 1.0)
        ratio = df["metric_value"].astype(float) / p99
        df["severity"] = np.select([ratio >= 4, ratio >= 2.5], ["CRITICAL", "HIGH"], default="MEDIUM")
    return _frame(df, "UNUSUAL_ORDER_QUANTITY", "isolation_forest", "OLIST_HISTORICAL")


def detect_supplier_lead_time_anomalies(pos: pd.DataFrame, cfg: AnomalySettings) -> pd.DataFrame:
    """SIMULATED received POs: z vs the supplier's planned lead time, or above the absolute threshold."""
    r = pos[pos["status"] == "RECEIVED"].copy()
    if r.empty:
        return _frame(pd.DataFrame(), "SUPPLIER_LEAD_TIME", "zscore", "SIMULATED")
    r["z"] = (r["simulated_lead_time_days"].astype(float) - r["average_lead_time_days"].astype(float)) \
        / r["lead_time_std_days"].astype(float).replace(0, np.nan)
    hit = r[(r["z"] >= cfg.zscore_threshold)
            | (r["simulated_lead_time_days"].astype(float) > cfg.supplier_lead_time_threshold_days)]
    df = pd.DataFrame({"record_id": hit["po_id"], "anomaly_score": hit["z"].fillna(cfg.zscore_threshold),
                       "metric_value": hit["simulated_lead_time_days"], "expected_value": hit["average_lead_time_days"],
                       "event_date": pd.to_datetime(hit["actual_arrival_ts"]).dt.date,
                       "details": [{"supplier_id": s, "product_id": p} for s, p in zip(hit["supplier_id"], hit["product_id"])]})
    if not df.empty:
        df["severity"] = severity_from_z(df["anomaly_score"], cfg)
    return _frame(df, "SUPPLIER_LEAD_TIME", "zscore", "SIMULATED")


def detect_rapid_depletion(consumption_24h: pd.DataFrame, cfg: AnomalySettings, min_units: int = 3) -> pd.DataFrame:
    """SIMULATED consumption: [product_id, warehouse_id, units_24h, expected_daily, window_end, net_available].

    Poisson z = (x - lambda) / sqrt(lambda); flagged when z >= threshold AND x / lambda >= ratio threshold.
    """
    c = consumption_24h.copy()
    lam = c["expected_daily"].astype(float).clip(lower=0.05)
    c["z"] = (c["units_24h"] - lam) / np.sqrt(lam)
    c["ratio"] = c["units_24h"] / lam
    hit = c[(c["z"] >= cfg.zscore_threshold) & (c["ratio"] >= cfg.depletion_ratio_threshold)
            & (c["units_24h"] >= min_units)]
    df = pd.DataFrame({"record_id": hit["product_id"] + "|" + hit["warehouse_id"] + "|"
                                    + pd.to_datetime(hit["window_end"]).dt.strftime("%Y-%m-%dT%H"),
                       "anomaly_score": hit["z"], "metric_value": hit["units_24h"], "expected_value": lam[hit.index],
                       "event_date": pd.to_datetime(hit["window_end"]).dt.date,
                       "details": [{"product_id": p, "warehouse_id": w, "ratio": round(float(r), 2),
                                    "net_available": int(n)}
                                   for p, w, r, n in zip(hit["product_id"], hit["warehouse_id"], hit["ratio"],
                                                         hit["net_available"])]})
    if not df.empty:
        df["severity"] = severity_from_z(df["anomaly_score"], cfg)
    return _frame(df, "RAPID_DEPLETION", "poisson_zscore", "SIMULATED")


# --------------------------------------------------------------------------- #
# Database orchestration
# --------------------------------------------------------------------------- #
def _load_inputs(engine: Engine) -> dict[str, pd.DataFrame]:
    q = {
        "category_daily": """
            SELECT d.full_date AS date, c.category, COALESCE(x.units, 0) AS units
            FROM analytics.dim_date d
            JOIN analytics.vw_data_window w ON d.full_date BETWEEN w.min_date AND w.max_date
            CROSS JOIN (SELECT DISTINCT category_name_en AS category FROM analytics.dim_product) c
            LEFT JOIN (SELECT oi.purchase_date, p.category_name_en, COUNT(*) AS units
                       FROM analytics.fact_order_items oi JOIN analytics.dim_product p USING (product_id)
                       WHERE oi.is_revenue_order GROUP BY 1, 2) x
              ON x.purchase_date = d.full_date AND x.category_name_en = c.category""",
        "daily_sales": "SELECT sales_date AS date, revenue, orders FROM analytics.vw_daily_sales",
        "shipments": """SELECT seller_id, delivered_ts::DATE AS delivered_date, delay_days
                        FROM analytics.fact_shipments
                        WHERE shipment_type = 'OUTBOUND_CUSTOMER' AND shipment_status = 'DELIVERED'""",
        "orders": """SELECT order_id, purchase_date, customer_state, delivery_days, delay_days, freight_value,
                            item_count, distinct_products, items_value
                     FROM analytics.fact_orders""",
        "pos": """SELECT f.*, s.average_lead_time_days, s.lead_time_std_days
                  FROM analytics.fact_supplier_performance f JOIN analytics.dim_supplier s USING (supplier_id)""",
        "consumption": """
            WITH last AS (SELECT MAX(movement_timestamp) AS t FROM analytics.fact_inventory_movements)
            SELECT t.product_id, t.warehouse_id, COALESCE(-SUM(m.quantity), 0) AS units_24h,
                   t.avg_daily_demand AS expected_daily, (SELECT t FROM last) AS window_end, t.net_available
            FROM analytics.inventory_thresholds t
            LEFT JOIN analytics.fact_inventory_movements m
              ON m.product_id = t.product_id AND m.warehouse_id = t.warehouse_id
             AND m.movement_type = 'SALE_CONSUMPTION'
             AND m.movement_timestamp > (SELECT t FROM last) - INTERVAL '24 hours'
            GROUP BY t.product_id, t.warehouse_id, t.avg_daily_demand, t.net_available""",
    }
    return {k: read_sql(v, engine=engine) for k, v in q.items()}


def detect_all(inputs: dict[str, pd.DataFrame], cfg: AnomalySettings | None = None) -> pd.DataFrame:
    cfg = cfg or get_settings().anomaly
    parts = [
        detect_demand_spikes(inputs["category_daily"].assign(date=lambda x: pd.to_datetime(x["date"])), cfg),
        detect_sales_drops(inputs["daily_sales"].assign(date=lambda x: pd.to_datetime(x["date"])), cfg),
        detect_seller_delay_anomalies(inputs["shipments"], cfg),
        detect_order_delay_anomalies(inputs["orders"], cfg),
        detect_unusual_order_quantities(inputs["orders"], cfg),
    ]
    if not inputs.get("pos", pd.DataFrame()).empty:
        parts.append(detect_supplier_lead_time_anomalies(inputs["pos"], cfg))
    if not inputs.get("consumption", pd.DataFrame()).empty:
        parts.append(detect_rapid_depletion(inputs["consumption"], cfg))
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=OUTPUT_COLUMNS)


def run_anomaly_detection(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_engine()
    anomalies = detect_all(_load_inputs(engine))
    if not anomalies.empty:
        upsert_dataframe(anomalies, "analytics.anomaly_events", ["anomaly_id"],
                         update_columns=["anomaly_score", "metric_value", "expected_value", "severity", "details"],
                         engine=engine)
    summary = anomalies.groupby(["anomaly_type", "severity"]).size() if not anomalies.empty else {}
    logger.info("Anomalies detected:\n%s", summary)
    return anomalies


if __name__ == "__main__":
    res = run_anomaly_detection()
    print(res.groupby(["anomaly_type", "severity"]).size())
