"""Read the Olist CSV files and load the RAW layer in PostgreSQL.

Raw-layer principles
--------------------
* Every source column is stored as TEXT exactly as read (no type coercion), so
  the raw layer is a faithful, re-playable copy of the CSV files.
* Three lineage columns are added: ``_batch_id``, ``_source_file``, ``_loaded_at``.
* Loads are idempotent: each table is TRUNCATEd and re-COPYed inside a single
  transaction, and a load is skipped when the file checksum is unchanged since
  the last successful load (recorded in ``raw.load_audit``).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy.engine import Engine

from config.database import copy_dataframe, get_engine, raw_connection, read_sql
from config.settings import OLIST_FILES
from src.ingestion.kaggle_download import resolve_source_dir, verify_source_files
from src.utils.helpers import file_sha256, utc_now
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# Expected source columns (single source of truth, mirrored in sql/02_create_raw_tables.sql)
SOURCE_COLUMNS: dict[str, list[str]] = {
    "orders": ["order_id", "customer_id", "order_status", "order_purchase_timestamp", "order_approved_at",
               "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date"],
    "order_items": ["order_id", "order_item_id", "product_id", "seller_id", "shipping_limit_date", "price",
                    "freight_value"],
    "customers": ["customer_id", "customer_unique_id", "customer_zip_code_prefix", "customer_city",
                  "customer_state"],
    "products": ["product_id", "product_category_name", "product_name_lenght", "product_description_lenght",
                 "product_photos_qty", "product_weight_g", "product_length_cm", "product_height_cm",
                 "product_width_cm"],
    "sellers": ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"],
    "payments": ["order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value"],
    "reviews": ["review_id", "order_id", "review_score", "review_comment_title", "review_comment_message",
                "review_creation_date", "review_answer_timestamp"],
    "geolocation": ["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng", "geolocation_city",
                    "geolocation_state"],
    "category_translation": ["product_category_name", "product_category_name_english"],
}

RAW_TABLES: dict[str, str] = {key: f"raw.olist_{key}" for key in OLIST_FILES}

DATE_COLUMNS: dict[str, list[str]] = {
    "orders": ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
               "order_delivered_customer_date", "order_estimated_delivery_date"],
    "order_items": ["shipping_limit_date"],
    "reviews": ["review_creation_date", "review_answer_timestamp"],
}

NUMERIC_COLUMNS: dict[str, list[str]] = {
    "order_items": ["order_item_id", "price", "freight_value"],
    "products": ["product_name_lenght", "product_description_lenght", "product_photos_qty",
                 "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"],
    "payments": ["payment_sequential", "payment_installments", "payment_value"],
    "reviews": ["review_score"],
    "geolocation": ["geolocation_lat", "geolocation_lng"],
}


class SchemaMismatchError(ValueError):
    """Raised when a CSV does not have the expected Olist columns."""


def read_csv_as_text(path: Path) -> pd.DataFrame:
    """Read a CSV with every column as string (used for the raw layer and profiling)."""
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""], encoding="utf-8")


def check_columns(key: str, df: pd.DataFrame) -> None:
    expected = SOURCE_COLUMNS[key]
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise SchemaMismatchError(f"{OLIST_FILES[key]} is missing columns: {missing}")


def load_source_csvs(source_dir: Path | None = None, as_text: bool = True) -> dict[str, pd.DataFrame]:
    """Load all nine CSV files (validating presence and columns).

    ``as_text=True`` returns raw strings; ``as_text=False`` returns typed frames
    (numbers coerced with ``errors='coerce'``, timestamps parsed).
    """
    paths = verify_source_files(source_dir or resolve_source_dir())
    frames: dict[str, pd.DataFrame] = {}
    for key, path in paths.items():
        df = read_csv_as_text(path)
        check_columns(key, df)
        frames[key] = df if as_text else to_typed(key, df)
        logger.info("Read %-22s %9d rows x %d cols", key, len(df), df.shape[1])
    return frames


def to_typed(key: str, df: pd.DataFrame) -> pd.DataFrame:
    """Coerce numeric/date columns; invalid values become NaN/NaT (counted by validation)."""
    out = df.copy()
    for col in NUMERIC_COLUMNS.get(key, []):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in DATE_COLUMNS.get(key, []):
        out[col] = pd.to_datetime(out[col], errors="coerce")
    return out


def _last_loaded_checksum(table: str, engine: Engine) -> str | None:
    df = read_sql(
        "SELECT sha256 FROM raw.load_audit WHERE table_name = :t AND status = 'SUCCESS' "
        "ORDER BY loaded_at DESC LIMIT 1",
        {"t": table}, engine=engine,
    )
    return None if df.empty else df.iloc[0, 0]


def load_raw_tables(
    source_dir: Path | None = None,
    engine: Engine | None = None,
    force: bool = False,
) -> dict[str, dict]:
    """Load each CSV into its raw table. Returns per-table load statistics."""
    engine = engine or get_engine()
    paths = verify_source_files(source_dir or resolve_source_dir())
    batch_id = str(uuid.uuid4())
    results: dict[str, dict] = {}
    for key, path in paths.items():
        table = RAW_TABLES[key]
        checksum = file_sha256(path)
        if not force and checksum == _last_loaded_checksum(table, engine):
            logger.info("Skip %s: source unchanged (sha256 %s...)", table, checksum[:10])
            results[key] = {"table": table, "rows": None, "skipped": True}
            continue
        df = read_csv_as_text(path)
        check_columns(key, df)
        df = df[SOURCE_COLUMNS[key]].copy()
        df["_batch_id"] = batch_id
        df["_source_file"] = path.name
        df["_loaded_at"] = utc_now()
        with raw_connection(engine) as conn:
            with conn.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {table}")
            copy_dataframe(df, table, conn=conn)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO raw.load_audit (batch_id, table_name, source_file, sha256, row_count, status) "
                    "VALUES (%s, %s, %s, %s, %s, 'SUCCESS')",
                    (batch_id, table, path.name, checksum, len(df)),
                )
        logger.info("Loaded %-32s %9d rows", table, len(df))
        results[key] = {"table": table, "rows": len(df), "skipped": False}
    return results


def source_checksums(source_dir: Path | None = None) -> dict[str, str]:
    paths = verify_source_files(source_dir or resolve_source_dir())
    return {key: file_sha256(p) for key, p in paths.items()}


if __name__ == "__main__":
    from config.database import ensure_database_exists
    ensure_database_exists()
    print(load_raw_tables())
