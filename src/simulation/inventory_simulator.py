"""SIMULATED hourly inventory engine driven by historical Olist demand.

What is real and what is simulated
----------------------------------
* REAL (Olist): which products sell, their relative popularity, average daily
  demand and its variability (last ``SIM_DEMAND_LOOKBACK_DAYS`` of history),
  and average historical unit prices.
* SIMULATED: stock levels, reservations, warehouses, suppliers, purchase
  orders, lead times, receipts, demand surges and stock adjustments.

Timeline
--------
Olist history ends in 2018, so the operational simulation runs on its own clock
(UTC hour of each pipeline run). Hourly demand = historical average daily
demand / 24 x intraday profile x random over-dispersion x optional surge. The
hourly DAG therefore refreshes a *simulated* operational feed - it does not
imply that Kaggle provides live transactions.

Reproducibility: the random stream for every simulated hour is seeded with
``(SIMULATION_RANDOM_SEED, hour_index)``, so re-running the same hours yields
identical records, and all inserts are idempotent (deterministic IDs + ON CONFLICT).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm
from sqlalchemy.engine import Engine

from config.database import get_engine, raw_connection, read_sql, upsert_dataframe
from config.settings import get_settings
from src.simulation import supplier_simulator as sup
from src.utils.helpers import stable_hash, utc_now
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# Intraday e-commerce order profile (Brazil local time is UTC-3); normalised to mean 1
_RAW_HOURLY = np.array([0.35, 0.2, 0.12, 0.08, 0.07, 0.1, 0.25, 0.5, 0.8, 1.1, 1.3, 1.4,
                        1.4, 1.45, 1.5, 1.5, 1.45, 1.4, 1.35, 1.4, 1.45, 1.35, 1.0, 0.65])
HOURLY_WEIGHTS = _RAW_HOURLY / _RAW_HOURLY.mean()
UTC_TO_BRT = -3
MAX_CATCHUP_HOURS = 168


PO_DATETIME_COLUMNS = ("order_ts", "expected_arrival_ts", "actual_arrival_ts")


def _append_rows(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Row-append that is robust to block-layout differences (pandas 2.1 datetime concat issue)."""
    if existing.empty:
        combined = new.copy()
    else:
        combined = pd.DataFrame(existing.to_dict("records") + new[existing.columns.intersection(new.columns)
                                                                  .tolist()].to_dict("records"))
    for col in PO_DATETIME_COLUMNS:
        if col in combined:
            combined[col] = pd.to_datetime(combined[col]).astype("datetime64[ns]")
    return combined


def floor_hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0, tzinfo=None)


@dataclass
class SimulationResult:
    start_ts: datetime | None
    end_ts: datetime | None
    hours_simulated: int
    snapshots: int
    movements: int
    purchase_orders_created: int
    receipts: int
    lost_units: int
    bootstrapped: bool


# --------------------------------------------------------------------------- #
# Demand profile from historical data (REAL Olist demand)
# --------------------------------------------------------------------------- #
def load_demand_profile(engine: Engine, n_products: int, lookback_days: int) -> pd.DataFrame:
    top = read_sql("""
        WITH top AS (
            SELECT oi.product_id, COUNT(*) AS units_total, MIN(oi.purchase_date) AS first_date,
                   MAX(oi.purchase_date) AS last_date
            FROM analytics.fact_order_items oi
            WHERE oi.is_revenue_order
            GROUP BY oi.product_id
            ORDER BY COUNT(*) DESC, oi.product_id
            LIMIT :n
        ),
        seller_state AS (
            SELECT DISTINCT ON (oi.product_id) oi.product_id, s.state AS main_seller_state
            FROM analytics.fact_order_items oi JOIN analytics.dim_seller s ON s.seller_id = oi.seller_id
            WHERE oi.product_id IN (SELECT product_id FROM top)
            GROUP BY oi.product_id, s.state
            ORDER BY oi.product_id, COUNT(*) DESC, s.state
        )
        SELECT t.*, p.category_name_en, COALESCE(p.avg_unit_price, 0) AS avg_unit_price, ss.main_seller_state
        FROM top t
        JOIN analytics.dim_product p ON p.product_id = t.product_id
        LEFT JOIN seller_state ss ON ss.product_id = t.product_id
    """, {"n": n_products}, engine=engine)
    if top.empty:
        raise RuntimeError("No historical sales found - run the ETL transformations before the simulation")
    daily = read_sql("""
        SELECT oi.product_id, oi.purchase_date, COUNT(*) AS units
        FROM analytics.fact_order_items oi
        WHERE oi.is_revenue_order AND oi.product_id = ANY(:ids)
          AND oi.purchase_date > (SELECT max_date FROM analytics.vw_data_window) - :lb
        GROUP BY 1, 2
    """, {"ids": top["product_id"].tolist(), "lb": lookback_days}, engine=engine)
    end = read_sql("SELECT max_date FROM analytics.vw_data_window", engine=engine).iloc[0, 0]
    days = pd.date_range(pd.Timestamp(end) - pd.Timedelta(days=lookback_days - 1), pd.Timestamp(end))
    matrix = (daily.assign(purchase_date=pd.to_datetime(daily["purchase_date"]))
              .pivot_table(index="purchase_date", columns="product_id", values="units", aggfunc="sum")
              .reindex(index=days, columns=top["product_id"], fill_value=0).fillna(0))
    top["avg_daily_demand"] = matrix.mean().values
    top["demand_std"] = matrix.std(ddof=1).fillna(0).values
    span = (pd.to_datetime(top["last_date"]) - pd.to_datetime(top["first_date"])).dt.days + 1
    overall = top["units_total"] / span.clip(lower=1)
    fallback = top["avg_daily_demand"] <= 0
    top.loc[fallback, "avg_daily_demand"] = overall[fallback]
    top.loc[fallback, "demand_std"] = np.sqrt(overall[fallback])  # Poisson assumption
    top["main_seller_state"] = top["main_seller_state"].fillna("SP")
    return top


def initial_policy(daily: np.ndarray, std: np.ndarray, lt: np.ndarray, lt_std: np.ndarray,
                   service_level: float, review_days: int) -> dict[str, np.ndarray]:
    z = norm.ppf(service_level)
    ss = np.ceil(z * np.sqrt(lt * std ** 2 + daily ** 2 * lt_std ** 2))
    rop = np.ceil(daily * lt + ss)
    max_stock = np.ceil(rop + daily * (review_days + 30))
    return {"safety_stock": ss.astype(int), "reorder_level": np.maximum(rop, 1).astype(int),
            "maximum_stock": np.maximum(max_stock, rop + 2).astype(int)}


class InventorySimulator:
    def __init__(self, engine: Engine | None = None, seed: int | None = None):
        self.engine = engine or get_engine()
        self.settings = get_settings()
        self.sim = self.settings.simulation
        self.seed = self.sim.random_seed if seed is None else seed
        self.suppliers = sup.generate_suppliers(self.sim.n_suppliers, self.seed)

    # ------------------------------------------------------------------ #
    # Master data
    # ------------------------------------------------------------------ #
    def ensure_master_data(self) -> pd.DataFrame:
        """Create simulated suppliers/warehouses/product mapping if missing. Returns the SKU table."""
        profile = load_demand_profile(self.engine, self.sim.n_products, self.sim.demand_lookback_days)
        with raw_connection(self.engine) as conn:
            upsert_dataframe(sup.generate_warehouses(), "analytics.dim_warehouse", ["warehouse_id"], conn=conn)
            upsert_dataframe(
                self.suppliers.drop(columns="_latent_reliability"), "analytics.dim_supplier", ["supplier_id"],
                update_columns=["supplier_name", "supplier_location", "supplier_state", "average_lead_time_days",
                                "lead_time_std_days"], conn=conn)
            mapping = sup.assign_products(profile, self.suppliers, self.seed)
            upsert_dataframe(mapping, "analytics.bridge_product_supplier", ["product_id"], update_columns=[], conn=conn)
        bridge = read_sql("SELECT product_id, supplier_id, warehouse_id, unit_cost_proxy "
                          "FROM analytics.bridge_product_supplier", engine=self.engine)
        skus = profile.merge(bridge, on="product_id", how="inner").merge(
            self.suppliers[["supplier_id", "average_lead_time_days", "lead_time_std_days", "_latent_reliability"]],
            on="supplier_id")
        return skus.sort_values(["product_id", "warehouse_id"]).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #
    def _get_state(self, key: str):
        df = read_sql("SELECT state_value FROM analytics.simulation_state WHERE state_key = :k", {"k": key},
                      engine=self.engine)
        if df.empty:
            return None
        val = df.iloc[0, 0]
        return json.loads(val) if isinstance(val, str) else val

    def _thresholds(self) -> pd.DataFrame:
        return read_sql("SELECT product_id, warehouse_id, safety_stock, reorder_point AS reorder_level, "
                        "maximum_stock, reorder_qty FROM analytics.inventory_thresholds", engine=self.engine)

    def _bootstrap_state(self, skus: pd.DataFrame, start_ts: datetime) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
        rng = np.random.default_rng([self.seed, 0])
        inv = self.settings.inventory
        pol = initial_policy(skus["avg_daily_demand"].values, skus["demand_std"].values,
                             skus["average_lead_time_days"].values, skus["lead_time_std_days"].values,
                             inv.service_level, inv.review_period_days)
        state = skus[["product_id", "warehouse_id", "supplier_id", "avg_daily_demand", "average_lead_time_days",
                      "lead_time_std_days", "_latent_reliability"]].copy()
        for k, v in pol.items():
            state[k] = v
        state["reorder_qty"] = np.maximum(state["maximum_stock"] - state["reorder_level"], 1)
        start = np.round(state["maximum_stock"] * rng.uniform(0.45, 1.0, len(state)))
        low = rng.random(len(state)) < self.sim.low_stock_share
        start = np.where(low, np.round(state["safety_stock"] * rng.uniform(0.0, 1.5, len(state))), start)
        state["available_stock"] = start.astype(int)
        state["reserved_stock"] = 0
        state["on_order_qty"] = 0
        state["surge_mult"] = 1.0
        state["surge_hours_left"] = 0
        init_mov = pd.DataFrame({
            "movement_id": [stable_hash("INITIAL_STOCK", p, w, start_ts, length=24)
                            for p, w in zip(state["product_id"], state["warehouse_id"])],
            "product_id": state["product_id"], "warehouse_id": state["warehouse_id"],
            "movement_type": "INITIAL_STOCK", "quantity": state["available_stock"],
            "movement_timestamp": start_ts, "reference_order_id": None,
        })
        return state, [init_mov[init_mov["quantity"] > 0]]

    def _load_state(self, skus: pd.DataFrame) -> pd.DataFrame:
        snap = read_sql("""
            SELECT DISTINCT ON (product_id, warehouse_id) product_id, warehouse_id, available_stock,
                   reserved_stock, on_order_qty, reorder_level, safety_stock, maximum_stock
            FROM analytics.fact_inventory ORDER BY product_id, warehouse_id, inventory_timestamp DESC
        """, engine=self.engine)
        state = skus[["product_id", "warehouse_id", "supplier_id", "avg_daily_demand", "average_lead_time_days",
                      "lead_time_std_days", "_latent_reliability"]].merge(snap, on=["product_id", "warehouse_id"],
                                                                         how="inner")
        state["reorder_qty"] = np.maximum(state["maximum_stock"] - state["reorder_level"], 1)
        surge = self._get_state("surges") or {}
        state["surge_mult"] = [surge.get(p, {}).get("mult", 1.0) for p in state["product_id"]]
        state["surge_hours_left"] = [surge.get(p, {}).get("hours", 0) for p in state["product_id"]]
        return state

    def _apply_thresholds(self, state: pd.DataFrame) -> pd.DataFrame:
        thr = self._thresholds()
        if thr.empty:
            return state
        merged = state.merge(thr, on=["product_id", "warehouse_id"], how="left", suffixes=("", "_thr"))
        for col in ["safety_stock", "reorder_level", "maximum_stock", "reorder_qty"]:
            merged[col] = merged[f"{col}_thr"].fillna(merged[col]).astype(int)
        return merged.drop(columns=[c for c in merged.columns if c.endswith("_thr")])

    # ------------------------------------------------------------------ #
    # One simulated hour (vectorised over SKUs)
    # ------------------------------------------------------------------ #
    def _step(self, state: pd.DataFrame, open_pos: pd.DataFrame, ts: datetime, out: dict) -> pd.DataFrame:
        hour_index = int(ts.timestamp() // 3600)
        rng = np.random.default_rng([self.seed, hour_index])
        n = len(state)
        key = list(zip(state["product_id"], state["warehouse_id"]))

        # 1) receive purchase orders that have arrived
        if not open_pos.empty:
            due = open_pos["status"].eq("IN_TRANSIT") & (open_pos["actual_arrival_ts"] <= ts)
            if due.any():
                received = open_pos[due]
                qty = received.groupby(["product_id", "warehouse_id"])["order_qty"].sum()
                add = np.array([qty.get(k, 0) for k in key])
                state["available_stock"] += add
                state["on_order_qty"] = np.maximum(state["on_order_qty"] - add, 0)
                open_pos.loc[due, "status"] = "RECEIVED"
                out["movements"].append(pd.DataFrame({
                    "movement_id": [stable_hash("RECEIPT", r, length=24) for r in received["po_id"]],
                    "product_id": received["product_id"].values, "warehouse_id": received["warehouse_id"].values,
                    "movement_type": "REPLENISHMENT_RECEIPT", "quantity": received["order_qty"].values,
                    "movement_timestamp": received["actual_arrival_ts"].values,
                    "reference_order_id": received["po_id"].values,
                }))
                out["receipts"] += int(due.sum())

        # 2) ship last hour's reservations (consumption)
        shipped = state["reserved_stock"].values.copy()
        state["available_stock"] -= shipped
        state["reserved_stock"] = 0
        mask = shipped > 0
        if mask.any():
            out["movements"].append(pd.DataFrame({
                "movement_id": [stable_hash("SALE", p, w, ts, length=24) for p, w in np.array(key, dtype=object)[mask]],
                "product_id": state["product_id"].values[mask], "warehouse_id": state["warehouse_id"].values[mask],
                "movement_type": "SALE_CONSUMPTION", "quantity": -shipped[mask], "movement_timestamp": ts,
                "reference_order_id": ["SIMORD-" + stable_hash(p, ts, length=12) for p in state["product_id"].values[mask]],
            }))

        # 3) demand surges (simulated events for anomaly detection)
        state["surge_hours_left"] = np.maximum(state["surge_hours_left"] - 1, 0)
        state.loc[state["surge_hours_left"] == 0, "surge_mult"] = 1.0
        start_surge = (state["surge_hours_left"].values == 0) & (rng.random(n) < 0.0015)
        state.loc[start_surge, "surge_mult"] = rng.uniform(3, 6, start_surge.sum())
        state.loc[start_surge, "surge_hours_left"] = rng.integers(6, 19, start_surge.sum())

        # 4) new demand -> reservations (over-dispersed Poisson)
        local_hour = (ts.hour + UTC_TO_BRT) % 24
        lam = state["avg_daily_demand"].values / 24.0 * HOURLY_WEIGHTS[local_hour] * state["surge_mult"].values
        demand = rng.poisson(lam * rng.gamma(2.0, 0.5, n))
        reserved = np.minimum(demand, state["available_stock"].values)
        state["reserved_stock"] = reserved
        out["lost_units"] += int((demand - reserved).sum())

        # 5) occasional cycle-count adjustment (shrinkage)
        adj = (rng.random(n) < 0.0008) & (state["available_stock"].values - reserved > 3)
        if adj.any():
            q = -rng.integers(1, 4, adj.sum())
            state.loc[adj, "available_stock"] += q
            out["movements"].append(pd.DataFrame({
                "movement_id": [stable_hash("ADJ", p, w, ts, length=24) for p, w in np.array(key, dtype=object)[adj]],
                "product_id": state["product_id"].values[adj], "warehouse_id": state["warehouse_id"].values[adj],
                "movement_type": "ADJUSTMENT", "quantity": q, "movement_timestamp": ts, "reference_order_id": None,
            }))

        # 6) reorder when inventory position <= reorder level and no open PO
        position = state["available_stock"] - state["reserved_stock"] + state["on_order_qty"]
        has_open = np.zeros(n, dtype=bool)
        if not open_pos.empty:
            open_keys = set(zip(open_pos.loc[open_pos["status"] == "IN_TRANSIT", "product_id"],
                                open_pos.loc[open_pos["status"] == "IN_TRANSIT", "warehouse_id"]))
            has_open = np.array([k in open_keys for k in key])
        trigger = (position.values <= state["reorder_level"].values) & ~has_open & (state["avg_daily_demand"].values > 0)
        if trigger.any():
            t = state[trigger]
            qty = np.maximum.reduce([t["reorder_qty"].values, (t["maximum_stock"] - position[trigger]).values,
                                     np.full(len(t), self.settings.inventory.min_order_qty)]).astype(int)
            realised, disrupted = sup.sample_lead_times(
                t["average_lead_time_days"].values, t["lead_time_std_days"].values,
                t["_latent_reliability"].values, rng, self.sim.lead_time_disruption_prob)
            po_ids = [f"PO-{ts:%Y%m%d%H}-{stable_hash(p, w, ts, length=8)}" for p, w in zip(t["product_id"], t["warehouse_id"])]
            new = pd.DataFrame({
                "po_id": po_ids, "supplier_id": t["supplier_id"].values, "product_id": t["product_id"].values,
                "warehouse_id": t["warehouse_id"].values, "order_qty": qty, "order_ts": ts,
                "expected_lead_time_days": t["average_lead_time_days"].values,
                "expected_arrival_ts": [ts + timedelta(days=float(d)) for d in t["average_lead_time_days"]],
                "simulated_lead_time_days": realised,
                "actual_arrival_ts": [ts + timedelta(days=float(d)) for d in realised],
                "is_disrupted": disrupted, "status": "IN_TRANSIT",
            })
            open_pos = _append_rows(open_pos, new)
            state.loc[trigger, "on_order_qty"] += qty
            out["pos_created"] += len(new)

        # 7) hourly snapshot
        out["snapshots"].append(state[["product_id", "warehouse_id", "available_stock", "reserved_stock",
                                       "on_order_qty", "reorder_level", "safety_stock", "maximum_stock"]]
                                .assign(inventory_timestamp=ts))
        return open_pos

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def run(self, until_ts: datetime | None = None) -> SimulationResult:
        """Advance the simulation clock up to ``until_ts`` (default: current UTC hour)."""
        target = floor_hour(until_ts or utc_now())
        skus = self.ensure_master_data()
        last = self._get_state("clock")
        last_ts = pd.Timestamp(last["last_sim_ts"]).to_pydatetime() if last else None
        out = {"snapshots": [], "movements": [], "receipts": 0, "pos_created": 0, "lost_units": 0}
        bootstrapped = False
        if last_ts is None:
            start = target - timedelta(hours=self.sim.bootstrap_hours)
            state, init_movements = self._bootstrap_state(skus, start)
            out["movements"].extend(init_movements)
            open_pos = pd.DataFrame()
            first = start + timedelta(hours=1)
            bootstrapped = True
            logger.info("Bootstrapping simulation: %d SKUs, %d hours from %s", len(state), self.sim.bootstrap_hours, start)
        else:
            if last_ts >= target:
                logger.info("Simulation already at %s - nothing to do (idempotent)", last_ts)
                return SimulationResult(None, last_ts, 0, 0, 0, 0, 0, 0, False)
            state = self._apply_thresholds(self._load_state(skus))
            open_pos = read_sql("SELECT * FROM analytics.fact_supplier_performance WHERE status = 'IN_TRANSIT'",
                                engine=self.engine)
            for c in ("order_ts", "expected_arrival_ts", "actual_arrival_ts"):
                if c in open_pos:
                    open_pos[c] = pd.to_datetime(open_pos[c])
            pending = self._get_state("pending_arrivals") or {}
            if not open_pos.empty:
                open_pos["actual_arrival_ts"] = pd.to_datetime(open_pos["po_id"].map(pending))
                missing = open_pos["actual_arrival_ts"].isna()
                # fallback (state lost): realised arrival = order time + simulated lead time
                open_pos.loc[missing, "actual_arrival_ts"] = (
                    open_pos.loc[missing, "order_ts"]
                    + pd.to_timedelta(open_pos.loc[missing, "simulated_lead_time_days"].astype(float), unit="D"))
                open_pos = open_pos.drop(columns=[c for c in ("delay_days", "is_late", "is_simulated") if c in open_pos])
            first = last_ts + timedelta(hours=1)
            if (target - first) > timedelta(hours=MAX_CATCHUP_HOURS):
                logger.warning("Simulation gap > %d h: skipping ahead to %s", MAX_CATCHUP_HOURS,
                               target - timedelta(hours=MAX_CATCHUP_HOURS))
                first = target - timedelta(hours=MAX_CATCHUP_HOURS)
        # simulated "true" demand rate is fixed at the historical average (planning values may differ)
        ts = first
        hours = 0
        while ts <= target:
            open_pos = self._step(state, open_pos, ts, out)
            ts += timedelta(hours=1)
            hours += 1
        self._persist(out, open_pos, state, first, target)
        sup.refresh_supplier_metrics(self.engine)
        result = SimulationResult(first, target, hours, sum(len(s) for s in out["snapshots"]),
                                  sum(len(m) for m in out["movements"]), out["pos_created"], out["receipts"],
                                  out["lost_units"], bootstrapped)
        logger.info("Simulation result: %s", result)
        return result

    def _persist(self, out: dict, open_pos: pd.DataFrame, state: pd.DataFrame, first: datetime,
                 target: datetime) -> None:
        snapshots = pd.concat(out["snapshots"], ignore_index=True)
        frames = []
        for m in out["movements"]:
            m = m.copy()
            m["movement_timestamp"] = pd.to_datetime(m["movement_timestamp"]).astype("datetime64[ns]")
            m["reference_order_id"] = m["reference_order_id"].astype(object)
            frames.append(m)
        movements = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        surges = {p: {"mult": float(m), "hours": int(h)} for p, m, h in
                  zip(state["product_id"], state["surge_mult"], state["surge_hours_left"]) if h > 0}
        with raw_connection(self.engine) as conn:
            upsert_dataframe(snapshots, "analytics.fact_inventory",
                             ["product_id", "warehouse_id", "inventory_timestamp"], update_columns=[], conn=conn)
            if not movements.empty:
                movements["quantity"] = movements["quantity"].astype(int)
                upsert_dataframe(movements, "analytics.fact_inventory_movements", ["movement_id"],
                                 update_columns=[], conn=conn)
            if not open_pos.empty:
                pos = open_pos.copy()
                received = pos["status"] == "RECEIVED"
                pos["delay_days"] = np.where(received, (pos["simulated_lead_time_days"].astype(float)
                                                        - pos["expected_lead_time_days"].astype(float)).round(2), np.nan)
                pos["is_late"] = np.where(received, pos["delay_days"] > 1.0, None)
                pos.loc[~received, "actual_arrival_ts"] = pd.NaT
                pos["is_disrupted"] = pos["is_disrupted"].astype(bool)
                cols = ["po_id", "supplier_id", "product_id", "warehouse_id", "order_qty", "order_ts",
                        "expected_lead_time_days", "expected_arrival_ts", "simulated_lead_time_days",
                        "actual_arrival_ts", "delay_days", "is_late", "is_disrupted", "status"]
                # keep the realised arrival for in-transit POs in the state (needed to receive them later)
                po_rows = pos[cols].copy()
                upsert_dataframe(po_rows, "analytics.fact_supplier_performance", ["po_id"], conn=conn)
                # store pending arrival times separately (they are 'future' information)
                pending = open_pos.loc[open_pos["status"] == "IN_TRANSIT", ["po_id", "actual_arrival_ts"]]
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO analytics.simulation_state (state_key, state_value, updated_at) VALUES "
                        "('pending_arrivals', %s, now() AT TIME ZONE 'utc') ON CONFLICT (state_key) DO UPDATE "
                        "SET state_value = EXCLUDED.state_value, updated_at = EXCLUDED.updated_at",
                        (json.dumps({r.po_id: pd.Timestamp(r.actual_arrival_ts).isoformat()
                                     for r in pending.itertuples()}),))
                sh = pos.merge(read_sql("SELECT warehouse_id, state FROM analytics.dim_warehouse", engine=self.engine),
                               on="warehouse_id").merge(self.suppliers[["supplier_id", "supplier_state"]], on="supplier_id")
                shipments = pd.DataFrame({
                    "shipment_id": sh["po_id"], "shipment_type": "INBOUND_REPLENISHMENT", "po_id": sh["po_id"],
                    "supplier_id": sh["supplier_id"], "product_id": sh["product_id"],
                    "warehouse_id": sh["warehouse_id"], "origin_state": sh["supplier_state"],
                    "destination_state": sh["state"], "quantity": sh["order_qty"], "shipped_ts": sh["order_ts"],
                    "delivered_ts": sh["actual_arrival_ts"], "estimated_ts": sh["expected_arrival_ts"],
                    "delivery_days": np.where(sh["status"] == "RECEIVED", sh["simulated_lead_time_days"], np.nan),
                    "delay_days": sh["delay_days"], "is_late": sh["is_late"],
                    "shipment_status": np.where(sh["status"] == "RECEIVED", "DELIVERED", "IN_TRANSIT"),
                    "data_source": "SIMULATED", "is_simulated": True,
                })
                upsert_dataframe(shipments, "analytics.fact_shipments", ["shipment_id"], conn=conn)
            with conn.cursor() as cur:
                for key, value in (("clock", {"last_sim_ts": target.isoformat(), "first_sim_ts_last_run": first.isoformat()}),
                                   ("surges", surges)):
                    cur.execute(
                        "INSERT INTO analytics.simulation_state (state_key, state_value, updated_at) VALUES "
                        "(%s, %s, now() AT TIME ZONE 'utc') ON CONFLICT (state_key) DO UPDATE "
                        "SET state_value = EXCLUDED.state_value, updated_at = EXCLUDED.updated_at",
                        (key, json.dumps(value)))
        self._export_extracts(snapshots)

    def _export_extracts(self, snapshots: pd.DataFrame) -> None:
        out_dir = self.settings.paths.simulated_dir
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            latest = snapshots[snapshots["inventory_timestamp"] == snapshots["inventory_timestamp"].max()]
            latest.assign(is_simulated=True).to_csv(out_dir / "SIMULATED_inventory_latest.csv", index=False)
            self.suppliers.drop(columns="_latent_reliability").assign(is_simulated=True).to_csv(
                out_dir / "SIMULATED_suppliers.csv", index=False)
        except OSError as exc:  # pragma: no cover
            logger.warning("Could not write simulated extracts: %s", exc)


def run_simulation(until_ts: datetime | None = None, engine: Engine | None = None) -> SimulationResult:
    return InventorySimulator(engine).run(until_ts)


if __name__ == "__main__":
    print(run_simulation())
