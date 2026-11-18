-- =====================================================================
-- 08_kpi_queries.sql  -  reference KPI queries (read-only)
-- Each query is the documented calculation behind a KPI in
-- docs/kpi_definitions.md. They are executed by tests/test_kpis.py
-- (integration) and can be pasted into Superset SQL Lab.
-- [SIM] = uses SIMULATED operational data.
-- =====================================================================

-- ======================= SALES & DEMAND ===============================
-- KPI: Total sales revenue / total orders / AOV / units sold
SELECT SUM(items_value)                              AS total_revenue,
       COUNT(*)                                      AS total_orders,
       ROUND(SUM(items_value) / NULLIF(COUNT(*), 0), 2) AS avg_order_value,
       SUM(item_count)                               AS units_sold
FROM analytics.fact_orders
WHERE is_revenue_order;

-- KPI: Daily sales
SELECT sales_date, orders, revenue, units, avg_order_value
FROM analytics.vw_daily_sales ORDER BY sales_date;

-- KPI: Monthly sales
SELECT month, orders, revenue, units, avg_order_value
FROM analytics.vw_monthly_sales ORDER BY month;

-- KPI: Product-category demand (units, latest 12 months of data)
SELECT product_category, SUM(units) AS units, SUM(revenue) AS revenue
FROM analytics.vw_category_demand
WHERE month > (SELECT max_date FROM analytics.vw_data_window) - INTERVAL '12 months'
GROUP BY product_category ORDER BY units DESC;

-- KPI: Product demand growth (last 30d vs previous 30d, min 5 units)
SELECT * FROM analytics.vw_product_demand_growth
WHERE units_prev_30d >= 5 ORDER BY demand_growth_pct DESC NULLS LAST LIMIT 20;

-- KPI: Top-selling products
SELECT * FROM analytics.vw_top_products WHERE units_rank <= 20 ORDER BY units_rank;

-- KPI: Revenue by seller
SELECT seller_id, seller_state, revenue, units FROM analytics.vw_seller_performance ORDER BY revenue DESC LIMIT 20;

-- ============================ DELIVERY ================================
-- KPI: On-time / late delivery rate, average delivery time, average delay
SELECT * FROM analytics.vw_delivery_kpis;

-- KPI: Delivery performance by seller (ranked, >= 10 deliveries)
SELECT * FROM analytics.vw_seller_performance
WHERE performance_rank IS NOT NULL ORDER BY performance_rank LIMIT 20;

-- KPI: Delivery performance by customer state
SELECT * FROM analytics.vw_delivery_by_state ORDER BY on_time_rate_pct;

-- KPI [SIM]: Shipments in transit (simulated inbound replenishment)
SELECT COUNT(*) AS replenishment_shipments_in_transit
FROM analytics.fact_shipments
WHERE is_simulated AND shipment_status = 'IN_TRANSIT';

-- ============================ CUSTOMER ================================
-- KPI: Average review score, repeat customer rate (customer_unique_id), complaints
SELECT * FROM analytics.vw_customer_kpis;

-- KPI: Review score by delivery status
SELECT * FROM analytics.vw_review_by_delivery_status ORDER BY avg_review_score;

-- KPI: Customer satisfaction trend
SELECT * FROM analytics.vw_satisfaction_trend ORDER BY month;

-- ========================= INVENTORY [SIM] ============================
-- KPIs: inventory value, low-stock count, stock-out risk %, days on hand,
--       turnover, reorder quantity, holding exposure
SELECT * FROM analytics.vw_inventory_kpis;

-- KPI: Safety stock / reorder point / reorder quantity per SKU (at risk first)
SELECT product_id, product_category, warehouse_id, net_available, safety_stock, reorder_point,
       reorder_qty, days_of_cover, risk_status, recommended_action
FROM analytics.vw_current_inventory ORDER BY risk_rank, days_of_cover LIMIT 50;

-- ========================= SUPPLIER [SIM] =============================
-- KPIs: average lead time, reliability, delay rate, ranking, replenishment risk
SELECT * FROM analytics.vw_supplier_kpis ORDER BY performance_rank;

-- ========================= EXECUTIVE ==================================
SELECT * FROM analytics.vw_executive_kpis;
