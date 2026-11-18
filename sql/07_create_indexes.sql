-- =====================================================================
-- 07_create_indexes.sql  -  indexes for joins, filters and dashboards
-- =====================================================================
CREATE INDEX IF NOT EXISTS ix_raw_load_audit_table      ON raw.load_audit (table_name, loaded_at DESC);

CREATE INDEX IF NOT EXISTS ix_stg_orders_customer       ON staging.orders (customer_id);
CREATE INDEX IF NOT EXISTS ix_stg_items_product         ON staging.order_items (product_id);
CREATE INDEX IF NOT EXISTS ix_stg_items_seller          ON staging.order_items (seller_id);
CREATE INDEX IF NOT EXISTS ix_stg_reviews_order         ON staging.reviews (order_id);

CREATE INDEX IF NOT EXISTS ix_fo_purchase_date          ON analytics.fact_orders (purchase_date);
CREATE INDEX IF NOT EXISTS ix_fo_customer_unique        ON analytics.fact_orders (customer_unique_id);
CREATE INDEX IF NOT EXISTS ix_fo_customer_state         ON analytics.fact_orders (customer_state);
CREATE INDEX IF NOT EXISTS ix_fo_status                 ON analytics.fact_orders (order_status);
CREATE INDEX IF NOT EXISTS ix_foi_product_date          ON analytics.fact_order_items (product_id, purchase_date);
CREATE INDEX IF NOT EXISTS ix_foi_seller                ON analytics.fact_order_items (seller_id);
CREATE INDEX IF NOT EXISTS ix_foi_date                  ON analytics.fact_order_items (purchase_date);
CREATE INDEX IF NOT EXISTS ix_fr_order                  ON analytics.fact_reviews (order_id);
CREATE INDEX IF NOT EXISTS ix_fs_seller                 ON analytics.fact_shipments (seller_id);
CREATE INDEX IF NOT EXISTS ix_fs_type_status            ON analytics.fact_shipments (shipment_type, shipment_status);
CREATE INDEX IF NOT EXISTS ix_dp_category               ON analytics.dim_product (category_name_en);
CREATE INDEX IF NOT EXISTS ix_dc_unique                 ON analytics.dim_customer (customer_unique_id);

CREATE INDEX IF NOT EXISTS ix_fi_ts                     ON analytics.fact_inventory (inventory_timestamp);
CREATE INDEX IF NOT EXISTS ix_fi_product_ts             ON analytics.fact_inventory (product_id, inventory_timestamp DESC);
CREATE INDEX IF NOT EXISTS ix_fim_product_ts            ON analytics.fact_inventory_movements (product_id, movement_timestamp);
CREATE INDEX IF NOT EXISTS ix_fim_type_ts               ON analytics.fact_inventory_movements (movement_type, movement_timestamp);
CREATE INDEX IF NOT EXISTS ix_fsp_supplier_status       ON analytics.fact_supplier_performance (supplier_id, status);
CREATE INDEX IF NOT EXISTS ix_fsp_product_status        ON analytics.fact_supplier_performance (product_id, status);
CREATE INDEX IF NOT EXISTS ix_fdf_level_entity          ON analytics.fact_demand_forecast (model_level, entity_id, forecast_origin_date);
CREATE INDEX IF NOT EXISTS ix_alert_fingerprint         ON analytics.alert_history (fingerprint, status);
CREATE INDEX IF NOT EXISTS ix_alert_created             ON analytics.alert_history (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_anomaly_type_date         ON analytics.anomaly_events (anomaly_type, event_date);
CREATE INDEX IF NOT EXISTS ix_thresholds_risk           ON analytics.inventory_thresholds (risk_status);
