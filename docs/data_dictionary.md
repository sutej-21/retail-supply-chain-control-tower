# Data Dictionary

Generated from the live schema by `scripts/generate_data_dictionary.py`. **[SIM]** = synthetic operational data, not Olist. PK = primary key.

## `analytics.alert_history` (table)

All alerts with de-dup fingerprint, status, delivery channels and occurrences.

| Column | Type | Nullable | Key |
|---|---|---|---|
| alert_id | text | NO | PK |
| fingerprint | text | NO |  |
| alert_type | text | NO |  |
| severity | text | NO |  |
| entity_type | text | NO |  |
| entity_id | text | NO |  |
| product_id | text | YES |  |
| title | text | NO |  |
| explanation | text | NO |  |
| recommended_action | text | NO |  |
| metrics | jsonb | YES |  |
| status | text | NO |  |
| notified | boolean | NO |  |
| channels | jsonb | YES |  |
| occurrences | integer | NO |  |
| created_at | timestamp without time zone | NO |  |
| last_seen_at | timestamp without time zone | NO |  |
| resolved_at | timestamp without time zone | YES |  |
| run_id | text | YES |  |
| is_simulated_input | boolean | NO |  |

## `analytics.anomaly_events` (table)

Detected anomalies (deterministic id) with severity and recommended action.

| Column | Type | Nullable | Key |
|---|---|---|---|
| anomaly_id | text | NO | PK |
| record_id | text | NO |  |
| anomaly_type | text | NO |  |
| detection_method | text | NO |  |
| anomaly_score | numeric | NO |  |
| metric_value | numeric | YES |  |
| expected_value | numeric | YES |  |
| event_date | date | YES |  |
| detection_ts | timestamp without time zone | NO |  |
| severity | text | NO |  |
| recommended_action | text | NO |  |
| data_source | text | NO |  |
| details | jsonb | YES |  |

## `analytics.bridge_product_supplier` (table)

SIMULATED SKU -> supplier / warehouse mapping and unit cost proxy.

| Column | Type | Nullable | Key |
|---|---|---|---|
| product_id | text | NO | PK |
| supplier_id | text | NO |  |
| warehouse_id | text | NO |  |
| unit_cost_proxy | numeric | NO |  |
| is_simulated | boolean | NO |  |

## `analytics.data_quality_log` (table)

Persisted data-quality check results per run.

| Column | Type | Nullable | Key |
|---|---|---|---|
| dq_id | bigint | NO | PK |
| run_id | text | NO |  |
| table_name | text | NO |  |
| check_name | text | NO |  |
| column_name | text | YES |  |
| status | text | NO |  |
| severity | text | NO |  |
| failing_count | bigint | NO |  |
| total_count | bigint | NO |  |
| failing_pct | numeric | YES |  |
| details | text | YES |  |
| checked_at | timestamp without time zone | NO |  |

## `analytics.dim_customer` (table)

Customers with zip-prefix centroid.

| Column | Type | Nullable | Key |
|---|---|---|---|
| customer_id | text | NO | PK |
| customer_unique_id | text | NO |  |
| zip_code_prefix | character | YES |  |
| city | text | YES |  |
| state | character | YES |  |
| latitude | double precision | YES |  |
| longitude | double precision | YES |  |
| updated_at | timestamp without time zone | NO |  |

## `analytics.dim_date` (table)

Calendar 2016-01-01 .. today+400d. Key = YYYYMMDD.

| Column | Type | Nullable | Key |
|---|---|---|---|
| date_key | integer | NO | PK |
| full_date | date | NO |  |
| year | smallint | NO |  |
| quarter | smallint | NO |  |
| month | smallint | NO |  |
| month_name | text | NO |  |
| year_month | text | NO |  |
| iso_week | smallint | NO |  |
| day_of_month | smallint | NO |  |
| day_of_week | smallint | NO |  |
| day_name | text | NO |  |
| is_weekend | boolean | NO |  |

## `analytics.dim_product` (table)

Products + historical sales attributes (avg valid unit price, units sold).

| Column | Type | Nullable | Key |
|---|---|---|---|
| product_id | text | NO | PK |
| category_name_pt | text | NO |  |
| category_name_en | text | NO |  |
| is_category_missing | boolean | NO |  |
| name_length | numeric | YES |  |
| description_length | numeric | YES |  |
| photos_qty | numeric | YES |  |
| weight_g | numeric | YES |  |
| length_cm | numeric | YES |  |
| height_cm | numeric | YES |  |
| width_cm | numeric | YES |  |
| volume_cm3 | numeric | YES |  |
| avg_unit_price | numeric | YES |  |
| units_sold_total | integer | NO |  |
| first_sale_date | date | YES |  |
| last_sale_date | date | YES |  |
| updated_at | timestamp without time zone | NO |  |

## `analytics.dim_seller` (table)

Sellers with zip-prefix centroid.

| Column | Type | Nullable | Key |
|---|---|---|---|
| seller_id | text | NO | PK |
| zip_code_prefix | character | YES |  |
| city | text | YES |  |
| state | character | YES |  |
| latitude | double precision | YES |  |
| longitude | double precision | YES |  |
| updated_at | timestamp without time zone | NO |  |

## `analytics.dim_supplier` (table)

SIMULATED suppliers (fictional names) with planned and realised lead times.

| Column | Type | Nullable | Key |
|---|---|---|---|
| supplier_id | text | NO | PK |
| supplier_name | text | NO |  |
| supplier_location | text | NO |  |
| supplier_state | character | NO |  |
| average_lead_time_days | numeric | NO |  |
| lead_time_std_days | numeric | NO |  |
| actual_simulated_lead_time_days | numeric | YES |  |
| supplier_reliability_score | numeric | YES |  |
| supplier_status | text | NO |  |
| is_simulated | boolean | NO |  |
| data_source | text | NO |  |
| created_at | timestamp without time zone | NO |  |
| updated_at | timestamp without time zone | NO |  |

## `analytics.dim_warehouse` (table)

SIMULATED distribution centres.

| Column | Type | Nullable | Key |
|---|---|---|---|
| warehouse_id | text | NO | PK |
| warehouse_name | text | NO |  |
| city | text | NO |  |
| state | character | NO |  |
| capacity_units | integer | NO |  |
| is_simulated | boolean | NO |  |
| data_source | text | NO |  |

## `analytics.etl_run_log` (table)

Per task execution log (status, duration, rows, message).

| Column | Type | Nullable | Key |
|---|---|---|---|
| run_id | text | NO | PK |
| task_name | text | NO | PK |
| status | text | NO |  |
| started_at | timestamp without time zone | NO |  |
| finished_at | timestamp without time zone | YES |  |
| duration_sec | numeric | YES |  |
| rows_processed | bigint | YES |  |
| message | text | YES |  |

## `analytics.fact_demand_forecast` (table)

Prophet forecasts per level/entity/origin/date with intervals.

| Column | Type | Nullable | Key |
|---|---|---|---|
| forecast_id | bigint | NO | PK |
| run_id | text | NO |  |
| model_level | text | NO |  |
| entity_id | text | NO |  |
| entity_name | text | YES |  |
| forecast_origin_date | date | NO |  |
| forecast_date | date | NO |  |
| horizon_day | integer | NO |  |
| yhat | numeric | NO |  |
| yhat_lower | numeric | NO |  |
| yhat_upper | numeric | NO |  |
| model_name | text | NO |  |
| created_at | timestamp without time zone | NO |  |

## `analytics.fact_inventory` (table)

SIMULATED hourly snapshot per product x warehouse.

| Column | Type | Nullable | Key |
|---|---|---|---|
| inventory_id | bigint | NO | PK |
| product_id | text | NO |  |
| warehouse_id | text | NO |  |
| available_stock | integer | NO |  |
| reserved_stock | integer | NO |  |
| on_order_qty | integer | NO |  |
| reorder_level | integer | NO |  |
| safety_stock | integer | NO |  |
| maximum_stock | integer | NO |  |
| inventory_timestamp | timestamp without time zone | NO |  |
| is_simulated | boolean | NO |  |
| data_source | text | NO |  |

## `analytics.fact_inventory_movements` (table)

SIMULATED movements; quantity signed (+in / -out).

| Column | Type | Nullable | Key |
|---|---|---|---|
| movement_id | text | NO | PK |
| product_id | text | NO |  |
| warehouse_id | text | NO |  |
| movement_type | text | NO |  |
| quantity | integer | NO |  |
| movement_timestamp | timestamp without time zone | NO |  |
| reference_order_id | text | YES |  |
| is_simulated | boolean | NO |  |

## `analytics.fact_order_items` (table)

Grain: order item. Revenue line items.

| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | NO | PK |
| order_item_id | integer | NO | PK |
| product_id | text | NO |  |
| seller_id | text | NO |  |
| customer_id | text | NO |  |
| purchase_date | date | NO |  |
| purchase_date_key | integer | NO |  |
| order_status | text | NO |  |
| is_revenue_order | boolean | NO |  |
| shipping_limit_ts | timestamp without time zone | YES |  |
| price | numeric | NO |  |
| freight_value | numeric | NO |  |
| is_price_valid | boolean | NO |  |

## `analytics.fact_orders` (table)

Grain: order. Items and payments pre-aggregated (no fan-out). Delivery and review measures.

| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | NO | PK |
| customer_id | text | NO |  |
| customer_unique_id | text | NO |  |
| customer_state | character | YES |  |
| order_status | text | NO |  |
| purchase_ts | timestamp without time zone | NO |  |
| purchase_date | date | NO |  |
| purchase_date_key | integer | NO |  |
| approved_ts | timestamp without time zone | YES |  |
| delivered_carrier_ts | timestamp without time zone | YES |  |
| delivered_customer_ts | timestamp without time zone | YES |  |
| estimated_delivery_ts | timestamp without time zone | YES |  |
| item_count | integer | NO |  |
| distinct_products | integer | NO |  |
| distinct_sellers | integer | NO |  |
| items_value | numeric | NO |  |
| freight_value | numeric | NO |  |
| order_value | numeric | NO |  |
| payment_value | numeric | YES |  |
| payment_count | integer | NO |  |
| max_installments | integer | YES |  |
| primary_payment_type | text | YES |  |
| is_revenue_order | boolean | NO |  |
| is_delivered | boolean | NO |  |
| delivery_days | numeric | YES |  |
| delay_days | numeric | YES |  |
| is_late | boolean | YES |  |
| delivery_status | text | NO |  |
| review_score | smallint | YES |  |
| has_timestamp_issue | boolean | NO |  |
| updated_at | timestamp without time zone | NO |  |

## `analytics.fact_payments` (table)

Grain: payment.

| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | NO | PK |
| payment_sequential | integer | NO | PK |
| payment_date_key | integer | NO |  |
| payment_type | text | NO |  |
| payment_installments | integer | NO |  |
| payment_value | numeric | NO |  |

## `analytics.fact_reviews` (table)

Grain: review. `is_latest_for_order` marks the review used at order level.

| Column | Type | Nullable | Key |
|---|---|---|---|
| review_id | text | NO | PK |
| order_id | text | NO | PK |
| review_score | smallint | NO |  |
| has_comment | boolean | NO |  |
| review_creation_ts | timestamp without time zone | YES |  |
| review_answer_ts | timestamp without time zone | YES |  |
| response_hours | numeric | YES |  |
| is_latest_for_order | boolean | NO |  |
| review_date_key | integer | YES |  |

## `analytics.fact_shipments` (table)

Grain: shipment. OUTBOUND_CUSTOMER = Olist order x seller; INBOUND_REPLENISHMENT = SIMULATED PO.

| Column | Type | Nullable | Key |
|---|---|---|---|
| shipment_id | text | NO | PK |
| shipment_type | text | NO |  |
| order_id | text | YES |  |
| po_id | text | YES |  |
| seller_id | text | YES |  |
| supplier_id | text | YES |  |
| product_id | text | YES |  |
| warehouse_id | text | YES |  |
| origin_state | character | YES |  |
| destination_state | character | YES |  |
| quantity | integer | YES |  |
| shipping_limit_ts | timestamp without time zone | YES |  |
| shipped_ts | timestamp without time zone | YES |  |
| delivered_ts | timestamp without time zone | YES |  |
| estimated_ts | timestamp without time zone | YES |  |
| delivery_days | numeric | YES |  |
| delay_days | numeric | YES |  |
| is_late | boolean | YES |  |
| shipment_status | text | NO |  |
| data_source | text | NO |  |
| is_simulated | boolean | NO |  |

## `analytics.fact_supplier_performance` (table)

SIMULATED purchase orders / supplier delivery events.

| Column | Type | Nullable | Key |
|---|---|---|---|
| po_id | text | NO | PK |
| supplier_id | text | NO |  |
| product_id | text | NO |  |
| warehouse_id | text | NO |  |
| order_qty | integer | NO |  |
| order_ts | timestamp without time zone | NO |  |
| expected_lead_time_days | numeric | NO |  |
| expected_arrival_ts | timestamp without time zone | NO |  |
| simulated_lead_time_days | numeric | NO |  |
| actual_arrival_ts | timestamp without time zone | YES |  |
| delay_days | numeric | YES |  |
| is_late | boolean | YES |  |
| is_disrupted | boolean | NO |  |
| status | text | NO |  |
| is_simulated | boolean | NO |  |

## `analytics.forecast_accuracy` (table)

Hold-out accuracy per model (MAE, RMSE, MAPE, WAPE, seasonal-naive MAE).

| Column | Type | Nullable | Key |
|---|---|---|---|
| model_level | text | NO | PK |
| entity_id | text | NO | PK |
| holdout_start | date | NO |  |
| holdout_end | date | NO | PK |
| run_id | text | NO |  |
| train_days | integer | NO |  |
| mae | numeric | YES |  |
| rmse | numeric | YES |  |
| mape | numeric | YES |  |
| wape | numeric | YES |  |
| naive_mae | numeric | YES |  |
| weekly_seasonality | boolean | YES |  |
| yearly_seasonality | boolean | YES |  |
| created_at | timestamp without time zone | NO |  |

## `analytics.inventory_thresholds` (table)

Current replenishment policy & risk per SKU-location (recomputed each run).

| Column | Type | Nullable | Key |
|---|---|---|---|
| product_id | text | NO | PK |
| warehouse_id | text | NO | PK |
| supplier_id | text | YES |  |
| avg_daily_demand | numeric | NO |  |
| demand_std | numeric | NO |  |
| forecast_daily_demand | numeric | YES |  |
| planning_daily_demand | numeric | NO |  |
| lead_time_days | numeric | NO |  |
| lead_time_std_days | numeric | NO |  |
| service_level | numeric | NO |  |
| z_value | numeric | NO |  |
| safety_stock | integer | NO |  |
| reorder_point | integer | NO |  |
| reorder_qty | integer | NO |  |
| maximum_stock | integer | NO |  |
| available_stock | integer | NO |  |
| reserved_stock | integer | NO |  |
| on_order_qty | integer | NO |  |
| net_available | integer | NO |  |
| days_of_cover | numeric | YES |  |
| projected_stockout_ts | timestamp without time zone | YES |  |
| forecast_demand_lead_time | numeric | YES |  |
| risk_status | text | NO |  |
| recommended_action | text | NO |  |
| inventory_timestamp | timestamp without time zone | NO |  |
| computed_at | timestamp without time zone | NO |  |
| is_simulated | boolean | NO |  |

## `analytics.scenario_projection` (table)

Daily aggregate projection per scenario/variant.

| Column | Type | Nullable | Key |
|---|---|---|---|
| scenario_id | text | NO | PK |
| variant | text | NO | PK |
| day_offset | integer | NO | PK |
| projection_date | date | NO |  |
| projected_inventory | numeric | NO |  |
| unfulfilled_units | numeric | NO |  |
| products_out_of_stock | integer | NO |  |

## `analytics.scenario_results` (table)

Per SKU results for BASELINE and MODIFIED variants.

| Column | Type | Nullable | Key |
|---|---|---|---|
| scenario_id | text | NO | PK |
| product_id | text | NO | PK |
| warehouse_id | text | NO | PK |
| variant | text | NO | PK |
| category_name_en | text | YES |  |
| supplier_id | text | YES |  |
| starting_stock | integer | NO |  |
| daily_demand | numeric | NO |  |
| lead_time_days | numeric | NO |  |
| replenishment_qty | integer | NO |  |
| safety_stock | integer | NO |  |
| projected_end_stock | numeric | NO |  |
| min_projected_stock | numeric | NO |  |
| stockout_date | date | YES |  |
| days_until_stockout | integer | YES |  |
| unfulfilled_units | numeric | NO |  |
| additional_cover_days_required | numeric | NO |  |
| additional_units_required | numeric | NO |  |
| unit_price_proxy | numeric | NO |  |
| revenue_exposure_proxy | numeric | NO |  |
| is_affected | boolean | NO |  |

## `analytics.simulation_scenarios` (table)

What-if scenario parameters and summary.

| Column | Type | Nullable | Key |
|---|---|---|---|
| scenario_id | text | NO | PK |
| scenario_name | text | NO |  |
| description | text | YES |  |
| supplier_id | text | YES |  |
| supplier_delay_days | numeric | NO |  |
| demand_growth_pct | numeric | NO |  |
| inventory_adjustment_pct | numeric | NO |  |
| replenishment_qty_override | integer | YES |  |
| safety_stock_multiplier | numeric | NO |  |
| horizon_days | integer | NO |  |
| summary | jsonb | YES |  |
| created_at | timestamp without time zone | NO |  |

## `analytics.simulation_state` (table)

Simulation clock and pipeline state (checksums, forecast refresh).

| Column | Type | Nullable | Key |
|---|---|---|---|
| state_key | text | NO | PK |
| state_value | jsonb | NO |  |
| updated_at | timestamp without time zone | NO |  |

## `analytics.vw_active_alerts` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| alert_id | text | YES |  |
| alert_type | text | YES |  |
| severity | text | YES |  |
| severity_rank | integer | YES |  |
| entity_type | text | YES |  |
| entity_id | text | YES |  |
| product_id | text | YES |  |
| product_category | text | YES |  |
| title | text | YES |  |
| explanation | text | YES |  |
| recommended_action | text | YES |  |
| occurrences | integer | YES |  |
| created_at | timestamp without time zone | YES |  |
| last_seen_at | timestamp without time zone | YES |  |
| notified | boolean | YES |  |
| is_simulated_input | boolean | YES |  |

## `analytics.vw_category_demand` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| product_category | text | YES |  |
| month | date | YES |  |
| units | bigint | YES |  |
| revenue | numeric | YES |  |
| orders | bigint | YES |  |

## `analytics.vw_category_demand_growth` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| product_category | text | YES |  |
| units_last_30d | bigint | YES |  |
| units_prev_30d | bigint | YES |  |
| demand_growth_pct | numeric | YES |  |

## `analytics.vw_current_inventory` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| product_id | text | YES |  |
| product_category | text | YES |  |
| warehouse_id | text | YES |  |
| warehouse_state | character | YES |  |
| supplier_id | text | YES |  |
| available_stock | integer | YES |  |
| reserved_stock | integer | YES |  |
| on_order_qty | integer | YES |  |
| net_available | integer | YES |  |
| safety_stock | integer | YES |  |
| reorder_point | integer | YES |  |
| reorder_qty | integer | YES |  |
| maximum_stock | integer | YES |  |
| planning_daily_demand | numeric | YES |  |
| days_of_cover | numeric | YES |  |
| lead_time_days | numeric | YES |  |
| projected_stockout_ts | timestamp without time zone | YES |  |
| forecast_demand_lead_time | numeric | YES |  |
| risk_status | text | YES |  |
| risk_rank | integer | YES |  |
| recommended_action | text | YES |  |
| unit_cost_proxy | numeric | YES |  |
| inventory_value_proxy | numeric | YES |  |
| avg_unit_price | numeric | YES |  |
| inventory_timestamp | timestamp without time zone | YES |  |
| computed_at | timestamp without time zone | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_customer_kpis` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| unique_customers | bigint | YES |  |
| repeat_customers | bigint | YES |  |
| repeat_customer_rate_pct | numeric | YES |  |
| avg_review_score | numeric | YES |  |
| low_rating_orders | bigint | YES |  |
| complaints_with_comment | bigint | YES |  |

## `analytics.vw_daily_sales` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| sales_date | date | YES |  |
| orders | bigint | YES |  |
| revenue | numeric | YES |  |
| freight | numeric | YES |  |
| units | bigint | YES |  |
| avg_order_value | numeric | YES |  |

## `analytics.vw_data_window` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| min_date | date | YES |  |
| max_date | date | YES |  |
| n_days | integer | YES |  |

## `analytics.vw_delivery_by_state` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| customer_state | character | YES |  |
| delivered_orders | bigint | YES |  |
| late_orders | bigint | YES |  |
| on_time_rate_pct | numeric | YES |  |
| avg_delivery_days | numeric | YES |  |
| avg_delay_days_late_orders | numeric | YES |  |

## `analytics.vw_delivery_daily` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| purchase_date | date | YES |  |
| customer_state | character | YES |  |
| delivered_orders | bigint | YES |  |
| late_orders | bigint | YES |  |
| avg_delivery_days | numeric | YES |  |
| avg_delay_days | numeric | YES |  |

## `analytics.vw_delivery_kpis` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| delivered_orders | bigint | YES |  |
| late_orders | bigint | YES |  |
| on_time_delivery_rate_pct | numeric | YES |  |
| late_delivery_rate_pct | numeric | YES |  |
| avg_delivery_days | numeric | YES |  |
| avg_delay_days_late_orders | numeric | YES |  |
| historical_shipped_not_delivered | bigint | YES |  |

## `analytics.vw_demand_actual_vs_forecast` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| model_level | text | YES |  |
| entity_id | text | YES |  |
| entity_name | text | YES |  |
| ds | date | YES |  |
| actual_units | numeric | YES |  |
| forecast_units | numeric | YES |  |
| forecast_lower | numeric | YES |  |
| forecast_upper | numeric | YES |  |
| series | text | YES |  |

## `analytics.vw_executive_kpis` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| total_revenue | numeric | YES |  |
| total_orders | bigint | YES |  |
| avg_order_value | numeric | YES |  |
| units_sold | bigint | YES |  |
| on_time_delivery_rate_pct | numeric | YES |  |
| avg_review_score | numeric | YES |  |
| products_at_risk_sim | bigint | YES |  |
| critical_alerts | bigint | YES |  |
| historical_data_end | date | YES |  |

## `analytics.vw_forecast_latest` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| model_level | text | YES |  |
| entity_id | text | YES |  |
| entity_name | text | YES |  |
| forecast_origin_date | date | YES |  |
| forecast_date | date | YES |  |
| horizon_day | integer | YES |  |
| yhat | numeric | YES |  |
| yhat_lower | numeric | YES |  |
| yhat_upper | numeric | YES |  |
| horizon_bucket | text | YES |  |

## `analytics.vw_forecast_vs_inventory` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| product_id | text | YES |  |
| product_category | text | YES |  |
| warehouse_id | text | YES |  |
| net_available | integer | YES |  |
| demand_next_30d | numeric | YES |  |
| demand_source | text | YES |  |
| surplus_or_gap | numeric | YES |  |
| risk_status | text | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_inventory_by_category` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| product_category | text | YES |  |
| risk_status | text | YES |  |
| sku_locations | bigint | YES |  |
| available_units | bigint | YES |  |
| reorder_point_units | bigint | YES |  |
| inventory_value_proxy | numeric | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_inventory_consumption_daily` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| movement_date | date | YES |  |
| product_category | text | YES |  |
| movement_type | text | YES |  |
| units | bigint | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_inventory_kpis` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| sku_locations | bigint | YES |  |
| total_available_units | bigint | YES |  |
| total_reserved_units | bigint | YES |  |
| inventory_value_proxy | numeric | YES |  |
| products_at_risk | bigint | YES |  |
| critical_products | bigint | YES |  |
| low_stock_products | bigint | YES |  |
| stockout_risk_pct | numeric | YES |  |
| days_of_inventory_on_hand | numeric | YES |  |
| total_recommended_reorder_units | bigint | YES |  |
| holding_exposure_proxy | numeric | YES |  |
| inventory_turnover_30d | numeric | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_inventory_trend` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| inventory_timestamp | timestamp without time zone | YES |  |
| product_category | text | YES |  |
| available_units | bigint | YES |  |
| reserved_units | bigint | YES |  |
| reorder_level_units | bigint | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_latest_scenario` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| scenario_id | text | YES |  |
| scenario_name | text | YES |  |
| supplier_id | text | YES |  |
| supplier_delay_days | numeric | YES |  |
| demand_growth_pct | numeric | YES |  |
| created_at | timestamp without time zone | YES |  |

## `analytics.vw_latest_scenario_projection` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| scenario_id | text | YES |  |
| variant | text | YES |  |
| day_offset | integer | YES |  |
| projection_date | date | YES |  |
| projected_inventory | numeric | YES |  |
| unfulfilled_units | numeric | YES |  |
| products_out_of_stock | integer | YES |  |
| scenario_name | text | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_latest_scenario_results` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| scenario_id | text | YES |  |
| product_id | text | YES |  |
| warehouse_id | text | YES |  |
| variant | text | YES |  |
| category_name_en | text | YES |  |
| supplier_id | text | YES |  |
| starting_stock | integer | YES |  |
| daily_demand | numeric | YES |  |
| lead_time_days | numeric | YES |  |
| replenishment_qty | integer | YES |  |
| safety_stock | integer | YES |  |
| projected_end_stock | numeric | YES |  |
| min_projected_stock | numeric | YES |  |
| stockout_date | date | YES |  |
| days_until_stockout | integer | YES |  |
| unfulfilled_units | numeric | YES |  |
| additional_cover_days_required | numeric | YES |  |
| additional_units_required | numeric | YES |  |
| unit_price_proxy | numeric | YES |  |
| revenue_exposure_proxy | numeric | YES |  |
| is_affected | boolean | YES |  |
| scenario_name | text | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_latest_scenario_summary` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| scenario_id | text | YES |  |
| scenario_name | text | YES |  |
| supplier_id | text | YES |  |
| supplier_delay_days | numeric | YES |  |
| demand_growth_pct | numeric | YES |  |
| created_at | timestamp without time zone | YES |  |
| variant | text | YES |  |
| products_stocking_out | bigint | YES |  |
| products_affected | bigint | YES |  |
| unfulfilled_units | numeric | YES |  |
| revenue_exposure_proxy | numeric | YES |  |
| additional_units_required | numeric | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_low_rating_orders` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | YES |  |
| purchase_date | date | YES |  |
| customer_state | character | YES |  |
| order_status | text | YES |  |
| delivery_status | text | YES |  |
| delay_days | numeric | YES |  |
| review_score | smallint | YES |  |
| has_comment | boolean | YES |  |
| order_value | numeric | YES |  |

## `analytics.vw_monthly_sales` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| month | date | YES |  |
| orders | numeric | YES |  |
| revenue | numeric | YES |  |
| units | numeric | YES |  |
| avg_order_value | numeric | YES |  |

## `analytics.vw_product_demand_growth` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| product_id | text | YES |  |
| product_category | text | YES |  |
| units_last_30d | bigint | YES |  |
| units_prev_30d | bigint | YES |  |
| revenue_last_30d | numeric | YES |  |
| demand_growth_pct | numeric | YES |  |

## `analytics.vw_review_by_delay_bucket` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| delay_bucket | text | YES |  |
| sort_key | numeric | YES |  |
| orders | bigint | YES |  |
| avg_review_score | numeric | YES |  |

## `analytics.vw_review_by_delivery_status` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| delivery_status | text | YES |  |
| orders_with_review | bigint | YES |  |
| avg_review_score | numeric | YES |  |
| low_rating_pct | numeric | YES |  |

## `analytics.vw_sales_items_enriched` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | YES |  |
| order_item_id | integer | YES |  |
| purchase_date | date | YES |  |
| year_month | text | YES |  |
| day_name | text | YES |  |
| order_status | text | YES |  |
| is_revenue_order | boolean | YES |  |
| product_id | text | YES |  |
| product_category | text | YES |  |
| seller_id | text | YES |  |
| seller_state | character | YES |  |
| customer_state | character | YES |  |
| delivery_status | text | YES |  |
| review_score | smallint | YES |  |
| price | numeric | YES |  |
| freight_value | numeric | YES |  |
| units | integer | YES |  |

## `analytics.vw_satisfaction_trend` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| month | date | YES |  |
| avg_review_score | numeric | YES |  |
| low_rating_orders | bigint | YES |  |
| reviewed_orders | bigint | YES |  |

## `analytics.vw_scenario_summary` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| scenario_id | text | YES |  |
| scenario_name | text | YES |  |
| supplier_id | text | YES |  |
| supplier_delay_days | numeric | YES |  |
| demand_growth_pct | numeric | YES |  |
| created_at | timestamp without time zone | YES |  |
| variant | text | YES |  |
| products_stocking_out | bigint | YES |  |
| products_affected | bigint | YES |  |
| unfulfilled_units | numeric | YES |  |
| revenue_exposure_proxy | numeric | YES |  |
| additional_units_required | numeric | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_seller_performance` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| seller_id | text | YES |  |
| seller_state | character | YES |  |
| shipments | bigint | YES |  |
| delivered | bigint | YES |  |
| late | bigint | YES |  |
| on_time_rate_pct | numeric | YES |  |
| avg_delivery_days | numeric | YES |  |
| avg_delay_days_late | numeric | YES |  |
| late_handover_to_carrier | bigint | YES |  |
| revenue | numeric | YES |  |
| units | bigint | YES |  |
| avg_review_score | numeric | YES |  |
| performance_rank | bigint | YES |  |

## `analytics.vw_supplier_kpis` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| supplier_id | text | YES |  |
| supplier_name | text | YES |  |
| supplier_state | character | YES |  |
| supplier_status | text | YES |  |
| average_lead_time_days | numeric | YES |  |
| purchase_orders | bigint | YES |  |
| open_purchase_orders | bigint | YES |  |
| received_orders | bigint | YES |  |
| avg_actual_lead_time_days | numeric | YES |  |
| delay_rate_pct | numeric | YES |  |
| avg_delay_days_late | numeric | YES |  |
| reliability_score_pct | numeric | YES |  |
| performance_rank | bigint | YES |  |
| skus_at_replenishment_risk | bigint | YES |  |
| is_simulated | boolean | YES |  |

## `analytics.vw_top_products` (view)



| Column | Type | Nullable | Key |
|---|---|---|---|
| product_id | text | YES |  |
| product_category | text | YES |  |
| units_sold | bigint | YES |  |
| revenue | numeric | YES |  |
| orders | bigint | YES |  |
| units_rank | bigint | YES |  |
| revenue_rank | bigint | YES |  |

## `raw.load_audit` (table)



| Column | Type | Nullable | Key |
|---|---|---|---|
| audit_id | bigint | NO | PK |
| batch_id | text | NO |  |
| table_name | text | NO |  |
| source_file | text | NO |  |
| sha256 | text | NO |  |
| row_count | bigint | NO |  |
| status | text | NO |  |
| loaded_at | timestamp without time zone | NO |  |

## `raw.olist_category_translation` (table)

Original Olist CSV rows, every column TEXT + lineage (_batch_id, _source_file, _loaded_at).

| Column | Type | Nullable | Key |
|---|---|---|---|
| product_category_name | text | YES |  |
| product_category_name_english | text | YES |  |
| _batch_id | text | YES |  |
| _source_file | text | YES |  |
| _loaded_at | timestamp without time zone | YES |  |

## `raw.olist_customers` (table)

Original Olist CSV rows, every column TEXT + lineage (_batch_id, _source_file, _loaded_at).

| Column | Type | Nullable | Key |
|---|---|---|---|
| customer_id | text | YES |  |
| customer_unique_id | text | YES |  |
| customer_zip_code_prefix | text | YES |  |
| customer_city | text | YES |  |
| customer_state | text | YES |  |
| _batch_id | text | YES |  |
| _source_file | text | YES |  |
| _loaded_at | timestamp without time zone | YES |  |

## `raw.olist_geolocation` (table)

Original Olist CSV rows, every column TEXT + lineage (_batch_id, _source_file, _loaded_at).

| Column | Type | Nullable | Key |
|---|---|---|---|
| geolocation_zip_code_prefix | text | YES |  |
| geolocation_lat | text | YES |  |
| geolocation_lng | text | YES |  |
| geolocation_city | text | YES |  |
| geolocation_state | text | YES |  |
| _batch_id | text | YES |  |
| _source_file | text | YES |  |
| _loaded_at | timestamp without time zone | YES |  |

## `raw.olist_order_items` (table)

Original Olist CSV rows, every column TEXT + lineage (_batch_id, _source_file, _loaded_at).

| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | YES |  |
| order_item_id | text | YES |  |
| product_id | text | YES |  |
| seller_id | text | YES |  |
| shipping_limit_date | text | YES |  |
| price | text | YES |  |
| freight_value | text | YES |  |
| _batch_id | text | YES |  |
| _source_file | text | YES |  |
| _loaded_at | timestamp without time zone | YES |  |

## `raw.olist_orders` (table)

Original Olist CSV rows, every column TEXT + lineage (_batch_id, _source_file, _loaded_at).

| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | YES |  |
| customer_id | text | YES |  |
| order_status | text | YES |  |
| order_purchase_timestamp | text | YES |  |
| order_approved_at | text | YES |  |
| order_delivered_carrier_date | text | YES |  |
| order_delivered_customer_date | text | YES |  |
| order_estimated_delivery_date | text | YES |  |
| _batch_id | text | YES |  |
| _source_file | text | YES |  |
| _loaded_at | timestamp without time zone | YES |  |

## `raw.olist_payments` (table)

Original Olist CSV rows, every column TEXT + lineage (_batch_id, _source_file, _loaded_at).

| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | YES |  |
| payment_sequential | text | YES |  |
| payment_type | text | YES |  |
| payment_installments | text | YES |  |
| payment_value | text | YES |  |
| _batch_id | text | YES |  |
| _source_file | text | YES |  |
| _loaded_at | timestamp without time zone | YES |  |

## `raw.olist_products` (table)

Original Olist CSV rows, every column TEXT + lineage (_batch_id, _source_file, _loaded_at).

| Column | Type | Nullable | Key |
|---|---|---|---|
| product_id | text | YES |  |
| product_category_name | text | YES |  |
| product_name_lenght | text | YES |  |
| product_description_lenght | text | YES |  |
| product_photos_qty | text | YES |  |
| product_weight_g | text | YES |  |
| product_length_cm | text | YES |  |
| product_height_cm | text | YES |  |
| product_width_cm | text | YES |  |
| _batch_id | text | YES |  |
| _source_file | text | YES |  |
| _loaded_at | timestamp without time zone | YES |  |

## `raw.olist_reviews` (table)

Original Olist CSV rows, every column TEXT + lineage (_batch_id, _source_file, _loaded_at).

| Column | Type | Nullable | Key |
|---|---|---|---|
| review_id | text | YES |  |
| order_id | text | YES |  |
| review_score | text | YES |  |
| review_comment_title | text | YES |  |
| review_comment_message | text | YES |  |
| review_creation_date | text | YES |  |
| review_answer_timestamp | text | YES |  |
| _batch_id | text | YES |  |
| _source_file | text | YES |  |
| _loaded_at | timestamp without time zone | YES |  |

## `raw.olist_sellers` (table)

Original Olist CSV rows, every column TEXT + lineage (_batch_id, _source_file, _loaded_at).

| Column | Type | Nullable | Key |
|---|---|---|---|
| seller_id | text | YES |  |
| seller_zip_code_prefix | text | YES |  |
| seller_city | text | YES |  |
| seller_state | text | YES |  |
| _batch_id | text | YES |  |
| _source_file | text | YES |  |
| _loaded_at | timestamp without time zone | YES |  |

## `staging.category_translation` (table)

Portuguese -> English category names.

| Column | Type | Nullable | Key |
|---|---|---|---|
| category_name_pt | text | NO | PK |
| category_name_en | text | NO |  |

## `staging.customers` (table)

One row per customer_id. customer_id is generated per ORDER; customer_unique_id identifies the person.

| Column | Type | Nullable | Key |
|---|---|---|---|
| customer_id | text | NO | PK |
| customer_unique_id | text | NO |  |
| zip_code_prefix | character | YES |  |
| city | text | YES |  |
| state | character | YES |  |

## `staging.geolocation` (table)

One row per zip-code prefix (median lat/lng of points inside Brazil).

| Column | Type | Nullable | Key |
|---|---|---|---|
| zip_code_prefix | character | NO | PK |
| latitude | double precision | YES |  |
| longitude | double precision | YES |  |
| city | text | YES |  |
| state | character | YES |  |
| n_points | integer | YES |  |

## `staging.order_items` (table)

One row per order item (Olist). `is_price_valid` = price > 0.

| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | NO | PK |
| order_item_id | integer | NO | PK |
| product_id | text | NO |  |
| seller_id | text | NO |  |
| shipping_limit_ts | timestamp without time zone | YES |  |
| price | numeric | NO |  |
| freight_value | numeric | NO |  |
| is_price_valid | boolean | NO |  |

## `staging.orders` (table)

One row per order (Olist). Timestamps parsed; `has_timestamp_issue` flags material inconsistencies.

| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | NO | PK |
| customer_id | text | NO |  |
| order_status | text | NO |  |
| order_purchase_ts | timestamp without time zone | NO |  |
| order_approved_ts | timestamp without time zone | YES |  |
| order_delivered_carrier_ts | timestamp without time zone | YES |  |
| order_delivered_customer_ts | timestamp without time zone | YES |  |
| order_estimated_delivery_ts | timestamp without time zone | YES |  |
| has_timestamp_issue | boolean | NO |  |
| timestamp_issue | text | YES |  |

## `staging.payments` (table)

One row per (order_id, payment_sequential).

| Column | Type | Nullable | Key |
|---|---|---|---|
| order_id | text | NO | PK |
| payment_sequential | integer | NO | PK |
| payment_type | text | NO |  |
| payment_installments | integer | NO |  |
| payment_value | numeric | NO |  |

## `staging.products` (table)

Products with category (pt/en). Missing categories -> 'unknown'.

| Column | Type | Nullable | Key |
|---|---|---|---|
| product_id | text | NO | PK |
| category_name_pt | text | NO |  |
| category_name_en | text | NO |  |
| is_category_missing | boolean | NO |  |
| name_length | numeric | YES |  |
| description_length | numeric | YES |  |
| photos_qty | numeric | YES |  |
| weight_g | numeric | YES |  |
| length_cm | numeric | YES |  |
| height_cm | numeric | YES |  |
| width_cm | numeric | YES |  |

## `staging.reviews` (table)

One row per (review_id, order_id).

| Column | Type | Nullable | Key |
|---|---|---|---|
| review_id | text | NO | PK |
| order_id | text | NO | PK |
| review_score | smallint | NO |  |
| review_comment_title | text | YES |  |
| review_comment_message | text | YES |  |
| review_creation_ts | timestamp without time zone | YES |  |
| review_answer_ts | timestamp without time zone | YES |  |
| has_comment | boolean | NO |  |

## `staging.sellers` (table)

Sellers with standardised city/state/zip prefix.

| Column | Type | Nullable | Key |
|---|---|---|---|
| seller_id | text | NO | PK |
| zip_code_prefix | character | YES |  |
| city | text | YES |  |
| state | character | YES |  |
