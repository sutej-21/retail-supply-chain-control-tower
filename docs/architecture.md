# Architecture

## End-to-end flow

```mermaid
flowchart TD
    K[Kaggle: Olist Brazilian E-Commerce<br/>9 CSV files, CC BY-NC-SA 4.0] -->|kaggle API / manual zip| R[data/raw<br/>read-only originals + SHA-256 manifest]
    S[src/ingestion/sample_data.py<br/>SYNTHETIC Olist-schema sample] -.->|DATA_SOURCE=sample| R2[data/sample]
    R --> V[Profiling & data-quality checks<br/>reports/data_quality]
    R2 --> V
    V --> C[Cleaning & standardisation<br/>flags, not deletes - cleaning_log.md]
    C --> P[data/processed]
    R --> RAW[(PostgreSQL raw.*<br/>TEXT copy + lineage)]
    P --> STG[(staging.*<br/>typed, cleaned)]
    STG -->|09_transform SQL<br/>idempotent upserts| AN[(analytics.*<br/>star schema + KPI views)]
    AN --> SIM[Inventory & supplier simulator<br/>SYNTHETIC, seeded, hourly]
    SIM --> AN
    AN --> OPT[Inventory optimisation<br/>SS / ROP / reorder qty / risk]
    AN --> ML1[Anomaly detection<br/>Z-score + Isolation Forest]
    AN --> ML2[Prophet demand forecasting<br/>7 / 30 / 60 days]
    ML2 --> OPT
    OPT --> AN
    ML1 --> AN
    ML2 --> AN
    AN --> ALR[Alert engine<br/>dedup + history]
    ALR --> N[Slack / SMTP email / local log]
    AN --> SC[What-if scenarios]
    SC --> AN
    AN --> BI[Apache Superset<br/>5 dashboards]
    AF[[Apache Airflow DAG<br/>retail_supply_chain_etl - hourly]] -. orchestrates .-> V & C & RAW & STG & AN & SIM & OPT & ML1 & ML2 & ALR
```

## Runtime components (Docker Compose)

| Service | Image | Port | Purpose |
|---|---|---|---|
| `postgres` | postgres:16 | 5432 | Project DB `retail_supply_chain` + Airflow & Superset metadata DBs (created by `docker/postgres-init`) |
| `airflow-init` | built from `Dockerfile` (apache/airflow:2.10.5-python3.11) | – | `airflow db migrate` + admin user |
| `airflow-webserver` | same | 8080 | Airflow UI |
| `airflow-scheduler` | same | – | Runs the hourly DAG (LocalExecutor) |
| `superset` | built from `docker/superset/Dockerfile` (apache/superset:4.1.2 + psycopg2) | 8088 | Dashboards |

The project folder is mounted into the Airflow containers at `/opt/project`; the DAG imports the same
`src.pipeline.tasks` functions that the local CLI (`python -m src.pipeline.run_pipeline`) uses.

## Database layers

| Schema | Content | Load strategy |
|---|---|---|
| `raw` | Olist CSVs as TEXT + `_batch_id`, `_source_file`, `_loaded_at`; `raw.load_audit` | TRUNCATE + COPY per table in one transaction; skipped if checksum unchanged |
| `staging` | Typed, cleaned, standardised Olist data | TRUNCATE + COPY of all 9 tables in one transaction |
| `analytics` | Dimensions, facts, supporting tables, KPI views, ML outputs, simulated data | `INSERT ... ON CONFLICT DO UPDATE` (upserts) + deterministic IDs |

## ER diagram (analytics layer)

```mermaid
erDiagram
    dim_date ||--o{ fact_orders : purchase_date_key
    dim_customer ||--o{ fact_orders : customer_id
    fact_orders ||--|{ fact_order_items : order_id
    fact_orders ||--o{ fact_payments : order_id
    fact_orders ||--o{ fact_reviews : order_id
    fact_orders ||--o{ fact_shipments : "order_id (OUTBOUND)"
    dim_product ||--o{ fact_order_items : product_id
    dim_seller ||--o{ fact_order_items : seller_id
    dim_seller ||--o{ fact_shipments : seller_id
    dim_product ||--|| bridge_product_supplier : "product_id [SIM]"
    dim_supplier ||--o{ bridge_product_supplier : supplier_id
    dim_warehouse ||--o{ bridge_product_supplier : warehouse_id
    dim_product ||--o{ fact_inventory : "product_id [SIM]"
    dim_warehouse ||--o{ fact_inventory : warehouse_id
    dim_product ||--o{ fact_inventory_movements : "[SIM]"
    dim_supplier ||--o{ fact_supplier_performance : "supplier_id [SIM]"
    fact_supplier_performance ||--|| fact_shipments : "po_id (INBOUND) [SIM]"
    dim_product ||--o{ inventory_thresholds : product_id
    dim_warehouse ||--o{ inventory_thresholds : warehouse_id
    dim_product ||--o{ fact_demand_forecast : "entity_id (product level)"
    simulation_scenarios ||--o{ scenario_results : scenario_id
    simulation_scenarios ||--o{ scenario_projection : scenario_id
    dim_product ||--o{ alert_history : product_id

    fact_orders {
        text order_id PK
        text customer_id FK
        text customer_unique_id
        date purchase_date
        numeric items_value
        numeric payment_value
        boolean is_late
        text delivery_status
        smallint review_score
    }
    fact_order_items {
        text order_id PK
        int order_item_id PK
        text product_id FK
        text seller_id FK
        numeric price
    }
    fact_inventory {
        bigint inventory_id PK
        text product_id FK
        text warehouse_id FK
        int available_stock
        int reserved_stock
        timestamp inventory_timestamp
        boolean is_simulated
    }
    fact_supplier_performance {
        text po_id PK
        text supplier_id FK
        numeric simulated_lead_time_days
        text status
    }
    fact_demand_forecast {
        text model_level
        text entity_id
        date forecast_origin_date
        date forecast_date
        numeric yhat
    }
```

## Key design decisions

| Decision | Reason |
|---|---|
| Raw layer stores TEXT | Faithful, re-playable copy; no load fails because of a single bad value; type problems are measured by validation instead. |
| Flag, don't delete | Bad timestamps / zero prices are kept and flagged so revenue is not silently lost; KPIs exclude them only where they would distort the metric (documented). |
| Aggregate children before joining | `fact_orders` gets item / payment totals from pre-aggregated CTEs, so an order with 3 items and 2 payments is never counted 6 times. |
| Shipments at order x seller grain | An Olist order can be fulfilled by several sellers; seller delivery KPIs need that grain. |
| Separate simulation clock | Olist ends in 2018; operational simulation runs on the pipeline's UTC hour and is clearly labelled `is_simulated`. |
| Checksum-based incremental loads | The Olist files are static; hourly runs skip unchanged loads but still refresh simulated feeds, KPIs and alerts. |
| One task module for Airflow and CLI | The DAG is a thin wrapper; all logic is unit-/integration-tested without Airflow. |
| Superset built through the REST API | Dashboards are reproducible code (`superset/bootstrap_dashboards.py`), not manual clicks. |
