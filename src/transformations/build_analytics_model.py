"""Create the database schema, load STAGING and build the ANALYTICS model.

Execution order of SQL scripts (all idempotent):

    02_create_raw_tables.sql      raw schema + tables
    03_create_staging_tables.sql  staging tables
    04_create_dimensions.sql      dimensions (incl. simulated dims)
    05_create_fact_tables.sql     facts + supporting tables
    06_create_analytics_views.sql KPI views
    07_create_indexes.sql         indexes
    09_transform_staging_to_analytics.sql  (every run)
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy.engine import Engine

from config.database import copy_dataframe, ensure_database_exists, get_engine, raw_connection, read_sql, run_sql_file
from config.settings import get_settings
from src.preprocessing.clean_data import STAGING_COLUMNS
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA_SCRIPTS = [
    "02_create_raw_tables.sql",
    "03_create_staging_tables.sql",
    "04_create_dimensions.sql",
    "05_create_fact_tables.sql",
    "06_create_analytics_views.sql",
    "07_create_indexes.sql",
]
TRANSFORM_SCRIPT = "09_transform_staging_to_analytics.sql"

STAGING_TABLES = {key: f"staging.{key}" for key in STAGING_COLUMNS}


def init_database_schema(engine: Engine | None = None, create_database: bool = True) -> None:
    if create_database:
        ensure_database_exists()
    engine = engine or get_engine()
    sql_dir = get_settings().paths.sql_dir
    for script in SCHEMA_SCRIPTS:
        run_sql_file(sql_dir / script, engine)
    logger.info("Database schema is up to date")


def _prepare_for_copy(key: str, df: pd.DataFrame) -> pd.DataFrame:
    out = df[STAGING_COLUMNS[key]].copy()
    for col in out.columns:
        if out[col].dtype == bool or str(out[col].dtype) == "boolean":
            out[col] = out[col].map({True: "t", False: "f"})
    # integer-like columns may arrive as float after CSV round-trip
    for col in ("order_item_id", "payment_sequential", "payment_installments", "review_score", "n_points"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
    return out


def load_staging(cleaned: dict[str, pd.DataFrame], engine: Engine | None = None) -> dict[str, int]:
    """Replace all staging tables in ONE transaction (TRUNCATE + COPY)."""
    engine = engine or get_engine()
    counts: dict[str, int] = {}
    with raw_connection(engine) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE " + ", ".join(STAGING_TABLES.values()))
        for key, table in STAGING_TABLES.items():
            counts[key] = copy_dataframe(_prepare_for_copy(key, cleaned[key]), table, conn=conn)
            logger.info("Staged %-36s %9d rows", table, counts[key])
    return counts


def run_transformations(engine: Engine | None = None) -> dict[str, int]:
    engine = engine or get_engine()
    run_sql_file(get_settings().paths.sql_dir / TRANSFORM_SCRIPT, engine)
    counts = table_counts(engine, ["dim_product", "dim_customer", "dim_seller", "fact_orders", "fact_order_items",
                                   "fact_payments", "fact_reviews", "fact_shipments"])
    logger.info("Analytics model counts: %s", counts)
    return counts


def table_counts(engine: Engine, tables: list[str], schema: str = "analytics") -> dict[str, int]:
    sql = " UNION ALL ".join(f"SELECT '{t}' AS t, COUNT(*) AS n FROM {schema}.{t}" for t in tables)
    df = read_sql(sql, engine=engine)
    return dict(zip(df["t"], df["n"].astype(int)))


if __name__ == "__main__":
    from src.preprocessing.clean_data import read_processed
    init_database_schema()
    load_staging(read_processed())
    print(run_transformations())
