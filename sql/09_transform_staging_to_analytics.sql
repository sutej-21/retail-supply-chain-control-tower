-- =====================================================================
-- 09_transform_staging_to_analytics.sql
-- Idempotent STAGING -> ANALYTICS transformation (executed every run by
-- src/transformations/build_analytics_model.py inside ONE transaction).
--   * INSERT ... ON CONFLICT DO UPDATE  => re-runs never duplicate rows
--   * Order-level measures are aggregated from items/payments FIRST,
--     then joined (prevents fan-out / double counting).
--   * Historical rows that disappeared from staging are removed from
--     the historical facts (full sync; simulated tables untouched).
-- =====================================================================

-- ---------- dim_date: 2016-01-01 .. today + 400 days ------------------
INSERT INTO analytics.dim_date (date_key, full_date, year, quarter, month, month_name, year_month,
                                iso_week, day_of_month, day_of_week, day_name, is_weekend)
SELECT TO_CHAR(d, 'YYYYMMDD')::INT, d::DATE, EXTRACT(YEAR FROM d), EXTRACT(QUARTER FROM d),
       EXTRACT(MONTH FROM d), TRIM(TO_CHAR(d, 'Month')), TO_CHAR(d, 'YYYY-MM'), EXTRACT(WEEK FROM d),
       EXTRACT(DAY FROM d), EXTRACT(ISODOW FROM d), TRIM(TO_CHAR(d, 'Day')), EXTRACT(ISODOW FROM d) IN (6, 7)
FROM generate_series(DATE '2016-01-01', CURRENT_DATE + 400, INTERVAL '1 day') AS g(d)
ON CONFLICT (date_key) DO NOTHING;

-- ---------- dim_customer (geolocation = zip-prefix centroid) ----------
INSERT INTO analytics.dim_customer (customer_id, customer_unique_id, zip_code_prefix, city, state,
                                    latitude, longitude, updated_at)
SELECT c.customer_id, c.customer_unique_id, c.zip_code_prefix, c.city, c.state, g.latitude, g.longitude,
       now() AT TIME ZONE 'utc'
FROM staging.customers c
LEFT JOIN staging.geolocation g ON g.zip_code_prefix = c.zip_code_prefix
ON CONFLICT (customer_id) DO UPDATE SET
    customer_unique_id = EXCLUDED.customer_unique_id, zip_code_prefix = EXCLUDED.zip_code_prefix,
    city = EXCLUDED.city, state = EXCLUDED.state, latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude, updated_at = EXCLUDED.updated_at;

-- ---------- dim_seller ------------------------------------------------
INSERT INTO analytics.dim_seller (seller_id, zip_code_prefix, city, state, latitude, longitude, updated_at)
SELECT s.seller_id, s.zip_code_prefix, s.city, s.state, g.latitude, g.longitude, now() AT TIME ZONE 'utc'
FROM staging.sellers s
LEFT JOIN staging.geolocation g ON g.zip_code_prefix = s.zip_code_prefix
ON CONFLICT (seller_id) DO UPDATE SET
    zip_code_prefix = EXCLUDED.zip_code_prefix, city = EXCLUDED.city, state = EXCLUDED.state,
    latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, updated_at = EXCLUDED.updated_at;

-- ---------- dim_product (+ sales attributes aggregated at product grain) ----
WITH sales AS (
    SELECT oi.product_id,
           ROUND(AVG(oi.price) FILTER (WHERE oi.is_price_valid), 2) AS avg_unit_price,
           COUNT(*) FILTER (WHERE o.order_status NOT IN ('canceled', 'unavailable')) AS units_sold_total,
           MIN(o.order_purchase_ts)::DATE AS first_sale_date,
           MAX(o.order_purchase_ts)::DATE AS last_sale_date
    FROM staging.order_items oi
    JOIN staging.orders o ON o.order_id = oi.order_id
    GROUP BY oi.product_id
)
INSERT INTO analytics.dim_product (product_id, category_name_pt, category_name_en, is_category_missing,
    name_length, description_length, photos_qty, weight_g, length_cm, height_cm, width_cm, volume_cm3,
    avg_unit_price, units_sold_total, first_sale_date, last_sale_date, updated_at)
SELECT p.product_id, p.category_name_pt, p.category_name_en, p.is_category_missing, p.name_length,
       p.description_length, p.photos_qty, p.weight_g, p.length_cm, p.height_cm, p.width_cm,
       p.length_cm * p.height_cm * p.width_cm, s.avg_unit_price, COALESCE(s.units_sold_total, 0),
       s.first_sale_date, s.last_sale_date, now() AT TIME ZONE 'utc'
FROM staging.products p
LEFT JOIN sales s ON s.product_id = p.product_id
ON CONFLICT (product_id) DO UPDATE SET
    category_name_pt = EXCLUDED.category_name_pt, category_name_en = EXCLUDED.category_name_en,
    is_category_missing = EXCLUDED.is_category_missing, name_length = EXCLUDED.name_length,
    description_length = EXCLUDED.description_length, photos_qty = EXCLUDED.photos_qty,
    weight_g = EXCLUDED.weight_g, length_cm = EXCLUDED.length_cm, height_cm = EXCLUDED.height_cm,
    width_cm = EXCLUDED.width_cm, volume_cm3 = EXCLUDED.volume_cm3, avg_unit_price = EXCLUDED.avg_unit_price,
    units_sold_total = EXCLUDED.units_sold_total, first_sale_date = EXCLUDED.first_sale_date,
    last_sale_date = EXCLUDED.last_sale_date, updated_at = EXCLUDED.updated_at;

-- ---------- remove historical facts no longer present in staging ------
DELETE FROM analytics.fact_orders f
WHERE NOT EXISTS (SELECT 1 FROM staging.orders s WHERE s.order_id = f.order_id);

-- ---------- fact_orders (order grain; pre-aggregated children) ---------
WITH items AS (
    SELECT order_id, COUNT(*) AS item_count, COUNT(DISTINCT product_id) AS distinct_products,
           COUNT(DISTINCT seller_id) AS distinct_sellers, SUM(price) AS items_value,
           SUM(freight_value) AS freight_value
    FROM staging.order_items GROUP BY order_id
),
pay AS (
    SELECT order_id, SUM(payment_value) AS payment_value, COUNT(*) AS payment_count,
           MAX(payment_installments) AS max_installments,
           (ARRAY_AGG(payment_type ORDER BY payment_value DESC, payment_sequential))[1] AS primary_payment_type
    FROM staging.payments GROUP BY order_id
),
rev AS (
    SELECT DISTINCT ON (order_id) order_id, review_score
    FROM staging.reviews
    ORDER BY order_id, review_answer_ts DESC NULLS LAST, review_creation_ts DESC NULLS LAST
),
base AS (
    SELECT o.*, c.customer_unique_id, c.state AS customer_state,
           (o.order_status NOT IN ('canceled', 'unavailable')) AS is_revenue_order,
           (o.order_status = 'delivered' AND o.order_delivered_customer_ts IS NOT NULL
               AND NOT o.has_timestamp_issue) AS is_delivered
    FROM staging.orders o
    JOIN staging.customers c ON c.customer_id = o.customer_id
)
INSERT INTO analytics.fact_orders (order_id, customer_id, customer_unique_id, customer_state, order_status,
    purchase_ts, purchase_date, purchase_date_key, approved_ts, delivered_carrier_ts, delivered_customer_ts,
    estimated_delivery_ts, item_count, distinct_products, distinct_sellers, items_value, freight_value,
    order_value, payment_value, payment_count, max_installments, primary_payment_type, is_revenue_order,
    is_delivered, delivery_days, delay_days, is_late, delivery_status, review_score, has_timestamp_issue, updated_at)
SELECT b.order_id, b.customer_id, b.customer_unique_id, b.customer_state, b.order_status,
       b.order_purchase_ts, b.order_purchase_ts::DATE, TO_CHAR(b.order_purchase_ts, 'YYYYMMDD')::INT,
       b.order_approved_ts, b.order_delivered_carrier_ts, b.order_delivered_customer_ts,
       b.order_estimated_delivery_ts,
       COALESCE(i.item_count, 0), COALESCE(i.distinct_products, 0), COALESCE(i.distinct_sellers, 0),
       COALESCE(i.items_value, 0), COALESCE(i.freight_value, 0),
       COALESCE(i.items_value, 0) + COALESCE(i.freight_value, 0),
       p.payment_value, COALESCE(p.payment_count, 0), p.max_installments, p.primary_payment_type,
       b.is_revenue_order, b.is_delivered,
       CASE WHEN b.is_delivered THEN ROUND(EXTRACT(EPOCH FROM (b.order_delivered_customer_ts - b.order_purchase_ts)) / 86400.0, 2) END,
       CASE WHEN b.is_delivered AND b.order_estimated_delivery_ts IS NOT NULL
            THEN (b.order_delivered_customer_ts::DATE - b.order_estimated_delivery_ts::DATE) END,
       CASE WHEN b.is_delivered AND b.order_estimated_delivery_ts IS NOT NULL
            THEN b.order_delivered_customer_ts::DATE > b.order_estimated_delivery_ts::DATE END,
       CASE WHEN b.order_status = 'delivered' AND b.has_timestamp_issue THEN 'invalid_timestamps'
            WHEN NOT b.is_delivered THEN 'not_delivered'
            WHEN b.order_estimated_delivery_ts IS NULL THEN 'not_delivered'
            WHEN b.order_delivered_customer_ts::DATE > b.order_estimated_delivery_ts::DATE THEN 'late'
            ELSE 'on_time' END,
       r.review_score, b.has_timestamp_issue, now() AT TIME ZONE 'utc'
FROM base b
LEFT JOIN items i ON i.order_id = b.order_id
LEFT JOIN pay p   ON p.order_id = b.order_id
LEFT JOIN rev r   ON r.order_id = b.order_id
ON CONFLICT (order_id) DO UPDATE SET
    customer_id = EXCLUDED.customer_id, customer_unique_id = EXCLUDED.customer_unique_id,
    customer_state = EXCLUDED.customer_state, order_status = EXCLUDED.order_status,
    purchase_ts = EXCLUDED.purchase_ts, purchase_date = EXCLUDED.purchase_date,
    purchase_date_key = EXCLUDED.purchase_date_key, approved_ts = EXCLUDED.approved_ts,
    delivered_carrier_ts = EXCLUDED.delivered_carrier_ts, delivered_customer_ts = EXCLUDED.delivered_customer_ts,
    estimated_delivery_ts = EXCLUDED.estimated_delivery_ts, item_count = EXCLUDED.item_count,
    distinct_products = EXCLUDED.distinct_products, distinct_sellers = EXCLUDED.distinct_sellers,
    items_value = EXCLUDED.items_value, freight_value = EXCLUDED.freight_value,
    order_value = EXCLUDED.order_value, payment_value = EXCLUDED.payment_value,
    payment_count = EXCLUDED.payment_count, max_installments = EXCLUDED.max_installments,
    primary_payment_type = EXCLUDED.primary_payment_type, is_revenue_order = EXCLUDED.is_revenue_order,
    is_delivered = EXCLUDED.is_delivered, delivery_days = EXCLUDED.delivery_days,
    delay_days = EXCLUDED.delay_days, is_late = EXCLUDED.is_late, delivery_status = EXCLUDED.delivery_status,
    review_score = EXCLUDED.review_score, has_timestamp_issue = EXCLUDED.has_timestamp_issue,
    updated_at = EXCLUDED.updated_at;

-- ---------- fact_order_items ------------------------------------------
DELETE FROM analytics.fact_order_items f
WHERE NOT EXISTS (SELECT 1 FROM staging.order_items s
                  WHERE s.order_id = f.order_id AND s.order_item_id = f.order_item_id);

INSERT INTO analytics.fact_order_items (order_id, order_item_id, product_id, seller_id, customer_id,
    purchase_date, purchase_date_key, order_status, is_revenue_order, shipping_limit_ts, price,
    freight_value, is_price_valid)
SELECT oi.order_id, oi.order_item_id, oi.product_id, oi.seller_id, f.customer_id, f.purchase_date,
       f.purchase_date_key, f.order_status, f.is_revenue_order, oi.shipping_limit_ts, oi.price,
       oi.freight_value, oi.is_price_valid
FROM staging.order_items oi
JOIN analytics.fact_orders f ON f.order_id = oi.order_id
ON CONFLICT (order_id, order_item_id) DO UPDATE SET
    product_id = EXCLUDED.product_id, seller_id = EXCLUDED.seller_id, customer_id = EXCLUDED.customer_id,
    purchase_date = EXCLUDED.purchase_date, purchase_date_key = EXCLUDED.purchase_date_key,
    order_status = EXCLUDED.order_status, is_revenue_order = EXCLUDED.is_revenue_order,
    shipping_limit_ts = EXCLUDED.shipping_limit_ts, price = EXCLUDED.price,
    freight_value = EXCLUDED.freight_value, is_price_valid = EXCLUDED.is_price_valid;

-- ---------- fact_payments ---------------------------------------------
DELETE FROM analytics.fact_payments f
WHERE NOT EXISTS (SELECT 1 FROM staging.payments s
                  WHERE s.order_id = f.order_id AND s.payment_sequential = f.payment_sequential);

INSERT INTO analytics.fact_payments (order_id, payment_sequential, payment_date_key, payment_type,
                                     payment_installments, payment_value)
SELECT p.order_id, p.payment_sequential, f.purchase_date_key, p.payment_type, p.payment_installments,
       p.payment_value
FROM staging.payments p
JOIN analytics.fact_orders f ON f.order_id = p.order_id
ON CONFLICT (order_id, payment_sequential) DO UPDATE SET
    payment_date_key = EXCLUDED.payment_date_key, payment_type = EXCLUDED.payment_type,
    payment_installments = EXCLUDED.payment_installments, payment_value = EXCLUDED.payment_value;

-- ---------- fact_reviews ----------------------------------------------
DELETE FROM analytics.fact_reviews f
WHERE NOT EXISTS (SELECT 1 FROM staging.reviews s WHERE s.review_id = f.review_id AND s.order_id = f.order_id);

INSERT INTO analytics.fact_reviews (review_id, order_id, review_score, has_comment, review_creation_ts,
    review_answer_ts, response_hours, is_latest_for_order, review_date_key)
SELECT r.review_id, r.order_id, r.review_score, r.has_comment, r.review_creation_ts, r.review_answer_ts,
       ROUND(EXTRACT(EPOCH FROM (r.review_answer_ts - r.review_creation_ts)) / 3600.0, 2),
       ROW_NUMBER() OVER (PARTITION BY r.order_id
                          ORDER BY r.review_answer_ts DESC NULLS LAST, r.review_creation_ts DESC NULLS LAST) = 1,
       d.date_key
FROM staging.reviews r
JOIN analytics.fact_orders f ON f.order_id = r.order_id
LEFT JOIN analytics.dim_date d ON d.full_date = r.review_creation_ts::DATE
ON CONFLICT (review_id, order_id) DO UPDATE SET
    review_score = EXCLUDED.review_score, has_comment = EXCLUDED.has_comment,
    review_creation_ts = EXCLUDED.review_creation_ts, review_answer_ts = EXCLUDED.review_answer_ts,
    response_hours = EXCLUDED.response_hours, is_latest_for_order = EXCLUDED.is_latest_for_order,
    review_date_key = EXCLUDED.review_date_key;

-- ---------- fact_shipments: historical OUTBOUND (order x seller grain) ----
DELETE FROM analytics.fact_shipments f
WHERE f.shipment_type = 'OUTBOUND_CUSTOMER'
  AND NOT EXISTS (SELECT 1 FROM staging.order_items s
                  WHERE s.order_id = f.order_id AND s.seller_id = f.seller_id);

WITH os AS (
    SELECT order_id, seller_id, COUNT(*) AS quantity, MAX(shipping_limit_ts) AS shipping_limit_ts
    FROM staging.order_items GROUP BY order_id, seller_id
)
INSERT INTO analytics.fact_shipments (shipment_id, shipment_type, order_id, seller_id, origin_state,
    destination_state, quantity, shipping_limit_ts, shipped_ts, delivered_ts, estimated_ts, delivery_days,
    delay_days, is_late, shipment_status, data_source, is_simulated)
SELECT MD5(os.order_id || '|' || os.seller_id), 'OUTBOUND_CUSTOMER', os.order_id, os.seller_id, s.state,
       f.customer_state, os.quantity, os.shipping_limit_ts, f.delivered_carrier_ts, f.delivered_customer_ts,
       f.estimated_delivery_ts, f.delivery_days, f.delay_days, f.is_late,
       CASE WHEN f.is_delivered THEN 'DELIVERED'
            WHEN f.order_status IN ('canceled', 'unavailable') THEN 'CANCELED'
            WHEN f.delivered_carrier_ts IS NOT NULL THEN 'SHIPPED_NOT_CONFIRMED'
            ELSE 'NOT_SHIPPED' END,
       'OLIST_HISTORICAL', FALSE
FROM os
JOIN analytics.fact_orders f ON f.order_id = os.order_id
JOIN analytics.dim_seller s ON s.seller_id = os.seller_id
ON CONFLICT (shipment_id) DO UPDATE SET
    quantity = EXCLUDED.quantity, shipping_limit_ts = EXCLUDED.shipping_limit_ts,
    shipped_ts = EXCLUDED.shipped_ts, delivered_ts = EXCLUDED.delivered_ts, estimated_ts = EXCLUDED.estimated_ts,
    delivery_days = EXCLUDED.delivery_days, delay_days = EXCLUDED.delay_days, is_late = EXCLUDED.is_late,
    shipment_status = EXCLUDED.shipment_status, origin_state = EXCLUDED.origin_state,
    destination_state = EXCLUDED.destination_state;
