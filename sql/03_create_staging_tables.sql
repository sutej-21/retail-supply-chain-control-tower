-- =====================================================================
-- 03_create_staging_tables.sql  -  STAGING layer
-- Typed, cleaned Olist data produced by src/preprocessing/clean_data.py.
-- Records are FLAGGED, not deleted (see reports/data_quality/cleaning_log.md).
-- Reloaded with TRUNCATE + COPY (idempotent).
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS staging;

CREATE TABLE IF NOT EXISTS staging.orders (
    order_id                      TEXT PRIMARY KEY,
    customer_id                   TEXT NOT NULL,
    order_status                  TEXT NOT NULL,
    order_purchase_ts             TIMESTAMP NOT NULL,
    order_approved_ts             TIMESTAMP,
    order_delivered_carrier_ts    TIMESTAMP,
    order_delivered_customer_ts   TIMESTAMP,
    order_estimated_delivery_ts   TIMESTAMP,
    has_timestamp_issue           BOOLEAN NOT NULL DEFAULT FALSE,
    timestamp_issue               TEXT
);

CREATE TABLE IF NOT EXISTS staging.order_items (
    order_id          TEXT NOT NULL,
    order_item_id     INTEGER NOT NULL,
    product_id        TEXT NOT NULL,
    seller_id         TEXT NOT NULL,
    shipping_limit_ts TIMESTAMP,
    price             NUMERIC(12,2) NOT NULL,
    freight_value     NUMERIC(12,2) NOT NULL,
    is_price_valid    BOOLEAN NOT NULL,
    PRIMARY KEY (order_id, order_item_id)
);

CREATE TABLE IF NOT EXISTS staging.customers (
    customer_id        TEXT PRIMARY KEY,
    customer_unique_id TEXT NOT NULL,
    zip_code_prefix    CHAR(5),
    city               TEXT,
    state              CHAR(2)
);

CREATE TABLE IF NOT EXISTS staging.products (
    product_id          TEXT PRIMARY KEY,
    category_name_pt    TEXT NOT NULL,
    category_name_en    TEXT NOT NULL,
    is_category_missing BOOLEAN NOT NULL,
    name_length         NUMERIC,
    description_length  NUMERIC,
    photos_qty          NUMERIC,
    weight_g            NUMERIC,
    length_cm           NUMERIC,
    height_cm           NUMERIC,
    width_cm            NUMERIC
);

CREATE TABLE IF NOT EXISTS staging.sellers (
    seller_id       TEXT PRIMARY KEY,
    zip_code_prefix CHAR(5),
    city            TEXT,
    state           CHAR(2)
);

CREATE TABLE IF NOT EXISTS staging.payments (
    order_id             TEXT NOT NULL,
    payment_sequential   INTEGER NOT NULL,
    payment_type         TEXT NOT NULL,
    payment_installments INTEGER NOT NULL,
    payment_value        NUMERIC(12,2) NOT NULL,
    PRIMARY KEY (order_id, payment_sequential)
);

CREATE TABLE IF NOT EXISTS staging.reviews (
    review_id              TEXT NOT NULL,
    order_id               TEXT NOT NULL,
    review_score           SMALLINT NOT NULL CHECK (review_score BETWEEN 1 AND 5),
    review_comment_title   TEXT,
    review_comment_message TEXT,
    review_creation_ts     TIMESTAMP,
    review_answer_ts       TIMESTAMP,
    has_comment            BOOLEAN NOT NULL,
    PRIMARY KEY (review_id, order_id)
);

CREATE TABLE IF NOT EXISTS staging.geolocation (
    zip_code_prefix CHAR(5) PRIMARY KEY,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    city            TEXT,
    state           CHAR(2),
    n_points        INTEGER
);

CREATE TABLE IF NOT EXISTS staging.category_translation (
    category_name_pt TEXT PRIMARY KEY,
    category_name_en TEXT NOT NULL
);
