# KPI Definitions

**Source** column: **H** = Olist historical data, **SIM** = simulated operational data.
SQL lives in `sql/06_create_analytics_views.sql` (views) and `sql/08_kpi_queries.sql` (reference queries);
the Python equivalents are in `src/transformations/kpi_calculations.py`. The integration test
`tests/test_kpis.py::test_sql_views_match_python` checks that the SQL and Python numbers match.

Global conventions
* **Revenue order** = `order_status NOT IN ('canceled','unavailable')`.
* **Revenue** = sum of item `price` of revenue orders. Freight is reported separately. `payment_value` is not used,
  because it includes vouchers and installment interest.
* Historical "last N days" windows are anchored to the **last date in the Olist data** (`vw_data_window.max_date`),
  not to today.

## Sales & demand (H)

| KPI | Formula | Object | Limitations |
|---|---|---|---|
| Total sales revenue | `SUM(items_value) WHERE is_revenue_order` | `vw_executive_kpis` | Excludes freight; BRL nominal values |
| Total orders | `COUNT(*) WHERE is_revenue_order` | `vw_executive_kpis` | |
| Average order value | revenue / revenue orders | `vw_executive_kpis`, `vw_daily_sales` | Skewed by a few large baskets |
| Units sold | `COUNT(order items)` of revenue orders (1 row = 1 unit in Olist) | `vw_executive_kpis` | Olist has no quantity column; each item row is one unit |
| Daily / monthly sales | the measures above grouped by purchase date / month; missing days filled with 0 | `vw_daily_sales`, `vw_monthly_sales` | The last weeks of Olist are incomplete (extraction cut-off) |
| Product-category demand | units and revenue per category per month | `vw_category_demand` | ~1.9% of Olist products have no category (shown as `unknown`) |
| Product demand growth | `(units_last_30d - units_prev_30d) / units_prev_30d * 100` | `vw_product_demand_growth`, `vw_category_demand_growth` | Undefined when the previous window is 0; noisy for low-volume products |
| Top-selling products | rank by units / revenue | `vw_top_products` | |
| Revenue by seller | `SUM(price)` per seller | `vw_seller_performance` | |

## Delivery (H, plus one SIM measure)

| KPI | Formula | Object | Limitations |
|---|---|---|---|
| On-time delivery rate | delivered orders with `delivered_date <= estimated_date` / delivered orders with an estimate | `vw_delivery_kpis` | Day-level comparison; orders with invalid timestamps are excluded |
| Late delivery rate | 100 − on-time rate | `vw_delivery_kpis` | |
| Average delivery time | `AVG(delivered_ts − purchase_ts)` in days | `vw_delivery_kpis` | Includes seller handling time |
| Average delivery delay | `AVG(delivered_date − estimated_date)` over late orders | `vw_delivery_kpis` | |
| Delivery performance by seller | on-time %, average days, late handover to carrier, ranked when there are at least 10 deliveries | `vw_seller_performance` | Uses order × seller shipment grain |
| Delivery performance by state | the same measures per customer state | `vw_delivery_by_state` | |
| Shipments in transit **(SIM)** | simulated inbound replenishment shipments with status `IN_TRANSIT` | `fact_shipments` | Olist has no live shipment status, so this KPI comes from the simulation |

## Customer (H)

| KPI | Formula | Object | Limitations |
|---|---|---|---|
| Average review score | `AVG(review_score)` using the latest review per order | `vw_customer_kpis` | About 1% of orders have no review |
| Customer satisfaction trend | monthly average review score and low-rating count | `vw_satisfaction_trend` | |
| Review score by delivery status | average score for on_time / late / not_delivered | `vw_review_by_delivery_status`, `vw_review_by_delay_bucket` | Correlation, not causation |
| Repeat customer rate | share of `customer_unique_id` with more than 1 revenue order | `vw_customer_kpis` | **Must** use `customer_unique_id`, because `customer_id` is unique per order |
| Complaints / low-rating orders | orders with score ≤ 2; complaints = low-rating reviews that have a comment | `vw_customer_kpis`, `vw_low_rating_orders` | Comment text is not classified |

## Inventory (SIM)

| KPI | Formula | Object | Limitations |
|---|---|---|---|
| Inventory turnover (30 days) | simulated units consumed in 30 days / average on-hand units | `vw_inventory_kpis` | Short simulated history right after bootstrap |
| Days of inventory on hand | `SUM(net_available) / SUM(planning_daily_demand)` (per SKU: `net / d`) | `vw_inventory_kpis`, `inventory_thresholds.days_of_cover` | |
| Stock-out risk % | SKU-locations rated Critical or High Risk / all SKU-locations | `vw_inventory_kpis` | |
| Low-stock product count | SKU-locations with `net_available <= reorder_point` | `vw_inventory_kpis` | |
| Inventory value | `available × unit_cost_proxy` (cost proxy = historical average price × simulated cost ratio of 0.55–0.70) | `vw_inventory_kpis` | Proxy, not accounting value |
| Safety stock | `z · sqrt(L·σd² + d²·σL²)` | `inventory_thresholds` | Assumes normal, independent demand and lead time |
| Reorder point | `d·L + SS` | `inventory_thresholds` | |
| Reorder quantity | `max(0, d·(L+R) + SS − (available − reserved + on order))`, capped at maximum stock | `inventory_thresholds` | Order-up-to policy; no supplier MOQ or price breaks |
| Holding exposure | `SUM(max(available − ROP, 0) × unit cost)` | `vw_inventory_kpis` | Capital tied up above the reorder point |

## Supplier (SIM)

| KPI | Formula | Object | Limitations |
|---|---|---|---|
| Average supplier lead time | `AVG(simulated_lead_time_days)` of received POs | `vw_supplier_kpis` | Simulated |
| Supplier reliability score | received POs with delay ≤ 1 day / received POs | `vw_supplier_kpis`, `dim_supplier` | |
| Supplier delay rate | 100 − reliability | `vw_supplier_kpis` | |
| Supplier performance ranking | rank by reliability, then by lead time | `vw_supplier_kpis` | |
| Purchase replenishment risk | number of the supplier's SKUs rated Critical or High Risk | `vw_supplier_kpis.skus_at_replenishment_risk` | |
