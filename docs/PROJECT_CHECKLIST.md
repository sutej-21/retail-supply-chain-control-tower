# Project Checklist and Validation Status

Validation environment: Python 3.11.15 with the pinned `requirements.txt`, PostgreSQL 16.15, Apache Airflow
2.10.5 and Apache Superset 4.1.2, all installed natively (no Docker daemon was available).
Data: the **synthetic Olist-schema sample** (30,000 orders). The Kaggle download was not reachable from the build
environment.

| # | Phase / deliverable | Status | How it was verified |
|---|---|---|---|
| 1 | Project setup, Kaggle API + manual acquisition, read-only originals + manifest | Done | `test_ingestion.py` (manual zip path); the Kaggle API path needs your credentials |
| 2 | Profiling, data-quality report, cleaning log (flag, don't delete) | Done | Reports generated; `test_data_quality.py` detects every planted issue |
| 3 | PostgreSQL DDL (`sql/01-09`): raw / staging / analytics, PKs, FKs, indexes, views | Done | Built from scratch on PostgreSQL 16; FK-integrity tests |
| 4 | Ingestion and transformations (idempotent, no fan-out) | Done | Re-runs keep identical counts; revenue/payment reconciliation tests |
| 5 | Inventory and supplier simulation (seeded, hourly, catch-up) | Done | Idempotency and incremental tests; 102k simulated records |
| 6 | Airflow DAG `retail_supply_chain_etl` (14 tasks, hourly, retries, failure callback) | Done | `airflow dags test` on Airflow 2.10.5: all 14 tasks SUCCESS |
| 7 | KPIs (SQL views + Python) | Done | SQL-vs-Python parity test; all queries in `08_kpi_queries.sql` execute |
| 8 | Anomaly detection (Z-score, Isolation Forest, 7 types) | Done | Planted spike, sales drop, seller delay and 25-item order all detected |
| 9 | Prophet forecasting (7/30/60 days, intervals, hold-out metrics) | Done | 30 models on the sample; leakage and shape tests |
| 10 | Inventory optimisation and what-if scenarios | Done | Formula unit tests; 7-day delay scenario gives measurable deltas |
| 11 | Slack / email / local notifications, dedup, history | Done | Local mode end to end; Slack mocked; SMTP not sent (no server available) |
| 12 | Five Superset dashboards (as code) | Done | Bootstrapped on Superset 4.1.2; all charts render without errors (screenshots in `docs/images`) |
| 13 | Docker Compose (Postgres, Airflow, Superset) | Written, **not executed** | `docker compose config` is valid. Please run `docker compose up -d --build` once and report any issue |
| 13 | Automated tests | Done | 77 passed (unit + integration) |
| 14 | README, architecture, ER diagram, data dictionary, KPI definitions, methodology, license, notebooks | Done | All 4 notebooks execute without errors |

## To do on your machine
1. Download the real Olist data (Kaggle API or manual zip) and run the pipeline with `DATA_SOURCE=kaggle`.
2. `docker compose up -d --build`, unpause the DAG, then run `./scripts/bootstrap_superset.sh`.
3. Replace the screenshots in `docs/images` with screenshots from the real-data run, and quote the accuracy figures
   from `analytics.forecast_accuracy`.
