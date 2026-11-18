-- =====================================================================
-- 06_create_analytics_views.sql  -  KPI & dashboard views
-- Conventions
--   * Revenue = SUM(item price) of orders whose status is NOT canceled /
--     unavailable (freight reported separately). Payment value is NOT used
--     for revenue because it includes vouchers/interest.
--   * "Recent" windows for historical data are anchored on the LAST date in
--     the Olist data (vw_data_window.max_date), never on today's date.
--   * Views prefixed with *sim* or containing is_simulated columns expose
--     SYNTHETIC operational data and are labelled as such in Superset.
-- All views are CREATE OR REPLACE => re-runnable.
-- =====================================================================

CREATE OR REPLACE VIEW analytics.vw_data_window AS
SELECT MIN(purchase_date) AS min_date, MAX(purchase_date) AS max_date,
       MAX(purchase_date) - MIN(purchase_date) + 1 AS n_days
FROM analytics.fact_orders;

-- ------------------------------ SALES --------------------------------
CREATE OR REPLACE VIEW analytics.vw_sales_items_enriched AS
SELECT oi.order_id, oi.order_item_id, oi.purchase_date, d.year_month, d.day_name, oi.order_status,
       oi.is_revenue_order, oi.product_id, p.category_name_en AS product_category, oi.seller_id,
       s.state AS seller_state, o.customer_state, o.delivery_status, o.review_score,
       oi.price, oi.freight_value, 1 AS units
FROM analytics.fact_order_items oi
JOIN analytics.fact_orders o  ON o.order_id = oi.order_id
JOIN analytics.dim_product p  ON p.product_id = oi.product_id
JOIN analytics.dim_seller s   ON s.seller_id = oi.seller_id
JOIN analytics.dim_date d     ON d.date_key = oi.purchase_date_key;

CREATE OR REPLACE VIEW analytics.vw_daily_sales AS
SELECT d.full_date AS sales_date,
       COUNT(DISTINCT o.order_id) FILTER (WHERE o.is_revenue_order)               AS orders,
       COALESCE(SUM(o.items_value) FILTER (WHERE o.is_revenue_order), 0)          AS revenue,
       COALESCE(SUM(o.freight_value) FILTER (WHERE o.is_revenue_order), 0)        AS freight,
       COALESCE(SUM(o.item_count) FILTER (WHERE o.is_revenue_order), 0)           AS units,
       ROUND(COALESCE(SUM(o.items_value) FILTER (WHERE o.is_revenue_order), 0)
             / NULLIF(COUNT(DISTINCT o.order_id) FILTER (WHERE o.is_revenue_order), 0), 2) AS avg_order_value
FROM analytics.dim_date d
JOIN analytics.vw_data_window w ON d.full_date BETWEEN w.min_date AND w.max_date
LEFT JOIN analytics.fact_orders o ON o.purchase_date = d.full_date
GROUP BY d.full_date;

CREATE OR REPLACE VIEW analytics.vw_monthly_sales AS
SELECT DATE_TRUNC('month', sales_date)::DATE AS month, SUM(orders) AS orders, SUM(revenue) AS revenue,
       SUM(units) AS units, ROUND(SUM(revenue) / NULLIF(SUM(orders), 0), 2) AS avg_order_value
FROM analytics.vw_daily_sales GROUP BY 1;

CREATE OR REPLACE VIEW analytics.vw_category_demand AS
SELECT p.category_name_en AS product_category, DATE_TRUNC('month', oi.purchase_date)::DATE AS month,
       COUNT(*) AS units, SUM(oi.price) AS revenue, COUNT(DISTINCT oi.order_id) AS orders
FROM analytics.fact_order_items oi
JOIN analytics.dim_product p ON p.product_id = oi.product_id
WHERE oi.is_revenue_order
GROUP BY 1, 2;

-- Demand growth: last 30 days vs previous 30 days (anchored on data end)
CREATE OR REPLACE VIEW analytics.vw_product_demand_growth AS
WITH w AS (SELECT max_date FROM analytics.vw_data_window),
agg AS (
    SELECT oi.product_id,
           COUNT(*) FILTER (WHERE oi.purchase_date >  w.max_date - 30)                                AS units_last_30d,
           COUNT(*) FILTER (WHERE oi.purchase_date <= w.max_date - 30 AND oi.purchase_date > w.max_date - 60) AS units_prev_30d,
           SUM(oi.price) FILTER (WHERE oi.purchase_date > w.max_date - 30)                            AS revenue_last_30d
    FROM analytics.fact_order_items oi CROSS JOIN w
    WHERE oi.is_revenue_order AND oi.purchase_date > w.max_date - 60
    GROUP BY oi.product_id
)
SELECT a.product_id, p.category_name_en AS product_category, a.units_last_30d, a.units_prev_30d,
       COALESCE(a.revenue_last_30d, 0) AS revenue_last_30d,
       ROUND(100.0 * (a.units_last_30d - a.units_prev_30d) / NULLIF(a.units_prev_30d, 0), 1) AS demand_growth_pct
FROM agg a JOIN analytics.dim_product p ON p.product_id = a.product_id;

CREATE OR REPLACE VIEW analytics.vw_category_demand_growth AS
WITH w AS (SELECT max_date FROM analytics.vw_data_window)
SELECT p.category_name_en AS product_category,
       COUNT(*) FILTER (WHERE oi.purchase_date >  w.max_date - 30) AS units_last_30d,
       COUNT(*) FILTER (WHERE oi.purchase_date <= w.max_date - 30) AS units_prev_30d,
       ROUND(100.0 * (COUNT(*) FILTER (WHERE oi.purchase_date > w.max_date - 30)
                      - COUNT(*) FILTER (WHERE oi.purchase_date <= w.max_date - 30))
             / NULLIF(COUNT(*) FILTER (WHERE oi.purchase_date <= w.max_date - 30), 0), 1) AS demand_growth_pct
FROM analytics.fact_order_items oi
JOIN analytics.dim_product p ON p.product_id = oi.product_id
CROSS JOIN w
WHERE oi.is_revenue_order AND oi.purchase_date > w.max_date - 60
GROUP BY p.category_name_en;

CREATE OR REPLACE VIEW analytics.vw_top_products AS
SELECT oi.product_id, p.category_name_en AS product_category, COUNT(*) AS units_sold,
       SUM(oi.price) AS revenue, COUNT(DISTINCT oi.order_id) AS orders,
       RANK() OVER (ORDER BY COUNT(*) DESC) AS units_rank,
       RANK() OVER (ORDER BY SUM(oi.price) DESC) AS revenue_rank
FROM analytics.fact_order_items oi
JOIN analytics.dim_product p ON p.product_id = oi.product_id
WHERE oi.is_revenue_order
GROUP BY oi.product_id, p.category_name_en;

-- ----------------------------- DELIVERY ------------------------------
CREATE OR REPLACE VIEW analytics.vw_delivery_kpis AS
SELECT COUNT(*) FILTER (WHERE is_delivered)                                         AS delivered_orders,
       COUNT(*) FILTER (WHERE is_late)                                              AS late_orders,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_delivered AND NOT is_late)
             / NULLIF(COUNT(*) FILTER (WHERE is_delivered AND is_late IS NOT NULL), 0), 2) AS on_time_delivery_rate_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_late)
             / NULLIF(COUNT(*) FILTER (WHERE is_delivered AND is_late IS NOT NULL), 0), 2) AS late_delivery_rate_pct,
       ROUND(AVG(delivery_days) FILTER (WHERE is_delivered), 2)                       AS avg_delivery_days,
       ROUND(AVG(delay_days) FILTER (WHERE is_late), 2)                               AS avg_delay_days_late_orders,
       COUNT(*) FILTER (WHERE order_status = 'shipped')                               AS historical_shipped_not_delivered
FROM analytics.fact_orders;

CREATE OR REPLACE VIEW analytics.vw_delivery_daily AS
SELECT purchase_date, customer_state,
       COUNT(*) FILTER (WHERE is_delivered) AS delivered_orders,
       COUNT(*) FILTER (WHERE is_late)      AS late_orders,
       ROUND(AVG(delivery_days) FILTER (WHERE is_delivered), 2) AS avg_delivery_days,
       ROUND(AVG(delay_days) FILTER (WHERE is_delivered), 2) AS avg_delay_days
FROM analytics.fact_orders
GROUP BY purchase_date, customer_state;

CREATE OR REPLACE VIEW analytics.vw_delivery_by_state AS
SELECT customer_state,
       COUNT(*) FILTER (WHERE is_delivered) AS delivered_orders,
       COUNT(*) FILTER (WHERE is_late)      AS late_orders,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_delivered AND NOT is_late)
             / NULLIF(COUNT(*) FILTER (WHERE is_delivered AND is_late IS NOT NULL), 0), 2) AS on_time_rate_pct,
       ROUND(AVG(delivery_days) FILTER (WHERE is_delivered), 2) AS avg_delivery_days,
       ROUND(AVG(delay_days) FILTER (WHERE is_late), 2)        AS avg_delay_days_late_orders
FROM analytics.fact_orders
GROUP BY customer_state;

-- Seller performance (shipment grain = order x seller; no fan-out)
CREATE OR REPLACE VIEW analytics.vw_seller_performance AS
WITH s AS (
    SELECT sh.seller_id,
           COUNT(*) AS shipments,
           COUNT(*) FILTER (WHERE sh.shipment_status = 'DELIVERED') AS delivered,
           COUNT(*) FILTER (WHERE sh.is_late) AS late,
           AVG(sh.delivery_days) FILTER (WHERE sh.shipment_status = 'DELIVERED') AS avg_delivery_days,
           AVG(sh.delay_days) FILTER (WHERE sh.is_late) AS avg_delay_days,
           COUNT(*) FILTER (WHERE sh.shipped_ts > sh.shipping_limit_ts) AS late_handover_to_carrier
    FROM analytics.fact_shipments sh
    WHERE sh.shipment_type = 'OUTBOUND_CUSTOMER'
    GROUP BY sh.seller_id
),
rev AS (
    SELECT seller_id, SUM(price) AS revenue, COUNT(*) AS units
    FROM analytics.fact_order_items WHERE is_revenue_order GROUP BY seller_id
),
rs AS (
    SELECT x.seller_id, AVG(o.review_score) AS avg_review_score
    FROM (SELECT DISTINCT order_id, seller_id FROM analytics.fact_order_items) x
    JOIN analytics.fact_orders o ON o.order_id = x.order_id
    GROUP BY x.seller_id
)
SELECT s.seller_id, ds.state AS seller_state, s.shipments, s.delivered, s.late,
       ROUND(100.0 * (s.delivered - s.late) / NULLIF(s.delivered, 0), 2) AS on_time_rate_pct,
       ROUND(s.avg_delivery_days, 2) AS avg_delivery_days, ROUND(s.avg_delay_days, 2) AS avg_delay_days_late,
       s.late_handover_to_carrier, COALESCE(rev.revenue, 0) AS revenue, COALESCE(rev.units, 0) AS units,
       ROUND(rs.avg_review_score, 2) AS avg_review_score,
       CASE WHEN s.delivered >= 10 THEN
            RANK() OVER (PARTITION BY (s.delivered >= 10)
                         ORDER BY (s.delivered - s.late)::NUMERIC / NULLIF(s.delivered, 0) DESC, s.avg_delivery_days)
       END AS performance_rank           -- ranked only with >= 10 deliveries
FROM s
JOIN analytics.dim_seller ds ON ds.seller_id = s.seller_id
LEFT JOIN rev ON rev.seller_id = s.seller_id
LEFT JOIN rs  ON rs.seller_id  = s.seller_id;

-- ----------------------------- CUSTOMER ------------------------------
CREATE OR REPLACE VIEW analytics.vw_review_by_delivery_status AS
SELECT delivery_status, COUNT(*) AS orders_with_review, ROUND(AVG(review_score), 3) AS avg_review_score,
       ROUND(100.0 * COUNT(*) FILTER (WHERE review_score <= 2) / NULLIF(COUNT(*), 0), 2) AS low_rating_pct
FROM analytics.fact_orders
WHERE review_score IS NOT NULL
GROUP BY delivery_status;

CREATE OR REPLACE VIEW analytics.vw_review_by_delay_bucket AS
SELECT CASE WHEN delay_days IS NULL THEN 'not delivered'
            WHEN delay_days <= -7 THEN '7+ days early'
            WHEN delay_days <= 0  THEN '0-6 days early'
            WHEN delay_days <= 3  THEN '1-3 days late'
            WHEN delay_days <= 7  THEN '4-7 days late'
            ELSE '8+ days late' END AS delay_bucket,
       MIN(COALESCE(delay_days, 9999)) AS sort_key,
       COUNT(*) AS orders, ROUND(AVG(review_score), 3) AS avg_review_score
FROM analytics.fact_orders WHERE review_score IS NOT NULL
GROUP BY 1;

CREATE OR REPLACE VIEW analytics.vw_satisfaction_trend AS
SELECT DATE_TRUNC('month', purchase_date)::DATE AS month, ROUND(AVG(review_score), 3) AS avg_review_score,
       COUNT(*) FILTER (WHERE review_score <= 2) AS low_rating_orders, COUNT(review_score) AS reviewed_orders
FROM analytics.fact_orders GROUP BY 1;

CREATE OR REPLACE VIEW analytics.vw_customer_kpis AS
WITH per_person AS (
    SELECT customer_unique_id, COUNT(DISTINCT order_id) AS n_orders
    FROM analytics.fact_orders WHERE is_revenue_order GROUP BY customer_unique_id
)
SELECT COUNT(*) AS unique_customers,
       COUNT(*) FILTER (WHERE n_orders > 1) AS repeat_customers,
       ROUND(100.0 * COUNT(*) FILTER (WHERE n_orders > 1) / NULLIF(COUNT(*), 0), 2) AS repeat_customer_rate_pct,
       (SELECT ROUND(AVG(review_score), 3) FROM analytics.fact_orders WHERE review_score IS NOT NULL) AS avg_review_score,
       (SELECT COUNT(*) FROM analytics.fact_orders WHERE review_score <= 2) AS low_rating_orders,
       (SELECT COUNT(*) FROM analytics.fact_reviews WHERE is_latest_for_order AND review_score <= 2 AND has_comment)
           AS complaints_with_comment
FROM per_person;

CREATE OR REPLACE VIEW analytics.vw_low_rating_orders AS
SELECT o.order_id, o.purchase_date, o.customer_state, o.order_status, o.delivery_status, o.delay_days,
       o.review_score, r.has_comment, o.order_value
FROM analytics.fact_orders o
LEFT JOIN analytics.fact_reviews r ON r.order_id = o.order_id AND r.is_latest_for_order
WHERE o.review_score <= 2;

-- ---------------------- INVENTORY (SIMULATED) ------------------------
CREATE OR REPLACE VIEW analytics.vw_current_inventory AS
SELECT t.product_id, p.category_name_en AS product_category, t.warehouse_id, w.state AS warehouse_state,
       t.supplier_id, t.available_stock, t.reserved_stock, t.on_order_qty, t.net_available, t.safety_stock,
       t.reorder_point, t.reorder_qty, t.maximum_stock, t.planning_daily_demand, t.days_of_cover,
       t.lead_time_days, t.projected_stockout_ts, t.forecast_demand_lead_time, t.risk_status,
       CASE t.risk_status WHEN 'Critical' THEN 1 WHEN 'High Risk' THEN 2 WHEN 'Warning' THEN 3 ELSE 4 END AS risk_rank,
       t.recommended_action, b.unit_cost_proxy,
       ROUND(t.available_stock * b.unit_cost_proxy, 2) AS inventory_value_proxy,
       p.avg_unit_price, t.inventory_timestamp, t.computed_at, TRUE AS is_simulated
FROM analytics.inventory_thresholds t
JOIN analytics.dim_product p ON p.product_id = t.product_id
JOIN analytics.dim_warehouse w ON w.warehouse_id = t.warehouse_id
LEFT JOIN analytics.bridge_product_supplier b ON b.product_id = t.product_id;

CREATE OR REPLACE VIEW analytics.vw_inventory_kpis AS
WITH cons AS (
    SELECT -SUM(quantity) AS units_consumed_30d
    FROM analytics.fact_inventory_movements
    WHERE movement_type = 'SALE_CONSUMPTION'
      AND movement_timestamp > (SELECT MAX(movement_timestamp) FROM analytics.fact_inventory_movements) - INTERVAL '30 days'
),
avg_inv AS (
    SELECT AVG(total) AS avg_inventory_units FROM (
        SELECT inventory_timestamp, SUM(available_stock) AS total FROM analytics.fact_inventory
        WHERE inventory_timestamp > (SELECT MAX(inventory_timestamp) FROM analytics.fact_inventory) - INTERVAL '30 days'
        GROUP BY inventory_timestamp) x
)
SELECT COUNT(*) AS sku_locations,
       SUM(available_stock) AS total_available_units,
       SUM(reserved_stock) AS total_reserved_units,
       ROUND(SUM(inventory_value_proxy), 2) AS inventory_value_proxy,
       COUNT(*) FILTER (WHERE risk_status IN ('Critical', 'High Risk', 'Warning')) AS products_at_risk,
       COUNT(*) FILTER (WHERE risk_status = 'Critical') AS critical_products,
       COUNT(*) FILTER (WHERE net_available <= reorder_point) AS low_stock_products,
       ROUND(100.0 * COUNT(*) FILTER (WHERE risk_status IN ('Critical', 'High Risk')) / NULLIF(COUNT(*), 0), 2)
           AS stockout_risk_pct,
       ROUND(SUM(net_available)::NUMERIC / NULLIF(SUM(planning_daily_demand), 0), 1) AS days_of_inventory_on_hand,
       SUM(reorder_qty) AS total_recommended_reorder_units,
       ROUND(SUM(GREATEST(available_stock - reorder_point, 0) * unit_cost_proxy), 2) AS holding_exposure_proxy,
       ROUND((SELECT units_consumed_30d FROM cons) / NULLIF((SELECT avg_inventory_units FROM avg_inv), 0), 3)
           AS inventory_turnover_30d,
       TRUE AS is_simulated
FROM analytics.vw_current_inventory;

CREATE OR REPLACE VIEW analytics.vw_inventory_by_category AS
SELECT product_category, risk_status, COUNT(*) AS sku_locations, SUM(available_stock) AS available_units,
       SUM(reorder_point) AS reorder_point_units, ROUND(SUM(inventory_value_proxy), 2) AS inventory_value_proxy,
       TRUE AS is_simulated
FROM analytics.vw_current_inventory GROUP BY product_category, risk_status;

CREATE OR REPLACE VIEW analytics.vw_inventory_trend AS
SELECT i.inventory_timestamp, p.category_name_en AS product_category,
       SUM(i.available_stock) AS available_units, SUM(i.reserved_stock) AS reserved_units,
       SUM(i.reorder_level) AS reorder_level_units, TRUE AS is_simulated
FROM analytics.fact_inventory i
JOIN analytics.dim_product p ON p.product_id = i.product_id
GROUP BY i.inventory_timestamp, p.category_name_en;

CREATE OR REPLACE VIEW analytics.vw_inventory_consumption_daily AS
SELECT DATE_TRUNC('day', m.movement_timestamp)::DATE AS movement_date, p.category_name_en AS product_category,
       m.movement_type, SUM(ABS(m.quantity)) AS units, TRUE AS is_simulated
FROM analytics.fact_inventory_movements m
JOIN analytics.dim_product p ON p.product_id = m.product_id
GROUP BY 1, 2, 3;

-- Forecast demand (next 30 days, latest origin) vs available inventory
CREATE OR REPLACE VIEW analytics.vw_forecast_vs_inventory AS
WITH latest AS (
    SELECT model_level, entity_id, MAX(forecast_origin_date) AS origin
    FROM analytics.fact_demand_forecast WHERE model_level = 'product' GROUP BY 1, 2
),
f AS (
    SELECT fc.entity_id AS product_id, SUM(fc.yhat) AS forecast_30d, SUM(fc.yhat_upper) AS forecast_30d_upper
    FROM analytics.fact_demand_forecast fc
    JOIN latest l ON l.model_level = fc.model_level AND l.entity_id = fc.entity_id AND l.origin = fc.forecast_origin_date
    WHERE fc.horizon_day <= 30
    GROUP BY fc.entity_id
)
SELECT c.product_id, c.product_category, c.warehouse_id, c.net_available,
       COALESCE(ROUND(f.forecast_30d, 1), ROUND(c.planning_daily_demand * 30, 1)) AS demand_next_30d,
       CASE WHEN f.product_id IS NOT NULL THEN 'prophet' ELSE 'historical_avg' END AS demand_source,
       c.net_available - COALESCE(f.forecast_30d, c.planning_daily_demand * 30) AS surplus_or_gap,
       c.risk_status, TRUE AS is_simulated
FROM analytics.vw_current_inventory c
LEFT JOIN f ON f.product_id = c.product_id;

-- ---------------------- SUPPLIERS (SIMULATED) ------------------------
CREATE OR REPLACE VIEW analytics.vw_supplier_kpis AS
SELECT s.supplier_id, s.supplier_name, s.supplier_state, s.supplier_status, s.average_lead_time_days,
       COUNT(f.po_id) AS purchase_orders,
       COUNT(f.po_id) FILTER (WHERE f.status = 'IN_TRANSIT') AS open_purchase_orders,
       COUNT(f.po_id) FILTER (WHERE f.status = 'RECEIVED') AS received_orders,
       ROUND(AVG(f.simulated_lead_time_days) FILTER (WHERE f.status = 'RECEIVED'), 2) AS avg_actual_lead_time_days,
       ROUND(100.0 * COUNT(*) FILTER (WHERE f.is_late) / NULLIF(COUNT(*) FILTER (WHERE f.status = 'RECEIVED'), 0), 2)
           AS delay_rate_pct,
       ROUND(AVG(f.delay_days) FILTER (WHERE f.is_late), 2) AS avg_delay_days_late,
       ROUND(100.0 * COUNT(*) FILTER (WHERE f.status = 'RECEIVED' AND NOT f.is_late)
             / NULLIF(COUNT(*) FILTER (WHERE f.status = 'RECEIVED'), 0), 2) AS reliability_score_pct,
       RANK() OVER (ORDER BY COUNT(*) FILTER (WHERE f.status = 'RECEIVED' AND NOT f.is_late)::NUMERIC
                    / NULLIF(COUNT(*) FILTER (WHERE f.status = 'RECEIVED'), 0) DESC NULLS LAST,
                    AVG(f.simulated_lead_time_days) FILTER (WHERE f.status = 'RECEIVED') ASC NULLS LAST)
           AS performance_rank,
       (SELECT COUNT(*) FROM analytics.inventory_thresholds t
         WHERE t.supplier_id = s.supplier_id AND t.risk_status IN ('Critical', 'High Risk')) AS skus_at_replenishment_risk,
       TRUE AS is_simulated
FROM analytics.dim_supplier s
LEFT JOIN analytics.fact_supplier_performance f ON f.supplier_id = s.supplier_id
GROUP BY s.supplier_id, s.supplier_name, s.supplier_state, s.supplier_status, s.average_lead_time_days;

-- ----------------------------- FORECAST ------------------------------
CREATE OR REPLACE VIEW analytics.vw_forecast_latest AS
WITH latest AS (
    SELECT model_level, entity_id, MAX(forecast_origin_date) AS origin
    FROM analytics.fact_demand_forecast GROUP BY 1, 2
)
SELECT f.model_level, f.entity_id, COALESCE(f.entity_name, f.entity_id) AS entity_name,
       f.forecast_origin_date, f.forecast_date, f.horizon_day, f.yhat, f.yhat_lower, f.yhat_upper,
       CASE WHEN f.horizon_day <= 7 THEN '7d' WHEN f.horizon_day <= 30 THEN '30d' ELSE '60d' END AS horizon_bucket
FROM analytics.fact_demand_forecast f
JOIN latest l ON l.model_level = f.model_level AND l.entity_id = f.entity_id AND l.origin = f.forecast_origin_date;

-- Actual daily units per level/entity (history) UNION latest forecast
CREATE OR REPLACE VIEW analytics.vw_demand_actual_vs_forecast AS
WITH actual_overall AS (
    SELECT 'overall'::TEXT AS model_level, 'ALL'::TEXT AS entity_id, 'All products'::TEXT AS entity_name,
           purchase_date AS ds, COUNT(*)::NUMERIC AS units
    FROM analytics.fact_order_items WHERE is_revenue_order GROUP BY purchase_date
),
actual_cat AS (
    SELECT 'category', p.category_name_en, p.category_name_en, oi.purchase_date, COUNT(*)::NUMERIC
    FROM analytics.fact_order_items oi JOIN analytics.dim_product p ON p.product_id = oi.product_id
    WHERE oi.is_revenue_order
      AND p.category_name_en IN (SELECT DISTINCT entity_id FROM analytics.fact_demand_forecast WHERE model_level = 'category')
    GROUP BY 2, 3, 4
),
actual_prod AS (
    SELECT 'product', oi.product_id, oi.product_id, oi.purchase_date, COUNT(*)::NUMERIC
    FROM analytics.fact_order_items oi
    WHERE oi.is_revenue_order
      AND oi.product_id IN (SELECT DISTINCT entity_id FROM analytics.fact_demand_forecast WHERE model_level = 'product')
    GROUP BY 2, 3, 4
)
SELECT model_level, entity_id, entity_name, ds, units AS actual_units, NULL::NUMERIC AS forecast_units,
       NULL::NUMERIC AS forecast_lower, NULL::NUMERIC AS forecast_upper, 'actual' AS series
FROM (SELECT * FROM actual_overall UNION ALL SELECT * FROM actual_cat UNION ALL SELECT * FROM actual_prod) a
UNION ALL
SELECT model_level, entity_id, entity_name, forecast_date, NULL, yhat, yhat_lower, yhat_upper, 'forecast'
FROM analytics.vw_forecast_latest;

-- ------------------------------ ALERTS -------------------------------
CREATE OR REPLACE VIEW analytics.vw_active_alerts AS
SELECT a.alert_id, a.alert_type, a.severity,
       CASE a.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END AS severity_rank,
       a.entity_type, a.entity_id, a.product_id, p.category_name_en AS product_category, a.title, a.explanation,
       a.recommended_action, a.occurrences, a.created_at, a.last_seen_at, a.notified, a.is_simulated_input
FROM analytics.alert_history a
LEFT JOIN analytics.dim_product p ON p.product_id = a.product_id
WHERE a.status = 'ACTIVE';

CREATE OR REPLACE VIEW analytics.vw_scenario_summary AS
SELECT s.scenario_id, s.scenario_name, s.supplier_id, s.supplier_delay_days, s.demand_growth_pct,
       s.created_at, r.variant,
       COUNT(*) FILTER (WHERE r.stockout_date IS NOT NULL) AS products_stocking_out,
       COUNT(*) FILTER (WHERE r.is_affected) AS products_affected,
       ROUND(SUM(r.unfulfilled_units), 1) AS unfulfilled_units,
       ROUND(SUM(r.revenue_exposure_proxy), 2) AS revenue_exposure_proxy,
       ROUND(SUM(r.additional_units_required), 1) AS additional_units_required,
       TRUE AS is_simulated
FROM analytics.simulation_scenarios s
JOIN analytics.scenario_results r ON r.scenario_id = s.scenario_id
GROUP BY s.scenario_id, s.scenario_name, s.supplier_id, s.supplier_delay_days, s.demand_growth_pct,
         s.created_at, r.variant;

-- ----------------------------- EXECUTIVE -----------------------------
CREATE OR REPLACE VIEW analytics.vw_executive_kpis AS
SELECT (SELECT SUM(items_value) FROM analytics.fact_orders WHERE is_revenue_order)                   AS total_revenue,
       (SELECT COUNT(*) FROM analytics.fact_orders WHERE is_revenue_order)                           AS total_orders,
       (SELECT ROUND(SUM(items_value) / NULLIF(COUNT(*), 0), 2) FROM analytics.fact_orders WHERE is_revenue_order)
                                                                                                    AS avg_order_value,
       (SELECT COUNT(*) FROM analytics.fact_order_items WHERE is_revenue_order)                      AS units_sold,
       (SELECT on_time_delivery_rate_pct FROM analytics.vw_delivery_kpis)                            AS on_time_delivery_rate_pct,
       (SELECT ROUND(AVG(review_score), 2) FROM analytics.fact_orders WHERE review_score IS NOT NULL) AS avg_review_score,
       (SELECT COUNT(*) FROM analytics.inventory_thresholds WHERE risk_status <> 'Healthy')          AS products_at_risk_sim,
       (SELECT COUNT(*) FROM analytics.alert_history WHERE status = 'ACTIVE' AND severity = 'CRITICAL') AS critical_alerts,
       (SELECT max_date FROM analytics.vw_data_window)                                              AS historical_data_end;

-- Latest scenario helpers (Superset custom SQL cannot contain sub-queries)
CREATE OR REPLACE VIEW analytics.vw_latest_scenario AS
SELECT scenario_id, scenario_name, supplier_id, supplier_delay_days, demand_growth_pct, created_at
FROM analytics.simulation_scenarios ORDER BY created_at DESC LIMIT 1;

CREATE OR REPLACE VIEW analytics.vw_latest_scenario_projection AS
SELECT p.*, l.scenario_name, TRUE AS is_simulated
FROM analytics.scenario_projection p JOIN analytics.vw_latest_scenario l USING (scenario_id);

CREATE OR REPLACE VIEW analytics.vw_latest_scenario_results AS
SELECT r.*, l.scenario_name, TRUE AS is_simulated
FROM analytics.scenario_results r JOIN analytics.vw_latest_scenario l USING (scenario_id);

CREATE OR REPLACE VIEW analytics.vw_latest_scenario_summary AS
SELECT s.* FROM analytics.vw_scenario_summary s JOIN analytics.vw_latest_scenario l USING (scenario_id);
