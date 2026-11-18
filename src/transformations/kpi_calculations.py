"""KPI calculations.

Two complementary implementations:

1. **Pure pandas functions** (``*_kpis``) - documented formulas that work on
   DataFrames; used by unit tests and notebooks and to cross-check the SQL.
2. **SQL views** (sql/06_create_analytics_views.sql) - used by Superset.
   ``fetch_kpi_snapshot`` reads them and stores a JSON snapshot per run.

Integration tests assert that both implementations return the same numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from config.database import get_engine, read_sql
from config.settings import get_settings
from src.utils.helpers import safe_divide, utc_now, write_json
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

NON_REVENUE_STATUSES = {"canceled", "unavailable"}


# ------------------------------ SALES --------------------------------------
def sales_kpis(orders: pd.DataFrame) -> dict:
    """orders: order grain with order_status, items_value, item_count.

    Revenue = sum(items_value) for orders whose status is not canceled/unavailable.
    AOV = revenue / number of revenue orders.
    """
    rev = orders[~orders["order_status"].isin(NON_REVENUE_STATUSES)]
    revenue = float(rev["items_value"].sum())
    n = int(len(rev))
    return {"total_revenue": round(revenue, 2), "total_orders": n,
            "avg_order_value": round(safe_divide(revenue, n), 2), "units_sold": int(rev["item_count"].sum())}


def daily_sales(orders: pd.DataFrame) -> pd.DataFrame:
    rev = orders[~orders["order_status"].isin(NON_REVENUE_STATUSES)].copy()
    rev["sales_date"] = pd.to_datetime(rev["purchase_ts"]).dt.normalize()
    out = rev.groupby("sales_date").agg(orders=("order_id", "nunique"), revenue=("items_value", "sum"),
                                        units=("item_count", "sum"))
    full = pd.date_range(out.index.min(), out.index.max(), freq="D")
    out = out.reindex(full, fill_value=0).rename_axis("sales_date").reset_index()
    out["avg_order_value"] = np.where(out["orders"] > 0, out["revenue"] / out["orders"].replace(0, np.nan), np.nan)
    return out


def demand_growth(items: pd.DataFrame, end_date, window_days: int = 30) -> pd.DataFrame:
    """Units in the last ``window`` days vs the previous window, per product."""
    end = pd.Timestamp(end_date)
    d = pd.to_datetime(items["purchase_date"])
    last = items[(d > end - pd.Timedelta(days=window_days)) & (d <= end)].groupby("product_id").size()
    prev = items[(d <= end - pd.Timedelta(days=window_days)) & (d > end - pd.Timedelta(days=2 * window_days))] \
        .groupby("product_id").size()
    out = pd.concat([last.rename("units_last"), prev.rename("units_prev")], axis=1).fillna(0).astype(int)
    out["growth_pct"] = np.where(out["units_prev"] > 0, 100 * (out["units_last"] - out["units_prev"]) / out["units_prev"].replace(0, np.nan), np.nan)
    return out.reset_index().rename(columns={"index": "product_id"})


# ------------------------------ DELIVERY -----------------------------------
def delivery_kpis(orders: pd.DataFrame) -> dict:
    """orders need: is_delivered, is_late, delivery_days, delay_days.

    On-time rate = delivered orders with delivered_date <= estimated_date / delivered orders with an estimate.
    """
    delivered = orders[orders["is_delivered"].astype(bool) & orders["is_late"].notna()]
    late = delivered["is_late"].astype(bool)
    n = len(delivered)
    return {
        "delivered_orders": int(n),
        "late_orders": int(late.sum()),
        "on_time_delivery_rate_pct": round(100 * safe_divide((~late).sum(), n), 2),
        "late_delivery_rate_pct": round(100 * safe_divide(late.sum(), n), 2),
        "avg_delivery_days": round(float(orders.loc[orders["is_delivered"].astype(bool), "delivery_days"].mean()), 2),
        "avg_delay_days_late_orders": round(float(delivered.loc[late, "delay_days"].mean()), 2) if late.any() else 0.0,
    }


# ------------------------------ CUSTOMER -----------------------------------
def repeat_customer_rate(orders: pd.DataFrame) -> float:
    """Share of unique persons (customer_unique_id) with more than one revenue order.

    NOTE: customer_id is generated per order in Olist, so it can never repeat.
    """
    rev = orders[~orders["order_status"].isin(NON_REVENUE_STATUSES)]
    per_person = rev.groupby("customer_unique_id")["order_id"].nunique()
    return round(100 * safe_divide((per_person > 1).sum(), len(per_person)), 2)


def review_by_delivery_status(orders: pd.DataFrame) -> pd.DataFrame:
    r = orders.dropna(subset=["review_score"])
    return (r.groupby("delivery_status")["review_score"]
              .agg(orders_with_review="size", avg_review_score="mean").round(3).reset_index())


# ------------------------------ INVENTORY ----------------------------------
def inventory_turnover(units_consumed: float, avg_inventory_units: float) -> float:
    """Units consumed in a period / average on-hand units in the same period."""
    return round(safe_divide(units_consumed, avg_inventory_units), 3)


def days_of_inventory_on_hand(net_available_units: float, daily_demand_units: float) -> float | None:
    return None if daily_demand_units <= 0 else round(net_available_units / daily_demand_units, 1)


def stockout_risk_pct(risk_status: pd.Series) -> float:
    return round(100 * safe_divide(risk_status.isin(["Critical", "High Risk"]).sum(), len(risk_status)), 2)


def inventory_value(available_units: pd.Series, unit_cost: pd.Series) -> float:
    return round(float((available_units * unit_cost).sum()), 2)


# ------------------------------ SUPPLIER -----------------------------------
def supplier_kpis(pos: pd.DataFrame, late_tolerance_days: float = 1.0) -> pd.DataFrame:
    """pos: simulated purchase orders with supplier_id, status, simulated_lead_time_days, delay_days."""
    rec = pos[pos["status"] == "RECEIVED"].copy()
    rec["is_late"] = rec["delay_days"] > late_tolerance_days
    out = rec.groupby("supplier_id").agg(received=("supplier_id", "size"),
                                         avg_lead_time_days=("simulated_lead_time_days", "mean"),
                                         delay_rate_pct=("is_late", "mean"))
    out["delay_rate_pct"] = (100 * out["delay_rate_pct"]).round(2)
    out["reliability_score_pct"] = (100 - out["delay_rate_pct"]).round(2)
    out["rank"] = out["reliability_score_pct"].rank(ascending=False, method="min").astype(int)
    return out.reset_index()


# ------------------------------ SNAPSHOT -----------------------------------
SNAPSHOT_VIEWS = {
    "executive": "SELECT * FROM analytics.vw_executive_kpis",
    "delivery": "SELECT * FROM analytics.vw_delivery_kpis",
    "customer": "SELECT * FROM analytics.vw_customer_kpis",
    "inventory_simulated": "SELECT * FROM analytics.vw_inventory_kpis",
}


def fetch_kpi_snapshot(engine: Engine | None = None, write: bool = True) -> dict:
    engine = engine or get_engine()
    snapshot = {"generated_at_utc": utc_now()}
    for name, sql in SNAPSHOT_VIEWS.items():
        df = read_sql(sql, engine=engine)
        snapshot[name] = {} if df.empty else {k: (None if pd.isna(v) else v) for k, v in df.iloc[0].to_dict().items()}
    snapshot["supplier_simulated"] = read_sql(
        "SELECT supplier_id, avg_actual_lead_time_days, delay_rate_pct, reliability_score_pct, performance_rank "
        "FROM analytics.vw_supplier_kpis ORDER BY performance_rank", engine=engine).to_dict(orient="records")
    if write:
        path = get_settings().paths.reports_dir / "kpis" / "kpi_snapshot.json"
        write_json(snapshot, path)
        logger.info("KPI snapshot written to %s", path)
    return snapshot


if __name__ == "__main__":
    import json
    from src.utils.helpers import json_default
    print(json.dumps(fetch_kpi_snapshot(), indent=2, default=json_default)[:3000])
