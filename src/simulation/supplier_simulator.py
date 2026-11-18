"""SIMULATED supplier, warehouse and purchase-order engine.

The Olist dataset contains no supplier, warehouse, purchase-order or lead-time
data. Everything produced by this module is SYNTHETIC, reproducible through a
seed, and stored with ``is_simulated = TRUE`` / ``data_source = 'SIMULATED'``.
Supplier names are fictional ("Simulated Supplier 001 Ltda").
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from config.database import execute
from src.utils.helpers import stable_hash
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

WAREHOUSES = pd.DataFrame([
    ("WH-SP", "Simulated DC Cajamar", "cajamar", "SP", 400_000),
    ("WH-RJ", "Simulated DC Duque de Caxias", "duque de caxias", "RJ", 150_000),
    ("WH-MG", "Simulated DC Contagem", "contagem", "MG", 120_000),
    ("WH-PR", "Simulated DC Sao Jose dos Pinhais", "sao jose dos pinhais", "PR", 100_000),
    ("WH-BA", "Simulated DC Camacari", "camacari", "BA", 80_000),
], columns=["warehouse_id", "warehouse_name", "city", "state", "capacity_units"])

SUPPLIER_LOCATIONS = [
    ("sao paulo", "SP"), ("campinas", "SP"), ("guarulhos", "SP"), ("sorocaba", "SP"), ("curitiba", "PR"),
    ("joinville", "SC"), ("belo horizonte", "MG"), ("rio de janeiro", "RJ"), ("porto alegre", "RS"),
    ("manaus", "AM"), ("recife", "PE"), ("goiania", "GO"),
]
# Planned lead time depends roughly on distance to the SP hub
STATE_BASE_LEAD_TIME = {"SP": 4, "PR": 6, "SC": 7, "MG": 6, "RJ": 6, "RS": 8, "AM": 14, "PE": 11, "GO": 8}


def generate_suppliers(n_suppliers: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(SUPPLIER_LOCATIONS), n_suppliers)
    rows = []
    for i in range(n_suppliers):
        city, state = SUPPLIER_LOCATIONS[idx[i]]
        base = STATE_BASE_LEAD_TIME[state]
        avg_lt = round(float(base + rng.uniform(0, 6)) * 2) / 2
        rows.append({
            "supplier_id": f"SUP-{i + 1:03d}",
            "supplier_name": f"Simulated Supplier {i + 1:03d} Ltda",
            "supplier_location": f"{city}, {state}",
            "supplier_state": state,
            "average_lead_time_days": avg_lt,
            "lead_time_std_days": round(float(rng.uniform(0.5, 0.25 * avg_lt + 0.5)), 2),
            # per-supplier latent reliability: probability a PO is NOT disrupted (hidden driver of the simulation)
            "_latent_reliability": float(rng.beta(9, 1.5)),
            "supplier_status": "ACTIVE",
        })
    return pd.DataFrame(rows)


def generate_warehouses() -> pd.DataFrame:
    return WAREHOUSES.copy()


def assign_products(products: pd.DataFrame, suppliers: pd.DataFrame, seed: int,
                    cost_ratio_range: tuple[float, float] = (0.55, 0.70)) -> pd.DataFrame:
    """Map each simulated SKU to one supplier (per category) and one warehouse (by seller state).

    ``products`` needs: product_id, category_name_en, avg_unit_price, main_seller_state.
    """
    rng = np.random.default_rng(seed + 1)
    categories = sorted(products["category_name_en"].unique())
    supplier_ids = suppliers["supplier_id"].tolist()
    # each category gets a primary and secondary supplier; products alternate deterministically
    cat_suppliers = {c: rng.choice(supplier_ids, size=2, replace=False) for c in categories}
    wh_states = set(WAREHOUSES["state"])
    rows = []
    for r in products.itertuples(index=False):
        pair = cat_suppliers[r.category_name_en]
        supplier = pair[int(stable_hash(r.product_id, length=8), 16) % 2]
        state = r.main_seller_state if r.main_seller_state in wh_states else "SP"
        price = float(r.avg_unit_price) if pd.notna(r.avg_unit_price) and r.avg_unit_price > 0 else 50.0
        rows.append({
            "product_id": r.product_id, "supplier_id": supplier, "warehouse_id": f"WH-{state}",
            "unit_cost_proxy": round(price * float(rng.uniform(*cost_ratio_range)), 2),
        })
    return pd.DataFrame(rows)


def sample_lead_times(planned: np.ndarray, std: np.ndarray, reliability: np.ndarray,
                      rng: np.random.Generator, disruption_prob: float) -> tuple[np.ndarray, np.ndarray]:
    """Realised lead time = planned + noise (+ disruption of 5-15 days).

    Disruption probability grows as latent reliability falls.
    """
    noise = rng.normal(0, std)
    p_disrupt = np.clip(disruption_prob + (1 - reliability) * 0.5, 0, 0.9)
    disrupted = rng.random(len(planned)) < p_disrupt
    extra = np.where(disrupted, rng.uniform(5, 15, len(planned)), 0.0)
    realised = np.maximum(1.0, planned + noise + extra)
    return np.round(realised, 2), disrupted


def refresh_supplier_metrics(engine: Engine, late_tolerance_days: float = 1.0) -> None:
    """Recompute actual lead time, reliability and status from received simulated POs."""
    execute(f"""
        WITH m AS (
            SELECT supplier_id, AVG(simulated_lead_time_days) AS actual_lt,
                   AVG(CASE WHEN delay_days <= {late_tolerance_days} THEN 1.0 ELSE 0.0 END) AS reliability,
                   COUNT(*) AS n
            FROM analytics.fact_supplier_performance WHERE status = 'RECEIVED' GROUP BY supplier_id
        )
        UPDATE analytics.dim_supplier s
        SET actual_simulated_lead_time_days = ROUND(m.actual_lt, 2),
            supplier_reliability_score = ROUND(m.reliability, 4),
            supplier_status = CASE WHEN m.n >= 5 AND m.reliability < 0.5 THEN 'SUSPENDED'
                                   WHEN m.n >= 5 AND (m.reliability < 0.8
                                        OR m.actual_lt > s.average_lead_time_days * 1.3) THEN 'ON_WATCH'
                                   ELSE 'ACTIVE' END,
            updated_at = now() AT TIME ZONE 'utc'
        FROM m WHERE m.supplier_id = s.supplier_id
    """, engine=engine)
    logger.info("Supplier metrics refreshed")
