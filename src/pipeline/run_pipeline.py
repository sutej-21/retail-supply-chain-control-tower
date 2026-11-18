"""Run the complete pipeline locally (same task code as the Airflow DAG).

Usage:
    python -m src.pipeline.run_pipeline                 # full run
    python -m src.pipeline.run_pipeline --until 2026-11-18T10:00
    FORCE_RELOAD=true python -m src.pipeline.run_pipeline
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from src.pipeline import tasks
from src.utils.helpers import utc_now
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def run(until: datetime | None = None, run_id: str | None = None, skip: set[str] | None = None) -> dict:
    run_id = run_id or f"manual__{utc_now():%Y%m%dT%H%M%S}"
    skip = skip or set()
    results, failed = {}, None
    for task in tasks.PIPELINE_ORDER:
        name = task.__name__
        if name in skip:
            continue
        try:
            kwargs = {"until": until} if name == "generate_inventory_simulation" else {}
            results[name] = task(run_id, **kwargs)
        except Exception as exc:  # stop on the first failure, like Airflow's default trigger rule
            failed = (name, exc)
            break
    try:
        results["log_execution_results"] = tasks.log_execution_results(run_id)
    except Exception as exc:  # pragma: no cover
        logger.error("Could not write the run summary: %s", exc)
    if failed:
        logger.error("Pipeline failed at %s: %s", failed[0], failed[1])
        raise SystemExit(1)
    return {"run_id": run_id, "results": results}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the Retail & Supply Chain Control Tower pipeline")
    ap.add_argument("--until", help="simulate operations up to this UTC hour (ISO format)")
    ap.add_argument("--skip", nargs="*", default=[], help="task names to skip")
    a = ap.parse_args()
    out = run(datetime.fromisoformat(a.until) if a.until else None, skip=set(a.skip))
    for name, res in out["results"].items():
        print(f"{name:32s} {res}")
    sys.exit(0)


if __name__ == "__main__":
    main()
