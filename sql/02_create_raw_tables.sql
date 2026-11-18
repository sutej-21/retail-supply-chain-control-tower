-- =====================================================================
-- 02_create_raw_tables.sql  -  RAW layer
-- Faithful copy of the nine Olist CSV files: every source column is TEXT,
-- plus lineage columns. Loaded by src/ingestion/load_data.py
-- (TRUNCATE + COPY in one transaction => idempotent).
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS analytics;

COMMENT ON SCHEMA raw       IS 'Original Olist CSV data, all columns TEXT, minimal modification';
COMMENT ON SCHEMA staging   IS 'Cleaned, typed and standardised Olist data';
COMMENT ON SCHEMA analytics IS 'Star schema, KPI views, ML outputs. Tables flagged is_simulated contain SYNTHETIC data';

CREATE TABLE IF NOT EXISTS raw.load_audit (
    audit_id     BIGSERIAL PRIMARY KEY,
    batch_id     TEXT        NOT NULL,
    table_name   TEXT        NOT NULL,
    source_file  TEXT        NOT NULL,
    sha256       TEXT        NOT NULL,
    row_count    BIGINT      NOT NULL,
    status       TEXT        NOT NULL,
    loaded_at    TIMESTAMP   NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE TABLE IF NOT EXISTS raw.olist_orders (
    order_id TEXT, customer_id TEXT, order_status TEXT, order_purchase_timestamp TEXT,
    order_approved_at TEXT, order_delivered_carrier_date TEXT, order_delivered_customer_date TEXT,
    order_estimated_delivery_date TEXT,
    _batch_id TEXT, _source_file TEXT, _loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.olist_order_items (
    order_id TEXT, order_item_id TEXT, product_id TEXT, seller_id TEXT, shipping_limit_date TEXT,
    price TEXT, freight_value TEXT,
    _batch_id TEXT, _source_file TEXT, _loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.olist_customers (
    customer_id TEXT, customer_unique_id TEXT, customer_zip_code_prefix TEXT, customer_city TEXT,
    customer_state TEXT,
    _batch_id TEXT, _source_file TEXT, _loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.olist_products (
    product_id TEXT, product_category_name TEXT, product_name_lenght TEXT, product_description_lenght TEXT,
    product_photos_qty TEXT, product_weight_g TEXT, product_length_cm TEXT, product_height_cm TEXT,
    product_width_cm TEXT,
    _batch_id TEXT, _source_file TEXT, _loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.olist_sellers (
    seller_id TEXT, seller_zip_code_prefix TEXT, seller_city TEXT, seller_state TEXT,
    _batch_id TEXT, _source_file TEXT, _loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.olist_payments (
    order_id TEXT, payment_sequential TEXT, payment_type TEXT, payment_installments TEXT, payment_value TEXT,
    _batch_id TEXT, _source_file TEXT, _loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.olist_reviews (
    review_id TEXT, order_id TEXT, review_score TEXT, review_comment_title TEXT, review_comment_message TEXT,
    review_creation_date TEXT, review_answer_timestamp TEXT,
    _batch_id TEXT, _source_file TEXT, _loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.olist_geolocation (
    geolocation_zip_code_prefix TEXT, geolocation_lat TEXT, geolocation_lng TEXT, geolocation_city TEXT,
    geolocation_state TEXT,
    _batch_id TEXT, _source_file TEXT, _loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS raw.olist_category_translation (
    product_category_name TEXT, product_category_name_english TEXT,
    _batch_id TEXT, _source_file TEXT, _loaded_at TIMESTAMP
);
