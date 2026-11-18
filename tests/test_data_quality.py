"""Data validation, cleaning rules and analytics-model integrity."""
from __future__ import annotations

import pandas as pd
import pytest

from src.preprocessing.data_validation import DataQualityError, build_report, run_data_quality_checks


def _check(df, table, check, column):
    row = df[(df["table"] == table) & (df["check"] == check) & (df["column"] == column)]
    assert len(row) == 1, (table, check, column)
    return row.iloc[0]


def test_sample_passes_critical_checks(raw_frames):
    checks = run_data_quality_checks(raw_frames)
    assert (checks["status"] != "FAIL").all(), checks[checks["status"] == "FAIL"]


def test_planted_issues_are_detected(raw_frames):
    checks = run_data_quality_checks(raw_frames)
    assert _check(checks, "order_items", "value_range", "price<=0")["failing_count"] == 2
    assert _check(checks, "orders", "timestamp_logic", "delivered_before_purchase")["failing_count"] == 3
    assert _check(checks, "orders", "timestamp_logic", "delivered_status_without_delivery_date")["failing_count"] == 5
    assert _check(checks, "products", "missing_category", "product_category_name")["failing_count"] > 0
    assert _check(checks, "geolocation", "duplicate_rows", "*")["failing_count"] == 25


def test_duplicate_primary_key_is_critical(raw_frames, tmp_path):
    frames = dict(raw_frames)
    frames["orders"] = pd.concat([frames["orders"], frames["orders"].head(1)], ignore_index=True)
    with pytest.raises(DataQualityError):
        build_report(frames, output_dir=tmp_path)
    assert (tmp_path / "data_quality_report.md").exists()


def test_orphan_foreign_key_detected(raw_frames):
    frames = dict(raw_frames)
    extra = frames["order_items"].head(1).copy()
    extra["product_id"] = "does-not-exist"
    extra["order_item_id"] = "99"
    frames["order_items"] = pd.concat([frames["order_items"], extra], ignore_index=True)
    checks = run_data_quality_checks(frames)
    row = _check(checks, "order_items", "foreign_key", "product_id->products.product_id")
    assert row["status"] == "FAIL" and row["failing_count"] == 1


def test_cleaning_flags_instead_of_deleting(raw_frames, cleaned):
    frames, log = cleaned
    assert len(frames["orders"]) == len(raw_frames["orders"])
    assert len(frames["order_items"]) == len(raw_frames["order_items"])
    assert frames["orders"]["has_timestamp_issue"].sum() == 8
    assert (~frames["order_items"]["is_price_valid"]).sum() == 2
    assert (frames["products"]["category_name_pt"] == "unknown").sum() == frames["products"]["is_category_missing"].sum()
    assert frames["geolocation"]["zip_code_prefix"].is_unique
    assert frames["reviews"][["review_id", "order_id"]].duplicated().sum() == 0
    rules = {e["rule"] for e in log.entries}
    assert {"exact_duplicate_rows", "price<=0", "missing_category", "outside_brazil_bbox"} <= rules
    assert all(e["reason"] for e in log.entries), "every cleaning rule must be documented"


def test_cleaned_data_types(cleaned):
    frames, _ = cleaned
    assert pd.api.types.is_datetime64_any_dtype(frames["orders"]["order_purchase_ts"])
    assert pd.api.types.is_float_dtype(frames["order_items"]["price"])
    assert frames["customers"]["zip_code_prefix"].str.len().eq(5).all()
    assert frames["customers"]["state"].str.isupper().all()


# ----------------------------- integration ---------------------------------
@pytest.mark.integration
def test_foreign_key_integrity_in_analytics(built_db):
    from config.database import read_sql
    q = {
        "items_without_order": "SELECT COUNT(*) FROM analytics.fact_order_items i LEFT JOIN analytics.fact_orders o USING (order_id) WHERE o.order_id IS NULL",
        "items_without_product": "SELECT COUNT(*) FROM analytics.fact_order_items i LEFT JOIN analytics.dim_product p USING (product_id) WHERE p.product_id IS NULL",
        "orders_without_customer": "SELECT COUNT(*) FROM analytics.fact_orders o LEFT JOIN analytics.dim_customer c USING (customer_id) WHERE c.customer_id IS NULL",
        "orders_without_date": "SELECT COUNT(*) FROM analytics.fact_orders o LEFT JOIN analytics.dim_date d ON d.date_key = o.purchase_date_key WHERE d.date_key IS NULL",
    }
    for name, sql in q.items():
        assert read_sql(sql, engine=built_db).iloc[0, 0] == 0, name


@pytest.mark.integration
def test_no_fan_out_double_counting(built_db, cleaned):
    from config.database import read_sql
    frames, _ = cleaned
    r = read_sql("""SELECT (SELECT SUM(items_value) FROM analytics.fact_orders) AS order_items_value,
                           (SELECT SUM(price) FROM analytics.fact_order_items) AS item_price,
                           (SELECT SUM(payment_value) FROM analytics.fact_orders) AS order_payments,
                           (SELECT SUM(payment_value) FROM analytics.fact_payments) AS payments""", engine=built_db).iloc[0]
    assert float(r.order_items_value) == pytest.approx(float(r.item_price))
    assert float(r.order_payments) == pytest.approx(float(r.payments))
    assert float(r.item_price) == pytest.approx(frames["order_items"]["price"].sum(), rel=1e-9)


@pytest.mark.integration
def test_transformations_are_idempotent(built_db):
    from src.transformations.build_analytics_model import run_transformations, table_counts
    tables = ["fact_orders", "fact_order_items", "fact_payments", "fact_reviews", "fact_shipments", "dim_product"]
    before = table_counts(built_db, tables)
    run_transformations(built_db)
    assert table_counts(built_db, tables) == before


@pytest.mark.integration
def test_staging_column_types(built_db):
    from config.database import read_sql
    types = read_sql("""SELECT column_name, data_type FROM information_schema.columns
                        WHERE table_schema = 'staging' AND table_name = 'orders'""", engine=built_db)
    t = dict(zip(types["column_name"], types["data_type"]))
    assert t["order_purchase_ts"].startswith("timestamp") and t["has_timestamp_issue"] == "boolean"
