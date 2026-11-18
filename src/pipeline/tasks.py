"""Pipeline task functions shared by the Airflow DAG and the local CLI runner.

Every task:
* takes a ``run_id`` and returns a small JSON-serialisable dict (safe for XCom);
* passes data through files/PostgreSQL, never through XCom;
* is idempotent - re-running it never duplicates records;
* is logged to ``analytics.etl_run_log`` (status, duration, rows, message).

Incremental processing: the SHA-256 checksums of the nine source files are
stored after a successful transformation. When they are unchanged, the raw /
staging / transformation tasks are skipped (the Olist data is static), while the
simulation, KPIs, anomalies, alerts and (when stale) forecasts still refresh.
Set ``FORCE_RELOAD=true`` to force a full reload.
"""
from __future__ import annotations

import functools
import json
import os
import time
from datetime import datetime

from config.database import execute, get_engine, read_sql
from config.settings import get_settings
from src.utils.helpers import utc_now, write_json
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def _force_reload() -> bool:
    return os.getenv("FORCE_RELOAD", "false").lower() in {"1", "true", "yes"}


def _state(key: str):
    try:
        df = read_sql("SELECT state_value FROM analytics.simulation_state WHERE state_key = :k", {"k": key})
    except Exception:  # schema not created yet
        return None
    if df.empty:
        return None
    v = df.iloc[0, 0]
    return json.loads(v) if isinstance(v, str) else v


def _set_state(key: str, value) -> None:
    execute("""INSERT INTO analytics.simulation_state (state_key, state_value, updated_at)
               VALUES (:k, CAST(:v AS JSONB), now() AT TIME ZONE 'utc')
               ON CONFLICT (state_key) DO UPDATE SET state_value = EXCLUDED.state_value,
                                                     updated_at = EXCLUDED.updated_at""",
            {"k": key, "v": json.dumps(value, default=str)})


def _log(run_id: str, task: str, status: str, started: datetime, rows=None, message: str | None = None) -> None:
    try:
        finished = utc_now()
        execute("""INSERT INTO analytics.etl_run_log (run_id, task_name, status, started_at, finished_at, duration_sec,
                                                      rows_processed, message)
                   VALUES (:r, :t, :s, :st, :f, :d, :n, :m)
                   ON CONFLICT (run_id, task_name) DO UPDATE SET status = EXCLUDED.status,
                       started_at = EXCLUDED.started_at, finished_at = EXCLUDED.finished_at,
                       duration_sec = EXCLUDED.duration_sec, rows_processed = EXCLUDED.rows_processed,
                       message = EXCLUDED.message""",
                {"r": run_id, "t": task, "s": status, "st": started, "f": finished,
                 "d": (finished - started).total_seconds(), "n": rows, "m": (message or "")[:4000]})
    except Exception as exc:  # the log table may not exist before the schema task
        logger.debug("etl_run_log not written for %s: %s", task, exc)


def logged_task(name: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(run_id: str, **kwargs):
            started = utc_now()
            t0 = time.perf_counter()
            logger.info("[%s] task %s started", run_id, name)
            try:
                result = fn(run_id, **kwargs) or {}
            except Exception as exc:
                _log(run_id, name, "FAILED", started, message=f"{exc.__class__.__name__}: {exc}")
                logger.exception("[%s] task %s failed", run_id, name)
                raise
            status = "SKIPPED" if result.get("skipped") else "SUCCESS"
            _log(run_id, name, status, started, result.get("rows"), json.dumps(result, default=str))
            logger.info("[%s] task %s %s in %.1fs", run_id, name, status, time.perf_counter() - t0)
            return result
        return wrapper
    return deco


def _source_changed() -> bool:
    from src.ingestion.load_data import source_checksums
    return _force_reload() or _state("etl_source_checksums") != source_checksums()


# ------------------------------------------------------------------------- #
@logged_task("check_source_files")
def check_source_files(run_id: str) -> dict:
    from src.ingestion.kaggle_download import resolve_source_dir, verify_source_files
    from src.transformations.build_analytics_model import init_database_schema
    paths = verify_source_files()  # raises MissingDataError with instructions
    init_database_schema()         # idempotent; makes logging/state tables available
    return {"source_dir": str(resolve_source_dir()), "files": len(paths), "source_changed": _source_changed()}


@logged_task("load_csv_files")
def load_csv_files(run_id: str) -> dict:
    from src.ingestion.load_data import load_source_csvs
    frames = load_source_csvs(as_text=True)
    return {"rows": sum(len(df) for df in frames.values()),
            "tables": {k: len(df) for k, df in frames.items()}}


@logged_task("validate_source_data")
def validate_source_data(run_id: str) -> dict:
    from src.ingestion.load_data import load_source_csvs
    from src.preprocessing.data_validation import build_report
    report = build_report(load_source_csvs(as_text=True), engine=get_engine(), run_id=run_id,
                          fail_on_critical=True)
    return {"summary": report["summary"]}


@logged_task("clean_and_preprocess")
def clean_and_preprocess(run_id: str) -> dict:
    from src.ingestion.load_data import load_source_csvs
    from src.preprocessing.clean_data import clean_all, write_processed
    processed = get_settings().paths.processed_dir
    if not _source_changed() and (processed / "orders.csv").exists():
        return {"skipped": True, "reason": "source unchanged"}
    cleaned, log = clean_all(load_source_csvs(as_text=True))
    write_processed(cleaned, log)
    return {"rows": sum(len(df) for df in cleaned.values()), "rules_logged": len(log.entries)}


@logged_task("load_raw_tables")
def load_raw_tables(run_id: str) -> dict:
    from src.ingestion.load_data import load_raw_tables as _load
    res = _load(force=_force_reload())
    return {"rows": sum(r["rows"] or 0 for r in res.values()),
            "skipped_tables": [k for k, r in res.items() if r["skipped"]]}


@logged_task("populate_staging")
def populate_staging(run_id: str) -> dict:
    from src.preprocessing.clean_data import read_processed
    from src.transformations.build_analytics_model import load_staging
    staged = read_sql("SELECT COUNT(*) AS n FROM staging.orders").iloc[0, 0]
    if not _source_changed() and staged > 0:
        return {"skipped": True, "reason": "source unchanged"}
    counts = load_staging(read_processed())
    return {"rows": sum(counts.values()), "tables": counts}


@logged_task("run_sql_transformations")
def run_sql_transformations(run_id: str) -> dict:
    from src.ingestion.load_data import source_checksums
    from src.transformations.build_analytics_model import run_transformations
    built = read_sql("SELECT COUNT(*) AS n FROM analytics.fact_orders").iloc[0, 0]
    if not _source_changed() and built > 0:
        return {"skipped": True, "reason": "source unchanged"}
    counts = run_transformations()
    _set_state("etl_source_checksums", source_checksums())
    _set_state("forecast_invalidated", True)   # new history => forecasts must be retrained
    return {"rows": sum(counts.values()), "tables": counts}


@logged_task("generate_inventory_simulation")
def generate_inventory_simulation(run_id: str, until: datetime | None = None) -> dict:
    from src.simulation.inventory_simulator import run_simulation
    r = run_simulation(until)
    return {"rows": r.snapshots + r.movements, "hours": r.hours_simulated, "pos_created": r.purchase_orders_created,
            "receipts": r.receipts, "bootstrapped": r.bootstrapped, "sim_until": str(r.end_ts)}


@logged_task("update_inventory_kpis")
def update_inventory_kpis(run_id: str) -> dict:
    from src.optimization.inventory_optimization import summarize, update_inventory_thresholds
    from src.transformations.kpi_calculations import fetch_kpi_snapshot
    policy = update_inventory_thresholds()
    fetch_kpi_snapshot()
    return {"rows": len(policy), **summarize(policy)}


@logged_task("run_anomaly_detection")
def run_anomaly_detection(run_id: str) -> dict:
    from src.ml.anomaly_detection import run_anomaly_detection as _run
    res = _run()
    by_type = res.groupby("anomaly_type").size().to_dict() if not res.empty else {}
    return {"rows": len(res), "by_type": by_type}


@logged_task("update_demand_forecasts")
def update_demand_forecasts(run_id: str) -> dict:
    from src.ml.demand_forecasting import run_forecasting
    from src.optimization.inventory_optimization import update_inventory_thresholds
    cfg = get_settings().forecast
    last = _state("forecast_run")
    stale = last is None or (utc_now() - datetime.fromisoformat(last["at"])).total_seconds() > cfg.refresh_hours * 3600
    if not (stale or _force_reload() or _state("forecast_invalidated")):
        return {"skipped": True, "reason": f"forecasts younger than {cfg.refresh_hours}h"}
    summary = run_forecasting()
    _set_state("forecast_run", {"at": utc_now().isoformat(), "run_id": summary["run_id"]})
    _set_state("forecast_invalidated", False)
    update_inventory_thresholds()  # policies now use the new forecasts
    return {"rows": summary["models"] * 60, "models": summary["models"], "skipped_series": len(summary["skipped"]),
            "forecast_origin": summary["forecast_origin"]}


@logged_task("evaluate_alert_thresholds")
def evaluate_alert_thresholds(run_id: str) -> dict:
    from src.alerts.notification_service import evaluate_and_record_alerts
    return evaluate_and_record_alerts(run_id=run_id, send=False)


@logged_task("send_notifications")
def send_notifications(run_id: str) -> dict:
    from src.alerts.notification_service import send_pending_notifications
    return send_pending_notifications()


def log_execution_results(run_id: str) -> dict:
    """Summarise the run (always executed, even after failures)."""
    df = read_sql("SELECT task_name, status, duration_sec, rows_processed FROM analytics.etl_run_log "
                  "WHERE run_id = :r ORDER BY started_at", {"r": run_id})
    summary = {"run_id": run_id, "finished_at": utc_now(), "tasks": df.to_dict(orient="records"),
               "failed": df.loc[df["status"] == "FAILED", "task_name"].tolist()}
    write_json(summary, get_settings().paths.reports_dir / "runs" / f"{run_id}.json")
    started = utc_now()
    _log(run_id, "log_execution_results", "SUCCESS" if not summary["failed"] else "COMPLETED_WITH_FAILURES",
         started, len(df), json.dumps({"failed": summary["failed"]}))
    logger.info("Run %s summary: %s", run_id, df.to_string(index=False))
    return {"tasks": len(df), "failed": summary["failed"]}


PIPELINE_ORDER = [
    check_source_files, load_csv_files, validate_source_data, clean_and_preprocess, load_raw_tables,
    populate_staging, run_sql_transformations, generate_inventory_simulation, update_inventory_kpis,
    run_anomaly_detection, update_demand_forecasts, evaluate_alert_thresholds, send_notifications,
]
