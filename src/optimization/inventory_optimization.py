"""Inventory optimisation & replenishment engine.

Formulas (documented in docs/methodology.md):

* Safety stock  SS  = z * sqrt( L * sigma_d^2 + d^2 * sigma_L^2 )
      z = Phi^-1(service level), d / sigma_d = mean / std of daily demand,
      L / sigma_L = mean / std of supplier lead time (days)
* Reorder point ROP = d * L + SS
* Order-up-to level  S = d * (L + R) + SS            (R = review period)
* Reorder quantity   Q = max(0, S - inventory position),
      inventory position = available - reserved + on order;
      rounded up to the minimum order quantity, capped at maximum stock.
* Risk classes (first matching rule wins; thresholds configurable):
      Critical  : net available < safety stock (or out of stock with demand)
      High Risk : days of cover < supplier lead time  (projected stock-out before a new PO can arrive)
      Warning   : net available <= ROP * (1 + warning_buffer)
      Healthy   : otherwise

Demand ``d`` is the Prophet forecast mean for the next 30 days where a product
forecast exists, otherwise the historical average (real Olist demand).
Stock, lead times and suppliers are SIMULATED.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import norm
from sqlalchemy.engine import Engine

from config.database import copy_dataframe, get_engine, raw_connection, read_sql
from config.settings import InventorySettings, get_settings
from src.simulation.inventory_simulator import load_demand_profile
from src.utils.helpers import utc_now
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

RISK_LEVELS = ["Critical", "High Risk", "Warning", "Healthy"]


# --------------------------------------------------------------------------- #
# Pure, unit-testable functions
# --------------------------------------------------------------------------- #
def z_value(service_level: float) -> float:
    if not 0.5 <= service_level < 1:
        raise ValueError("service_level must be in [0.5, 1)")
    return float(norm.ppf(service_level))


def safety_stock(z: float, daily_demand, demand_std, lead_time, lead_time_std):
    d, sd, lt, slt = (np.asarray(x, dtype=float) for x in (daily_demand, demand_std, lead_time, lead_time_std))
    return z * np.sqrt(lt * sd ** 2 + d ** 2 * slt ** 2)


def reorder_point(daily_demand, lead_time, safety_stock_units):
    return np.asarray(daily_demand, dtype=float) * np.asarray(lead_time, dtype=float) + np.asarray(safety_stock_units, dtype=float)


def reorder_quantity(daily_demand, lead_time, review_period, safety_stock_units, available, reserved, on_order,
                     maximum_stock=None, min_order_qty: int = 1):
    d = np.asarray(daily_demand, dtype=float)
    position = np.asarray(available, dtype=float) - np.asarray(reserved, dtype=float) + np.asarray(on_order, dtype=float)
    order_up_to = d * (np.asarray(lead_time, dtype=float) + review_period) + np.asarray(safety_stock_units, dtype=float)
    qty = np.maximum(0.0, np.ceil(order_up_to - position))
    if maximum_stock is not None:
        qty = np.minimum(qty, np.maximum(np.asarray(maximum_stock, dtype=float) - position, 0))
    qty = np.where((qty > 0) & (qty < min_order_qty), min_order_qty, qty)
    return qty.astype(int)


def days_of_cover(net_available, daily_demand):
    d = np.asarray(daily_demand, dtype=float)
    net = np.asarray(net_available, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        cover = np.where(d > 0, np.maximum(net, 0) / d, np.nan)
    return cover


def classify_risk(net_available, safety_stock_units, reorder_point_units, cover_days, lead_time,
                  daily_demand, warning_buffer: float = 0.2) -> np.ndarray:
    net = np.asarray(net_available, dtype=float)
    ss = np.asarray(safety_stock_units, dtype=float)
    rop = np.asarray(reorder_point_units, dtype=float)
    cover = np.asarray(cover_days, dtype=float)
    lt = np.asarray(lead_time, dtype=float)
    d = np.asarray(daily_demand, dtype=float)
    critical = ((net < ss) & (ss > 0)) | ((net <= 0) & (d > 0))
    high = ~critical & (d > 0) & (cover < lt)
    warning = ~critical & ~high & (net <= rop * (1 + warning_buffer)) & (d > 0)
    return np.select([critical, high, warning], ["Critical", "High Risk", "Warning"], default="Healthy")


def recommended_action(row) -> str:
    supplier = row.get("supplier_id") or "the supplier"
    qty = int(row["reorder_qty"])
    on_order = int(row.get("on_order_qty", 0) or 0)
    in_transit = f" {on_order} units already on order." if on_order > 0 else ""
    cover = row["days_of_cover"]
    cover_txt = "n/a" if pd.isna(cover) else f"{cover:.1f}"
    if row["risk_status"] == "Critical":
        if qty > 0:
            return (f"Initiate replenishment immediately: expedite {qty} units from {supplier} and consider a "
                    f"transfer from another DC.{in_transit}")
        return f"Stock below safety stock; expedite the open purchase order with {supplier}.{in_transit}"
    if row["risk_status"] == "High Risk":
        return (f"Place a replenishment order of {max(qty, 1)} units now: cover {cover_txt} days < lead time "
                f"{row['lead_time_days']:.1f} days.{in_transit}")
    if row["risk_status"] == "Warning":
        return f"Plan a reorder of {max(qty, 1)} units within the next review period.{in_transit}"
    return "No action required."


def build_policy(df: pd.DataFrame, settings: InventorySettings) -> pd.DataFrame:
    """Compute policy columns for a frame with demand/lead-time/stock inputs.

    Required columns: avg_daily_demand, demand_std, planning_daily_demand, lead_time_days,
    lead_time_std_days, available_stock, reserved_stock, on_order_qty.
    """
    out = df.copy()
    z = z_value(settings.service_level)
    d = out["planning_daily_demand"].astype(float).values
    # scale historical variability to the planning demand level (coefficient of variation preserved)
    hist = out["avg_daily_demand"].astype(float).replace(0, np.nan)
    sd = (out["demand_std"].astype(float) * (out["planning_daily_demand"].astype(float) / hist)).fillna(
        out["demand_std"].astype(float)).values
    lt = out["lead_time_days"].astype(float).values
    slt = out["lead_time_std_days"].astype(float).values
    out["service_level"] = settings.service_level
    out["z_value"] = round(z, 4)
    out["safety_stock"] = np.ceil(safety_stock(z, d, sd, lt, slt)).astype(int)
    out["reorder_point"] = np.ceil(reorder_point(d, lt, out["safety_stock"])).astype(int)
    out["maximum_stock"] = np.ceil(out["safety_stock"] + d * (lt + settings.review_period_days + 30)).astype(int)
    out["maximum_stock"] = np.maximum(out["maximum_stock"], out["reorder_point"] + 1)
    out["net_available"] = (out["available_stock"] - out["reserved_stock"]).astype(int)
    out["reorder_qty"] = reorder_quantity(d, lt, settings.review_period_days, out["safety_stock"],
                                          out["available_stock"], out["reserved_stock"], out["on_order_qty"],
                                          out["maximum_stock"], settings.min_order_qty)
    out["days_of_cover"] = np.round(days_of_cover(out["net_available"], d), 2)
    out["forecast_demand_lead_time"] = np.round(d * lt, 2)
    out["risk_status"] = classify_risk(out["net_available"], out["safety_stock"], out["reorder_point"],
                                       out["days_of_cover"], lt, d, settings.warning_buffer)
    out["recommended_action"] = out.apply(recommended_action, axis=1)
    return out


# --------------------------------------------------------------------------- #
# Database integration
# --------------------------------------------------------------------------- #
def _lead_time_stats(engine: Engine) -> pd.DataFrame:
    """Planned lead time, replaced by realised simulated stats once >= 3 POs were received."""
    return read_sql("""
        SELECT s.supplier_id,
               CASE WHEN COUNT(f.po_id) >= 3 THEN AVG(f.simulated_lead_time_days) ELSE s.average_lead_time_days END
                   AS lead_time_days,
               CASE WHEN COUNT(f.po_id) >= 3 THEN COALESCE(STDDEV_SAMP(f.simulated_lead_time_days), s.lead_time_std_days)
                    ELSE s.lead_time_std_days END AS lead_time_std_days
        FROM analytics.dim_supplier s
        LEFT JOIN analytics.fact_supplier_performance f ON f.supplier_id = s.supplier_id AND f.status = 'RECEIVED'
        GROUP BY s.supplier_id, s.average_lead_time_days, s.lead_time_std_days
    """, engine=engine)


def _forecast_daily(engine: Engine) -> pd.DataFrame:
    return read_sql("""
        SELECT entity_id AS product_id, AVG(yhat) AS forecast_daily_demand
        FROM analytics.vw_forecast_latest
        WHERE model_level = 'product' AND horizon_day <= 30
        GROUP BY entity_id
    """, engine=engine)


def compute_inventory_policy(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_engine()
    settings = get_settings()
    snap = read_sql("""
        SELECT DISTINCT ON (i.product_id, i.warehouse_id) i.product_id, i.warehouse_id, i.available_stock,
               i.reserved_stock, i.on_order_qty, i.inventory_timestamp, b.supplier_id
        FROM analytics.fact_inventory i
        JOIN analytics.bridge_product_supplier b ON b.product_id = i.product_id
        ORDER BY i.product_id, i.warehouse_id, i.inventory_timestamp DESC
    """, engine=engine)
    if snap.empty:
        raise RuntimeError("No simulated inventory found - run the inventory simulation first")
    demand = load_demand_profile(engine, settings.simulation.n_products, settings.inventory.demand_window_days)
    df = (snap.merge(demand[["product_id", "avg_daily_demand", "demand_std"]], on="product_id", how="left")
              .merge(_lead_time_stats(engine), on="supplier_id", how="left")
              .merge(_forecast_daily(engine), on="product_id", how="left"))
    df[["avg_daily_demand", "demand_std"]] = df[["avg_daily_demand", "demand_std"]].fillna(0.0)
    df["forecast_daily_demand"] = df["forecast_daily_demand"].clip(lower=0)
    df["planning_daily_demand"] = df["forecast_daily_demand"].fillna(df["avg_daily_demand"])
    policy = build_policy(df, settings.inventory)
    policy["projected_stockout_ts"] = [
        ts + pd.Timedelta(days=float(c)) if pd.notna(c) else pd.NaT
        for ts, c in zip(pd.to_datetime(policy["inventory_timestamp"]), policy["days_of_cover"])
    ]
    policy["computed_at"] = utc_now()
    policy["is_simulated"] = True
    return policy


THRESHOLD_COLUMNS = ["product_id", "warehouse_id", "supplier_id", "avg_daily_demand", "demand_std",
                     "forecast_daily_demand", "planning_daily_demand", "lead_time_days", "lead_time_std_days",
                     "service_level", "z_value", "safety_stock", "reorder_point", "reorder_qty", "maximum_stock",
                     "available_stock", "reserved_stock", "on_order_qty", "net_available", "days_of_cover",
                     "projected_stockout_ts", "forecast_demand_lead_time", "risk_status", "recommended_action",
                     "inventory_timestamp", "computed_at", "is_simulated"]


def update_inventory_thresholds(engine: Engine | None = None) -> pd.DataFrame:
    """Recompute and atomically replace ``analytics.inventory_thresholds``."""
    engine = engine or get_engine()
    policy = compute_inventory_policy(engine)
    out = policy[THRESHOLD_COLUMNS].copy()
    for col in ("avg_daily_demand", "demand_std", "forecast_daily_demand", "planning_daily_demand"):
        out[col] = out[col].astype(float).round(4)
    out["lead_time_days"] = out["lead_time_days"].astype(float).round(2)
    out["lead_time_std_days"] = out["lead_time_std_days"].astype(float).round(2)
    out["is_simulated"] = "t"
    with raw_connection(engine) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM analytics.inventory_thresholds")
        copy_dataframe(out, "analytics.inventory_thresholds", conn=conn)
    summary = out["risk_status"].value_counts().reindex(RISK_LEVELS, fill_value=0).to_dict()
    logger.info("Inventory policy updated for %d SKU-locations: %s", len(out), summary)
    return policy


def summarize(policy: pd.DataFrame) -> dict:
    return {
        "sku_locations": int(len(policy)),
        "risk_distribution": policy["risk_status"].value_counts().reindex(RISK_LEVELS, fill_value=0).to_dict(),
        "total_reorder_units": int(policy["reorder_qty"].sum()),
        "products_needing_action": int((policy["risk_status"] != "Healthy").sum()),
        "median_days_of_cover": None if policy["days_of_cover"].dropna().empty
        else float(math.floor(policy["days_of_cover"].median() * 10) / 10),
    }


if __name__ == "__main__":
    p = update_inventory_thresholds()
    print(summarize(p))
    print(p.sort_values("days_of_cover").head(10)[["product_id", "net_available", "safety_stock", "reorder_point",
                                                   "reorder_qty", "days_of_cover", "risk_status"]])
