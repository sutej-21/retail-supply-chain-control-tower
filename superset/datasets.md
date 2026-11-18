# Superset Datasets

All datasets point to the schema `analytics` in the database connection **Retail Supply Chain (PostgreSQL)**.
[SIM] = simulated operational data.

| Dataset | Grain | Used for |
|---|---|---|
| vw_sales_items_enriched | order item (with category, seller, state, status, date) | Revenue / orders KPIs and trends; the main target of the filters |
| vw_daily_sales / vw_monthly_sales | day / month | Sales trends (zero-filled days) |
| vw_delivery_daily | purchase date × customer state | On-time %, delayed orders, average delivery days (date-filterable) |
| vw_delivery_by_state | customer state | Delays by state |
| vw_seller_performance | seller | Seller ranking, revenue by seller |
| vw_review_by_delivery_status / vw_review_by_delay_bucket / vw_satisfaction_trend | status / bucket / month | Customer satisfaction |
| vw_current_inventory [SIM] | SKU × warehouse (latest) | Inventory tiles, reorder table, risk distribution |
| vw_inventory_kpis [SIM] | single row | Inventory KPIs |
| vw_inventory_by_category [SIM] | category × risk | Stacked bars |
| vw_inventory_trend / vw_inventory_consumption_daily [SIM] | hour / day × category | Consumption trend |
| vw_forecast_vs_inventory [SIM stock] | SKU × warehouse | 30-day forecast vs available stock |
| vw_supplier_kpis [SIM] | supplier | Lead time, reliability, ranking |
| vw_forecast_latest | level × entity × forecast date | 7/30/60-day forecasts |
| vw_demand_actual_vs_forecast | level × entity × date | Forecast vs history |
| vw_product_demand_growth / vw_category_demand_growth / vw_top_products | product / category | Demand growth, top products |
| forecast_accuracy | model | Hold-out metrics |
| vw_active_alerts / alert_history | alert | Alerts dashboard |
| vw_scenario_summary, vw_latest_scenario_summary / _projection / _results [SIM] | scenario × variant (× SKU / day) | What-if analysis |
| scenario_projection / scenario_results / anomaly_events | raw tables | Ad-hoc exploration in SQL Lab |
