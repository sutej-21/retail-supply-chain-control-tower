"""Airflow DAG: retail_supply_chain_etl (hourly).

The Olist dataset is STATIC historical data. Each hourly run:
  * re-validates the source files and skips raw/staging/transform work when the
    file checksums are unchanged (incremental, idempotent);
  * advances the SIMULATED operational feed (inventory, suppliers, POs) to the
    run's data-interval end;
  * recomputes inventory KPIs, anomalies, forecasts (when stale) and alerts.
It does NOT imply that Kaggle delivers new live transactions.

All business logic lives in ``src.pipeline.tasks`` so the same code runs locally
(``python -m src.pipeline.run_pipeline``) and in Airflow.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule

PROJECT_ROOT = os.getenv("PROJECT_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.alerts.notification_service import send_failure_notification  # noqa: E402
from src.pipeline import tasks  # noqa: E402
from src.utils.helpers import json_default  # noqa: E402

DEFAULT_ARGS = {
    "owner": "supply-chain-analytics",
    "depends_on_past": False,
    "retries": int(os.getenv("AIRFLOW_TASK_RETRIES", "2")),
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "execution_timeout": timedelta(minutes=45),
    "on_failure_callback": send_failure_notification,
}


def _run(task_fn, **context):
    """Execute a pipeline task with the Airflow run id; the simulation uses the data-interval end."""
    run_id = context["run_id"]
    if task_fn is tasks.generate_inventory_simulation:
        until = context["data_interval_end"]
        result = task_fn(run_id, until=datetime(until.year, until.month, until.day, until.hour))
    else:
        result = task_fn(run_id)
    return json.loads(json.dumps(result, default=json_default))  # XCom-safe (no numpy / datetime objects)


def _log_results(**context):
    return tasks.log_execution_results(context["run_id"])


with DAG(
    dag_id="retail_supply_chain_etl",
    description="Retail & Supply Chain Control Tower: Olist ETL + simulated operations + ML + alerts",
    default_args=DEFAULT_ARGS,
    schedule="@hourly",
    start_date=datetime(2026, 1, 1),
    catchup=False,                # the simulation catches up by itself (bounded), no backfill storm
    max_active_runs=1,            # runs must be sequential: the simulation clock is stateful
    dagrun_timeout=timedelta(hours=2),
    tags=["olist", "supply-chain", "control-tower"],
    doc_md=__doc__,
) as dag:
    ops = {}
    for fn in tasks.PIPELINE_ORDER:
        ops[fn.__name__] = PythonOperator(
            task_id=fn.__name__,
            python_callable=_run,
            op_kwargs={"task_fn": fn},
        )

    log_results = PythonOperator(
        task_id="log_execution_results",
        python_callable=_log_results,
        trigger_rule=TriggerRule.ALL_DONE,   # always summarise, even after failures
        retries=0,
    )

    (
        ops["check_source_files"]
        >> ops["load_csv_files"]
        >> ops["validate_source_data"]
        >> ops["clean_and_preprocess"]
        >> ops["load_raw_tables"]
        >> ops["populate_staging"]
        >> ops["run_sql_transformations"]
        >> ops["generate_inventory_simulation"]
        >> ops["update_inventory_kpis"]
        >> ops["run_anomaly_detection"]
        >> ops["update_demand_forecasts"]
        >> ops["evaluate_alert_thresholds"]
        >> ops["send_notifications"]
        >> log_results
    )
