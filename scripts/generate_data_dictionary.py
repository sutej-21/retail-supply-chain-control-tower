"""Generate docs/data_dictionary.md from the live PostgreSQL schema (column names/types/nullability)
combined with the table descriptions below. Run after `init_database_schema()`."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import read_sql  # noqa: E402

TABLES = {
    "raw": "Original Olist CSV rows, every column TEXT + lineage (_batch_id, _source_file, _loaded_at).",
    "staging.orders": "One row per order (Olist). Timestamps parsed; `has_timestamp_issue` flags material inconsistencies.",
    "staging.order_items": "One row per order item (Olist). `is_price_valid` = price > 0.",
    "staging.customers": "One row per customer_id. customer_id is generated per ORDER; customer_unique_id identifies the person.",
    "staging.products": "Products with category (pt/en). Missing categories -> 'unknown'.",
    "staging.sellers": "Sellers with standardised city/state/zip prefix.",
    "staging.payments": "One row per (order_id, payment_sequential).",
    "staging.reviews": "One row per (review_id, order_id).",
    "staging.geolocation": "One row per zip-code prefix (median lat/lng of points inside Brazil).",
    "staging.category_translation": "Portuguese -> English category names.",
    "analytics.dim_date": "Calendar 2016-01-01 .. today+400d. Key = YYYYMMDD.",
    "analytics.dim_product": "Products + historical sales attributes (avg valid unit price, units sold).",
    "analytics.dim_customer": "Customers with zip-prefix centroid.",
    "analytics.dim_seller": "Sellers with zip-prefix centroid.",
    "analytics.dim_supplier": "SIMULATED suppliers (fictional names) with planned and realised lead times.",
    "analytics.dim_warehouse": "SIMULATED distribution centres.",
    "analytics.bridge_product_supplier": "SIMULATED SKU -> supplier / warehouse mapping and unit cost proxy.",
    "analytics.fact_orders": "Grain: order. Items and payments pre-aggregated (no fan-out). Delivery and review measures.",
    "analytics.fact_order_items": "Grain: order item. Revenue line items.",
    "analytics.fact_payments": "Grain: payment.",
    "analytics.fact_reviews": "Grain: review. `is_latest_for_order` marks the review used at order level.",
    "analytics.fact_shipments": "Grain: shipment. OUTBOUND_CUSTOMER = Olist order x seller; INBOUND_REPLENISHMENT = SIMULATED PO.",
    "analytics.fact_inventory": "SIMULATED hourly snapshot per product x warehouse.",
    "analytics.fact_inventory_movements": "SIMULATED movements; quantity signed (+in / -out).",
    "analytics.fact_supplier_performance": "SIMULATED purchase orders / supplier delivery events.",
    "analytics.fact_demand_forecast": "Prophet forecasts per level/entity/origin/date with intervals.",
    "analytics.forecast_accuracy": "Hold-out accuracy per model (MAE, RMSE, MAPE, WAPE, seasonal-naive MAE).",
    "analytics.inventory_thresholds": "Current replenishment policy & risk per SKU-location (recomputed each run).",
    "analytics.anomaly_events": "Detected anomalies (deterministic id) with severity and recommended action.",
    "analytics.alert_history": "All alerts with de-dup fingerprint, status, delivery channels and occurrences.",
    "analytics.simulation_scenarios": "What-if scenario parameters and summary.",
    "analytics.scenario_results": "Per SKU results for BASELINE and MODIFIED variants.",
    "analytics.scenario_projection": "Daily aggregate projection per scenario/variant.",
    "analytics.etl_run_log": "Per task execution log (status, duration, rows, message).",
    "analytics.data_quality_log": "Persisted data-quality check results per run.",
    "analytics.simulation_state": "Simulation clock and pipeline state (checksums, forecast refresh).",
}


def main() -> None:
    cols = read_sql("""
        SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.is_nullable, t.table_type
        FROM information_schema.columns c
        JOIN information_schema.tables t USING (table_schema, table_name)
        WHERE c.table_schema IN ('raw', 'staging', 'analytics')
        ORDER BY c.table_schema, t.table_type, c.table_name, c.ordinal_position""")
    pks = read_sql("""
        SELECT tc.table_schema, tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu USING (constraint_schema, constraint_name)
        WHERE tc.constraint_type = 'PRIMARY KEY'""")
    pk = set(zip(pks.table_schema, pks.table_name, pks.column_name))
    out = ["# Data Dictionary", "",
           "Generated from the live schema by `scripts/generate_data_dictionary.py`. "
           "**[SIM]** = synthetic operational data, not Olist. PK = primary key.", ""]
    for (schema, table, ttype), g in cols.groupby(["table_schema", "table_name", "table_type"], sort=False):
        fq = f"{schema}.{table}"
        kind = "view" if ttype == "VIEW" else "table"
        desc = TABLES.get(fq, TABLES["raw"] if schema == "raw" and table != "load_audit" else "")
        out += [f"## `{fq}` ({kind})", "", desc, "", "| Column | Type | Nullable | Key |", "|---|---|---|---|"]
        for r in g.itertuples():
            out.append(f"| {r.column_name} | {r.data_type} | {r.is_nullable} | {'PK' if (schema, table, r.column_name) in pk else ''} |")
        out.append("")
    path = Path(__file__).resolve().parents[1] / "docs" / "data_dictionary.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
