-- =====================================================================
-- 05_create_fact_tables.sql  -  ANALYTICS layer: facts + supporting tables
-- Grain of every fact table is stated explicitly. Order-level measures are
-- pre-aggregated from items/payments BEFORE joining to avoid fan-out.
-- =====================================================================

-- ------------------------- HISTORICAL (Olist) --------------------------
-- Grain: one row per order
CREATE TABLE IF NOT EXISTS analytics.fact_orders (
    order_id              TEXT PRIMARY KEY,
    customer_id           TEXT NOT NULL REFERENCES analytics.dim_customer(customer_id),
    customer_unique_id    TEXT NOT NULL,
    customer_state        CHAR(2),
    order_status          TEXT NOT NULL,
    purchase_ts           TIMESTAMP NOT NULL,
    purchase_date         DATE NOT NULL,
    purchase_date_key     INTEGER NOT NULL REFERENCES analytics.dim_date(date_key),
    approved_ts           TIMESTAMP,
    delivered_carrier_ts  TIMESTAMP,
    delivered_customer_ts TIMESTAMP,
    estimated_delivery_ts TIMESTAMP,
    item_count            INTEGER NOT NULL DEFAULT 0,
    distinct_products     INTEGER NOT NULL DEFAULT 0,
    distinct_sellers      INTEGER NOT NULL DEFAULT 0,
    items_value           NUMERIC(14,2) NOT NULL DEFAULT 0,   -- sum(price)
    freight_value         NUMERIC(14,2) NOT NULL DEFAULT 0,   -- sum(freight_value)
    order_value           NUMERIC(14,2) NOT NULL DEFAULT 0,   -- items + freight
    payment_value         NUMERIC(14,2),                      -- sum(payment_value), may differ (vouchers/interest)
    payment_count         INTEGER NOT NULL DEFAULT 0,
    max_installments      INTEGER,
    primary_payment_type  TEXT,
    is_revenue_order      BOOLEAN NOT NULL,                   -- status NOT IN ('canceled','unavailable')
    is_delivered          BOOLEAN NOT NULL,                   -- delivered with valid timestamps
    delivery_days         NUMERIC(8,2),                       -- delivered - purchase (days)
    delay_days            NUMERIC(8,2),                       -- delivered date - estimated date (days, <=0 = on time)
    is_late               BOOLEAN,
    delivery_status       TEXT NOT NULL CHECK (delivery_status IN ('on_time','late','not_delivered','invalid_timestamps')),
    review_score          SMALLINT,                           -- latest review for the order
    has_timestamp_issue   BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at            TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

-- Grain: one row per order item
CREATE TABLE IF NOT EXISTS analytics.fact_order_items (
    order_id          TEXT NOT NULL REFERENCES analytics.fact_orders(order_id) ON DELETE CASCADE,
    order_item_id     INTEGER NOT NULL,
    product_id        TEXT NOT NULL REFERENCES analytics.dim_product(product_id),
    seller_id         TEXT NOT NULL REFERENCES analytics.dim_seller(seller_id),
    customer_id       TEXT NOT NULL,
    purchase_date     DATE NOT NULL,
    purchase_date_key INTEGER NOT NULL REFERENCES analytics.dim_date(date_key),
    order_status      TEXT NOT NULL,
    is_revenue_order  BOOLEAN NOT NULL,
    shipping_limit_ts TIMESTAMP,
    price             NUMERIC(12,2) NOT NULL,
    freight_value     NUMERIC(12,2) NOT NULL,
    is_price_valid    BOOLEAN NOT NULL,
    PRIMARY KEY (order_id, order_item_id)
);

-- Grain: one row per payment (order_id, payment_sequential)
CREATE TABLE IF NOT EXISTS analytics.fact_payments (
    order_id             TEXT NOT NULL REFERENCES analytics.fact_orders(order_id) ON DELETE CASCADE,
    payment_sequential   INTEGER NOT NULL,
    payment_date_key     INTEGER NOT NULL REFERENCES analytics.dim_date(date_key),
    payment_type         TEXT NOT NULL,
    payment_installments INTEGER NOT NULL,
    payment_value        NUMERIC(12,2) NOT NULL,
    PRIMARY KEY (order_id, payment_sequential)
);

-- Grain: one row per review (review_id, order_id)
CREATE TABLE IF NOT EXISTS analytics.fact_reviews (
    review_id            TEXT NOT NULL,
    order_id             TEXT NOT NULL REFERENCES analytics.fact_orders(order_id) ON DELETE CASCADE,
    review_score         SMALLINT NOT NULL,
    has_comment          BOOLEAN NOT NULL,
    review_creation_ts   TIMESTAMP,
    review_answer_ts     TIMESTAMP,
    response_hours       NUMERIC(10,2),
    is_latest_for_order  BOOLEAN NOT NULL,
    review_date_key      INTEGER REFERENCES analytics.dim_date(date_key),
    PRIMARY KEY (review_id, order_id)
);

-- Grain: one row per shipment.
--   OUTBOUND_CUSTOMER     = Olist order x seller (historical)
--   INBOUND_REPLENISHMENT = simulated supplier -> warehouse purchase order
CREATE TABLE IF NOT EXISTS analytics.fact_shipments (
    shipment_id         TEXT PRIMARY KEY,
    shipment_type       TEXT NOT NULL CHECK (shipment_type IN ('OUTBOUND_CUSTOMER','INBOUND_REPLENISHMENT')),
    order_id            TEXT,
    po_id               TEXT,
    seller_id           TEXT,
    supplier_id         TEXT,
    product_id          TEXT,
    warehouse_id        TEXT,
    origin_state        CHAR(2),
    destination_state   CHAR(2),
    quantity            INTEGER,
    shipping_limit_ts   TIMESTAMP,
    shipped_ts          TIMESTAMP,
    delivered_ts        TIMESTAMP,
    estimated_ts        TIMESTAMP,
    delivery_days       NUMERIC(8,2),
    delay_days          NUMERIC(8,2),
    is_late             BOOLEAN,
    shipment_status     TEXT NOT NULL,
    data_source         TEXT NOT NULL CHECK (data_source IN ('OLIST_HISTORICAL','SIMULATED')),
    is_simulated        BOOLEAN NOT NULL
);

-- ------------------------- SIMULATED ---------------------------------
-- Grain: one inventory snapshot per product x warehouse x hour
CREATE TABLE IF NOT EXISTS analytics.fact_inventory (
    inventory_id        BIGSERIAL PRIMARY KEY,
    product_id          TEXT NOT NULL REFERENCES analytics.dim_product(product_id),
    warehouse_id        TEXT NOT NULL REFERENCES analytics.dim_warehouse(warehouse_id),
    available_stock     INTEGER NOT NULL CHECK (available_stock >= 0),
    reserved_stock      INTEGER NOT NULL CHECK (reserved_stock >= 0),
    on_order_qty        INTEGER NOT NULL DEFAULT 0,
    reorder_level       INTEGER NOT NULL,
    safety_stock        INTEGER NOT NULL,
    maximum_stock       INTEGER NOT NULL,
    inventory_timestamp TIMESTAMP NOT NULL,
    is_simulated        BOOLEAN NOT NULL DEFAULT TRUE,
    data_source         TEXT NOT NULL DEFAULT 'SIMULATED',
    UNIQUE (product_id, warehouse_id, inventory_timestamp)
);
COMMENT ON TABLE analytics.fact_inventory IS 'SIMULATED hourly inventory snapshots (demand derived from Olist history)';

-- Grain: one row per inventory movement. quantity is SIGNED (+ inflow, - outflow)
CREATE TABLE IF NOT EXISTS analytics.fact_inventory_movements (
    movement_id        TEXT PRIMARY KEY,
    product_id         TEXT NOT NULL REFERENCES analytics.dim_product(product_id),
    warehouse_id       TEXT NOT NULL REFERENCES analytics.dim_warehouse(warehouse_id),
    movement_type      TEXT NOT NULL CHECK (movement_type IN
                         ('INITIAL_STOCK','SALE_CONSUMPTION','RESERVATION','RESERVATION_RELEASE',
                          'REPLENISHMENT_RECEIPT','ADJUSTMENT')),
    quantity           INTEGER NOT NULL,
    movement_timestamp TIMESTAMP NOT NULL,
    reference_order_id TEXT,              -- simulated order id (SIMORD-...) or purchase order id (PO-...)
    is_simulated       BOOLEAN NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE analytics.fact_inventory_movements IS 'SIMULATED inventory movements';

-- Grain: one row per simulated purchase order (supplier delivery event)
CREATE TABLE IF NOT EXISTS analytics.fact_supplier_performance (
    po_id                   TEXT PRIMARY KEY,
    supplier_id             TEXT NOT NULL REFERENCES analytics.dim_supplier(supplier_id),
    product_id              TEXT NOT NULL REFERENCES analytics.dim_product(product_id),
    warehouse_id            TEXT NOT NULL REFERENCES analytics.dim_warehouse(warehouse_id),
    order_qty               INTEGER NOT NULL CHECK (order_qty > 0),
    order_ts                TIMESTAMP NOT NULL,
    expected_lead_time_days NUMERIC(6,2) NOT NULL,
    expected_arrival_ts     TIMESTAMP NOT NULL,
    simulated_lead_time_days NUMERIC(6,2) NOT NULL,   -- realised (simulated) lead time
    actual_arrival_ts       TIMESTAMP,                -- NULL while in transit
    delay_days              NUMERIC(6,2),
    is_late                 BOOLEAN,
    is_disrupted            BOOLEAN NOT NULL DEFAULT FALSE,
    status                  TEXT NOT NULL CHECK (status IN ('IN_TRANSIT','RECEIVED')),
    is_simulated            BOOLEAN NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE analytics.fact_supplier_performance IS 'SIMULATED purchase orders / supplier delivery events';

-- ------------------------- FORECASTS ---------------------------------
-- Grain: model_level x entity x forecast origin x forecast date
CREATE TABLE IF NOT EXISTS analytics.fact_demand_forecast (
    forecast_id          BIGSERIAL PRIMARY KEY,
    run_id               TEXT NOT NULL,
    model_level          TEXT NOT NULL CHECK (model_level IN ('overall','category','product')),
    entity_id            TEXT NOT NULL,       -- 'ALL', category_name_en or product_id
    entity_name          TEXT,
    forecast_origin_date DATE NOT NULL,       -- last date of training data
    forecast_date        DATE NOT NULL,
    horizon_day          INTEGER NOT NULL,    -- 1..60
    yhat                 NUMERIC(14,3) NOT NULL,
    yhat_lower           NUMERIC(14,3) NOT NULL,
    yhat_upper           NUMERIC(14,3) NOT NULL,
    model_name           TEXT NOT NULL DEFAULT 'prophet',
    created_at           TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    UNIQUE (model_level, entity_id, forecast_origin_date, forecast_date)
);

CREATE TABLE IF NOT EXISTS analytics.forecast_accuracy (
    model_level        TEXT NOT NULL,
    entity_id          TEXT NOT NULL,
    holdout_start      DATE NOT NULL,
    holdout_end        DATE NOT NULL,
    run_id             TEXT NOT NULL,
    train_days         INTEGER NOT NULL,
    mae                NUMERIC(14,4),
    rmse               NUMERIC(14,4),
    mape               NUMERIC(14,4),         -- % on days with actual > 0
    wape               NUMERIC(14,4),         -- % sum|e| / sum(actual)
    naive_mae          NUMERIC(14,4),         -- seasonal-naive (t-7) benchmark
    weekly_seasonality BOOLEAN,
    yearly_seasonality BOOLEAN,
    created_at         TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (model_level, entity_id, holdout_end)
);

-- ------------------------- SUPPORTING --------------------------------
-- Current replenishment policy & risk per product x warehouse (recomputed every run)
CREATE TABLE IF NOT EXISTS analytics.inventory_thresholds (
    product_id             TEXT NOT NULL REFERENCES analytics.dim_product(product_id),
    warehouse_id           TEXT NOT NULL REFERENCES analytics.dim_warehouse(warehouse_id),
    supplier_id            TEXT REFERENCES analytics.dim_supplier(supplier_id),
    avg_daily_demand       NUMERIC(12,4) NOT NULL,
    demand_std             NUMERIC(12,4) NOT NULL,
    forecast_daily_demand  NUMERIC(12,4),
    planning_daily_demand  NUMERIC(12,4) NOT NULL,
    lead_time_days         NUMERIC(6,2) NOT NULL,
    lead_time_std_days     NUMERIC(6,2) NOT NULL,
    service_level          NUMERIC(5,4) NOT NULL,
    z_value                NUMERIC(6,4) NOT NULL,
    safety_stock           INTEGER NOT NULL,
    reorder_point          INTEGER NOT NULL,
    reorder_qty            INTEGER NOT NULL,
    maximum_stock          INTEGER NOT NULL,
    available_stock        INTEGER NOT NULL,
    reserved_stock         INTEGER NOT NULL,
    on_order_qty           INTEGER NOT NULL,
    net_available          INTEGER NOT NULL,
    days_of_cover          NUMERIC(10,2),
    projected_stockout_ts  TIMESTAMP,
    forecast_demand_lead_time NUMERIC(12,2),
    risk_status            TEXT NOT NULL CHECK (risk_status IN ('Critical','High Risk','Warning','Healthy')),
    recommended_action     TEXT NOT NULL,
    inventory_timestamp    TIMESTAMP NOT NULL,
    computed_at            TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    is_simulated           BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (product_id, warehouse_id)
);

CREATE TABLE IF NOT EXISTS analytics.anomaly_events (
    anomaly_id         TEXT PRIMARY KEY,            -- hash(anomaly_type, record_id)
    record_id          TEXT NOT NULL,
    anomaly_type       TEXT NOT NULL,
    detection_method   TEXT NOT NULL,               -- zscore | isolation_forest | ratio
    anomaly_score      NUMERIC(14,4) NOT NULL,
    metric_value       NUMERIC(14,4),
    expected_value     NUMERIC(14,4),
    event_date         DATE,
    detection_ts       TIMESTAMP NOT NULL,
    severity           TEXT NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    recommended_action TEXT NOT NULL,
    data_source        TEXT NOT NULL CHECK (data_source IN ('OLIST_HISTORICAL','SIMULATED')),
    details            JSONB
);

CREATE TABLE IF NOT EXISTS analytics.alert_history (
    alert_id           TEXT PRIMARY KEY,
    fingerprint        TEXT NOT NULL,              -- dedup key: type + entity
    alert_type         TEXT NOT NULL,
    severity           TEXT NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    entity_type        TEXT NOT NULL,
    entity_id          TEXT NOT NULL,
    product_id         TEXT,
    title              TEXT NOT NULL,
    explanation        TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    metrics            JSONB,
    status             TEXT NOT NULL CHECK (status IN ('ACTIVE','RESOLVED')),
    notified           BOOLEAN NOT NULL DEFAULT FALSE,
    channels           JSONB,
    occurrences        INTEGER NOT NULL DEFAULT 1,
    created_at         TIMESTAMP NOT NULL,
    last_seen_at       TIMESTAMP NOT NULL,
    resolved_at        TIMESTAMP,
    run_id             TEXT,
    is_simulated_input BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS analytics.simulation_scenarios (
    scenario_id              TEXT PRIMARY KEY,
    scenario_name            TEXT NOT NULL,
    description              TEXT,
    supplier_id              TEXT,               -- NULL = all suppliers
    supplier_delay_days      NUMERIC(6,2) NOT NULL,
    demand_growth_pct        NUMERIC(8,2) NOT NULL,
    inventory_adjustment_pct NUMERIC(8,2) NOT NULL,
    replenishment_qty_override INTEGER,
    safety_stock_multiplier  NUMERIC(6,3) NOT NULL,
    horizon_days             INTEGER NOT NULL,
    summary                  JSONB,
    created_at               TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE TABLE IF NOT EXISTS analytics.scenario_results (
    scenario_id                    TEXT NOT NULL REFERENCES analytics.simulation_scenarios(scenario_id) ON DELETE CASCADE,
    product_id                     TEXT NOT NULL,
    warehouse_id                   TEXT NOT NULL,
    variant                        TEXT NOT NULL CHECK (variant IN ('BASELINE','MODIFIED')),
    category_name_en               TEXT,
    supplier_id                    TEXT,
    starting_stock                 INTEGER NOT NULL,
    daily_demand                   NUMERIC(12,4) NOT NULL,
    lead_time_days                 NUMERIC(6,2) NOT NULL,
    replenishment_qty              INTEGER NOT NULL,
    safety_stock                   INTEGER NOT NULL,
    projected_end_stock            NUMERIC(14,2) NOT NULL,
    min_projected_stock            NUMERIC(14,2) NOT NULL,
    stockout_date                  DATE,
    days_until_stockout            INTEGER,
    unfulfilled_units              NUMERIC(14,2) NOT NULL,
    additional_cover_days_required NUMERIC(10,2) NOT NULL,
    additional_units_required      NUMERIC(14,2) NOT NULL,
    unit_price_proxy               NUMERIC(12,2) NOT NULL,
    revenue_exposure_proxy         NUMERIC(14,2) NOT NULL,   -- POTENTIAL exposure, not confirmed lost revenue
    is_affected                    BOOLEAN NOT NULL,
    PRIMARY KEY (scenario_id, product_id, warehouse_id, variant)
);

CREATE TABLE IF NOT EXISTS analytics.scenario_projection (
    scenario_id        TEXT NOT NULL REFERENCES analytics.simulation_scenarios(scenario_id) ON DELETE CASCADE,
    variant            TEXT NOT NULL,
    day_offset         INTEGER NOT NULL,
    projection_date    DATE NOT NULL,
    projected_inventory NUMERIC(16,2) NOT NULL,
    unfulfilled_units  NUMERIC(16,2) NOT NULL,
    products_out_of_stock INTEGER NOT NULL,
    PRIMARY KEY (scenario_id, variant, day_offset)
);

CREATE TABLE IF NOT EXISTS analytics.etl_run_log (
    run_id        TEXT NOT NULL,
    task_name     TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TIMESTAMP NOT NULL,
    finished_at   TIMESTAMP,
    duration_sec  NUMERIC(12,3),
    rows_processed BIGINT,
    message       TEXT,
    PRIMARY KEY (run_id, task_name)
);

CREATE TABLE IF NOT EXISTS analytics.data_quality_log (
    dq_id         BIGSERIAL PRIMARY KEY,
    run_id        TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    check_name    TEXT NOT NULL,
    column_name   TEXT,
    status        TEXT NOT NULL,
    severity      TEXT NOT NULL,
    failing_count BIGINT NOT NULL,
    total_count   BIGINT NOT NULL,
    failing_pct   NUMERIC(10,4),
    details       TEXT,
    checked_at    TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.simulation_state (
    state_key   TEXT PRIMARY KEY,
    state_value JSONB NOT NULL,
    updated_at  TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
