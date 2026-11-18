"""Shared fixtures.

* Unit tests use a small SYNTHETIC Olist-schema dataset generated on the fly
  (no Kaggle download required).
* Integration tests use a dedicated PostgreSQL database
  ``<POSTGRES_DB>_test`` (default ``retail_supply_chain_test``) which is
  dropped and re-created per test session. They are skipped with a clear
  message when PostgreSQL is not reachable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def tmp_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("control_tower")


@pytest.fixture(scope="session", autouse=True)
def isolated_paths(tmp_root):
    """Redirect every writable path to a temp folder so tests never touch project data."""
    env = {
        "SAMPLE_DATA_DIR": str(tmp_root / "sample"),
        "RAW_DATA_DIR": str(tmp_root / "raw"),
        "PROCESSED_DATA_DIR": str(tmp_root / "processed"),
        "SIMULATED_DATA_DIR": str(tmp_root / "simulated"),
        "REPORTS_DIR": str(tmp_root / "reports"),
        "LOGS_DIR": str(tmp_root / "logs"),
        "DATA_SOURCE": "sample",
        "ALERT_MODE": "local",
        "SIM_N_PRODUCTS": "60",
        "SIM_BOOTSTRAP_HOURS": "72",
        "FORECAST_TOP_N_CATEGORIES": "2",
        "FORECAST_TOP_N_PRODUCTS": "2",
        "FORECAST_MIN_NONZERO_SHARE": "0.2",
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="session")
def sample_frames(isolated_paths, tmp_root):
    from src.ingestion.sample_data import generate_sample_dataset
    return generate_sample_dataset(tmp_root / "sample", n_orders=12000, n_products=250, n_sellers=50, seed=11)


@pytest.fixture(scope="session")
def raw_frames(sample_frames):
    from src.ingestion.load_data import load_source_csvs
    return load_source_csvs(as_text=True)


@pytest.fixture(scope="session")
def cleaned(raw_frames):
    from src.preprocessing.clean_data import clean_all
    frames, log = clean_all(raw_frames)
    return frames, log


# --------------------------------------------------------------------------- #
# PostgreSQL integration fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def pg_engine(isolated_paths):
    from sqlalchemy import create_engine, text

    if not (os.getenv("POSTGRES_PASSWORD") or os.getenv("DATABASE_URL")):
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
    base_db = os.getenv("POSTGRES_DB", "retail_supply_chain")
    test_db = base_db if base_db.endswith("_test") else f"{base_db}_test"
    try:
        from config import database
        admin = create_engine(database.get_database_url("postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :d"),
                         {"d": test_db})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db}"'))
            conn.execute(text(f'CREATE DATABASE "{test_db}"'))
        admin.dispose()
    except Exception as exc:  # pragma: no cover - depends on environment
        pytest.skip(f"PostgreSQL not available for integration tests: {exc}")
    os.environ["POSTGRES_DB"] = test_db
    os.environ.pop("DATABASE_URL", None)
    from config.database import get_engine, reset_engine_cache
    reset_engine_cache()
    engine = get_engine()
    yield engine
    engine.dispose()
    reset_engine_cache()
    os.environ["POSTGRES_DB"] = base_db


@pytest.fixture(scope="session")
def built_db(pg_engine, sample_frames, cleaned):
    """Schema + raw + staging + analytics model built from the sample data."""
    from src.ingestion.load_data import load_raw_tables
    from src.transformations.build_analytics_model import init_database_schema, load_staging, run_transformations
    init_database_schema(pg_engine, create_database=False)
    load_raw_tables(engine=pg_engine)
    load_staging(cleaned[0], pg_engine)
    run_transformations(pg_engine)
    return pg_engine


@pytest.fixture(scope="session")
def simulated_db(built_db):
    from datetime import datetime
    from src.optimization.inventory_optimization import update_inventory_thresholds
    from src.simulation.inventory_simulator import InventorySimulator
    InventorySimulator(built_db).run(datetime(2026, 1, 10, 12))
    update_inventory_thresholds(built_db)
    return built_db
