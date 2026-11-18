"""What-if scenario analysis (e.g. "What if a supplier is delayed by 7 days?").

For every simulated SKU-location a daily inventory projection is run for a
BASELINE and a MODIFIED scenario over ``horizon_days``:

    stock[t] = stock[t-1] + arrivals[t] - min(stock[t-1] + arrivals[t], demand[t])
    unfulfilled[t] = max(demand[t] - (stock[t-1] + arrivals[t]), 0)

* Open purchase orders arrive at their planned arrival date (+ delay for the
  affected supplier in the modified scenario).
* A new order of Q units is placed when the inventory position falls to the
  reorder point; it arrives after the lead time (+ delay if affected).
* The planner's reorder point stays at the BASELINE demand level (the planner
  does not know about the demand shock in advance) - this is what exposes risk.

Revenue exposure = unfulfilled units x average historical Olist unit price.
It is a POTENTIAL exposure proxy, NOT confirmed lost revenue (customers may
wait, substitute, or buy elsewhere). Inventory and suppliers are SIMULATED.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from config.database import copy_dataframe, get_engine, raw_connection, read_sql
from config.settings import get_settings
from src.utils.helpers import json_default, stable_hash, utc_now
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ScenarioParams:
    scenario_name: str = "Supplier delayed by 7 days"
    supplier_id: str | None = None          # None = every supplier is delayed
    supplier_delay_days: float = 7.0
    demand_growth_pct: float = 0.0
    inventory_adjustment_pct: float = 0.0   # e.g. -20 = 20% less available inventory
    replenishment_qty_override: int | None = None
    safety_stock_multiplier: float = 1.0
    horizon_days: int = 60
    description: str | None = None

    def baseline(self) -> "ScenarioParams":
        return replace(self, scenario_name="Baseline", supplier_delay_days=0.0, demand_growth_pct=0.0,
                       inventory_adjustment_pct=0.0, replenishment_qty_override=None, safety_stock_multiplier=1.0)


REQUIRED_INPUT_COLUMNS = ["product_id", "warehouse_id", "supplier_id", "net_available", "planning_daily_demand",
                          "lead_time_days", "safety_stock", "reorder_point", "reorder_qty", "unit_price_proxy",
                          "open_po_qty", "open_po_arrival_day"]


def project_inventory(inputs: pd.DataFrame, params: ScenarioParams, start: date | None = None
                      ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Vectorised daily projection. Returns (per-SKU results, daily aggregate)."""
    missing = [c for c in REQUIRED_INPUT_COLUMNS if c not in inputs.columns]
    if missing:
        raise ValueError(f"scenario inputs missing columns: {missing}")
    start = start or utc_now().date()
    df = inputs.reset_index(drop=True)
    n, horizon = len(df), int(params.horizon_days)
    affected_supplier = np.ones(n, dtype=bool) if params.supplier_id is None \
        else (df["supplier_id"].values == params.supplier_id)
    delay = np.where(affected_supplier, params.supplier_delay_days, 0.0)

    base_d = df["planning_daily_demand"].astype(float).values
    demand = base_d * (1 + params.demand_growth_pct / 100.0)
    stock = np.maximum(df["net_available"].astype(float).values * (1 + params.inventory_adjustment_pct / 100.0), 0)
    start_stock = stock.copy()
    lead = df["lead_time_days"].astype(float).values
    ss = df["safety_stock"].astype(float).values * params.safety_stock_multiplier
    rop = base_d * lead + ss                                      # planner's reorder point
    q = df["reorder_qty"].astype(float).values
    if params.replenishment_qty_override is not None:
        q = np.full(n, float(params.replenishment_qty_override))
    q = np.where(q <= 0, np.ceil(base_d * (lead + 7) + ss), q)    # fall back to order-up-to quantity

    arrivals = np.zeros((n, horizon + 1))
    po_qty = df["open_po_qty"].fillna(0).astype(float).values
    po_day = df["open_po_arrival_day"].fillna(1).astype(float).values + delay
    po_idx = np.clip(np.ceil(po_day), 1, horizon + 1).astype(int)
    has_po = po_qty > 0
    for i in np.where(has_po & (po_idx <= horizon))[0]:
        arrivals[i, po_idx[i]] += po_qty[i]
    pending = has_po & (po_idx <= horizon)

    stock_path = np.zeros((n, horizon))
    unfulfilled_path = np.zeros((n, horizon))
    for t in range(1, horizon + 1):
        pending &= ~(po_idx <= t)                                # orders that arrived
        stock = stock + arrivals[:, t]
        served = np.minimum(stock, demand)
        unfulfilled_path[:, t - 1] = demand - served
        stock = stock - served
        stock_path[:, t - 1] = stock
        position = stock + arrivals[:, t + 1:].sum(axis=1)       # on hand + scheduled arrivals
        order = (position <= rop) & ~pending & (base_d > 0)
        if order.any():
            arrive = t + np.ceil(lead[order] + delay[order]).astype(int)
            rows = np.where(order)[0]
            for r, a in zip(rows, arrive):
                if a <= horizon:
                    arrivals[r, a] += q[r]
            pending[rows] = True
            po_idx[rows] = arrive

    out_of_stock = (stock_path <= 1e-9) & (demand[:, None] > 0)
    first_out = np.where(out_of_stock.any(axis=1), out_of_stock.argmax(axis=1) + 1, -1)
    cover = np.where(demand > 0, start_stock / np.where(demand > 0, demand, 1), np.inf)
    required = lead + delay
    add_cover = np.where(demand > 0, np.maximum(required - cover, 0), 0)
    price = df["unit_price_proxy"].fillna(0).astype(float).values
    unfulfilled = unfulfilled_path.sum(axis=1)

    per_sku = pd.DataFrame({
        "product_id": df["product_id"], "warehouse_id": df["warehouse_id"], "supplier_id": df["supplier_id"],
        "category_name_en": df.get("category_name_en"),
        "starting_stock": np.round(start_stock).astype(int), "daily_demand": np.round(demand, 4),
        "lead_time_days": np.round(lead + delay, 2), "replenishment_qty": q.astype(int),
        "safety_stock": np.round(ss).astype(int), "projected_end_stock": np.round(stock_path[:, -1], 2),
        "min_projected_stock": np.round(stock_path.min(axis=1), 2),
        "stockout_date": [start + pd.Timedelta(days=int(d)) if d > 0 else None for d in first_out],
        "days_until_stockout": np.where(first_out > 0, first_out, np.nan),
        "unfulfilled_units": np.round(unfulfilled, 2),
        "additional_cover_days_required": np.round(add_cover, 2),
        "additional_units_required": np.round(add_cover * demand, 2),
        "unit_price_proxy": np.round(price, 2),
        "revenue_exposure_proxy": np.round(unfulfilled * price, 2),
    })
    per_sku["stockout_date"] = pd.to_datetime(per_sku["stockout_date"]).dt.date
    daily = pd.DataFrame({
        "day_offset": np.arange(1, horizon + 1),
        "projection_date": [start + pd.Timedelta(days=int(t)) for t in range(1, horizon + 1)],
        "projected_inventory": stock_path.sum(axis=0).round(2),
        "unfulfilled_units": unfulfilled_path.sum(axis=0).round(2),
        "products_out_of_stock": out_of_stock.sum(axis=0).astype(int),
    })
    daily["projection_date"] = pd.to_datetime(daily["projection_date"]).dt.date
    return per_sku, daily


def compare(baseline: pd.DataFrame, modified: pd.DataFrame) -> pd.DataFrame:
    """Flag SKUs whose outcome worsens in the modified scenario."""
    m = modified.merge(baseline[["product_id", "warehouse_id", "unfulfilled_units", "days_until_stockout"]],
                       on=["product_id", "warehouse_id"], suffixes=("", "_base"))
    earlier = m["days_until_stockout"].notna() & (m["days_until_stockout_base"].isna()
                                                  | (m["days_until_stockout"] < m["days_until_stockout_base"]))
    m["is_affected"] = (m["unfulfilled_units"] > m["unfulfilled_units_base"] + 1e-6) | earlier
    return m.drop(columns=["unfulfilled_units_base", "days_until_stockout_base"])


def summarize(base: pd.DataFrame, mod: pd.DataFrame) -> dict:
    def s(df):
        return {"products_stocking_out": int(df["stockout_date"].notna().sum()),
                "unfulfilled_units": round(float(df["unfulfilled_units"].sum()), 1),
                "revenue_exposure_proxy": round(float(df["revenue_exposure_proxy"].sum()), 2),
                "additional_units_required": round(float(df["additional_units_required"].sum()), 1)}
    b, m = s(base), s(mod)
    return {"baseline": b, "modified": m, "products_affected": int(mod["is_affected"].sum()),
            "delta": {k: round(m[k] - b[k], 2) for k in b}}


# --------------------------------------------------------------------------- #
# Database integration
# --------------------------------------------------------------------------- #
def load_scenario_inputs(engine: Engine) -> pd.DataFrame:
    df = read_sql("""
        WITH po AS (
            SELECT product_id, warehouse_id, SUM(order_qty) AS open_po_qty,
                   MIN(expected_arrival_ts) AS next_expected_arrival
            FROM analytics.fact_supplier_performance WHERE status = 'IN_TRANSIT'
            GROUP BY product_id, warehouse_id
        )
        SELECT t.product_id, t.warehouse_id, t.supplier_id, p.category_name_en, t.net_available,
               t.planning_daily_demand, t.lead_time_days, t.safety_stock, t.reorder_point, t.reorder_qty,
               COALESCE(p.avg_unit_price, 0) AS unit_price_proxy, COALESCE(po.open_po_qty, 0) AS open_po_qty,
               GREATEST(1, CEIL(EXTRACT(EPOCH FROM (po.next_expected_arrival - t.inventory_timestamp)) / 86400.0))
                   AS open_po_arrival_day
        FROM analytics.inventory_thresholds t
        JOIN analytics.dim_product p ON p.product_id = t.product_id
        LEFT JOIN po ON po.product_id = t.product_id AND po.warehouse_id = t.warehouse_id
    """, engine=engine)
    if df.empty:
        raise RuntimeError("inventory_thresholds is empty - run the simulation and inventory optimisation first")
    return df


def run_scenario(params: ScenarioParams, engine: Engine | None = None, save: bool = True,
                 make_plot: bool = True) -> dict:
    engine = engine or get_engine()
    inputs = load_scenario_inputs(engine)
    start = utc_now().date()
    base, base_daily = project_inventory(inputs, params.baseline(), start)
    mod, mod_daily = project_inventory(inputs, params, start)
    mod = compare(base, mod)
    base["is_affected"] = False
    summary = summarize(base, mod)
    scenario_id = "SCN-" + stable_hash(params.scenario_name, utc_now().isoformat(), json.dumps(asdict(params)), length=12)
    logger.info("Scenario %s (%s): %s", scenario_id, params.scenario_name, summary)
    if save:
        results = pd.concat([base.assign(variant="BASELINE"), mod.assign(variant="MODIFIED")], ignore_index=True)
        results["scenario_id"] = scenario_id
        results["is_affected"] = results["is_affected"].map({True: "t", False: "f"})
        daily = pd.concat([base_daily.assign(variant="BASELINE"), mod_daily.assign(variant="MODIFIED")],
                          ignore_index=True).assign(scenario_id=scenario_id)
        with raw_connection(engine) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO analytics.simulation_scenarios (scenario_id, scenario_name, description, supplier_id, "
                    "supplier_delay_days, demand_growth_pct, inventory_adjustment_pct, replenishment_qty_override, "
                    "safety_stock_multiplier, horizon_days, summary) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (scenario_id) DO NOTHING",
                    (scenario_id, params.scenario_name, params.description, params.supplier_id,
                     params.supplier_delay_days, params.demand_growth_pct, params.inventory_adjustment_pct,
                     params.replenishment_qty_override, params.safety_stock_multiplier, params.horizon_days,
                     json.dumps(summary, default=json_default)))
            copy_dataframe(results[["scenario_id", "product_id", "warehouse_id", "variant", "category_name_en",
                                    "supplier_id", "starting_stock", "daily_demand", "lead_time_days",
                                    "replenishment_qty", "safety_stock", "projected_end_stock",
                                    "min_projected_stock", "stockout_date", "days_until_stockout",
                                    "unfulfilled_units", "additional_cover_days_required",
                                    "additional_units_required", "unit_price_proxy", "revenue_exposure_proxy",
                                    "is_affected"]].assign(days_until_stockout=lambda x: x["days_until_stockout"].astype("Int64")),
                           "analytics.scenario_results", conn=conn)
            copy_dataframe(daily[["scenario_id", "variant", "day_offset", "projection_date", "projected_inventory",
                                  "unfulfilled_units", "products_out_of_stock"]], "analytics.scenario_projection",
                           conn=conn)
    if make_plot:
        _plot(scenario_id, params, base_daily, mod_daily, mod)
    return {"scenario_id": scenario_id, "params": asdict(params), "summary": summary,
            "top_affected": mod[mod["is_affected"]].nlargest(10, "revenue_exposure_proxy")
            [["product_id", "supplier_id", "stockout_date", "unfulfilled_units", "revenue_exposure_proxy"]]
            .to_dict(orient="records")}


def _plot(scenario_id: str, params: ScenarioParams, base_daily: pd.DataFrame, mod_daily: pd.DataFrame,
          mod: pd.DataFrame) -> None:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:  # pragma: no cover
        return
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Projected inventory (units, SIMULATED)",
                                                        "Cumulative unfulfilled demand (units)"))
    for df, name, color in ((base_daily, "Baseline", "#2E7D32"), (mod_daily, params.scenario_name, "#C62828")):
        fig.add_trace(go.Scatter(x=df["projection_date"], y=df["projected_inventory"], name=name,
                                 line=dict(color=color)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df["projection_date"], y=df["unfulfilled_units"].cumsum(), name=f"{name} (unfulfilled)",
                                 line=dict(color=color, dash="dot")), row=1, col=2)
    fig.update_layout(template="plotly_white", title=f"What-if: {params.scenario_name} - {int(mod['is_affected'].sum())} "
                      f"SKUs affected, potential revenue exposure (proxy) R$ {mod['revenue_exposure_proxy'].sum():,.0f}")
    path = get_settings().paths.reports_dir / "scenarios" / f"{scenario_id}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(path, include_plotlyjs="cdn")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run a what-if supply chain scenario")
    ap.add_argument("--name", default=None)
    ap.add_argument("--supplier", default=None, help="supplier_id to delay (default: all suppliers)")
    ap.add_argument("--delay", type=float, default=7.0, help="supplier delay in days")
    ap.add_argument("--growth", type=float, default=0.0, help="demand growth in %%")
    ap.add_argument("--inventory-adj", type=float, default=0.0, help="available inventory change in %%")
    ap.add_argument("--replenishment-qty", type=int, default=None)
    ap.add_argument("--safety-mult", type=float, default=1.0)
    ap.add_argument("--horizon", type=int, default=60)
    a = ap.parse_args()
    name = a.name or f"Supplier {a.supplier or 'ALL'} delayed {a.delay:g}d, demand {a.growth:+g}%"
    res = run_scenario(ScenarioParams(name, a.supplier, a.delay, a.growth, a.inventory_adj, a.replenishment_qty,
                                      a.safety_mult, a.horizon))
    print(json.dumps(res, indent=2, default=json_default))


if __name__ == "__main__":
    main()
