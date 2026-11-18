"""PostgreSQL connectivity helpers (SQLAlchemy engine + psycopg2 COPY).

Connection parameters come exclusively from environment variables:

* ``DATABASE_URL`` (takes precedence), or
* ``POSTGRES_HOST``, ``POSTGRES_PORT``, ``POSTGRES_DB``, ``POSTGRES_USER``,
  ``POSTGRES_PASSWORD``.
"""
from __future__ import annotations

import io
import logging
import os
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Sequence

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL

from config.settings import PROJECT_ROOT  # noqa: F401  (ensures .env is loaded)

logger = logging.getLogger(__name__)

DEFAULT_DB_NAME = "retail_supply_chain"


class DatabaseConfigError(RuntimeError):
    """Raised when the database is not configured or not reachable."""


def _connection_parts(database: str | None = None) -> dict:
    password = os.getenv("POSTGRES_PASSWORD")
    if password is None:
        raise DatabaseConfigError(
            "POSTGRES_PASSWORD is not set. Copy .env.example to .env and fill in the "
            "database credentials (or set DATABASE_URL)."
        )
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "database": database or os.getenv("POSTGRES_DB", DEFAULT_DB_NAME),
        "username": os.getenv("POSTGRES_USER", "postgres"),
        "password": password,
    }


def get_database_url(database: str | None = None) -> URL | str:
    url = os.getenv("DATABASE_URL")
    if url and database is None:
        return url
    return URL.create("postgresql+psycopg2", **_connection_parts(database))


@lru_cache(maxsize=4)
def get_engine(database: str | None = None) -> Engine:
    """Return a pooled SQLAlchemy engine (cached per database name)."""
    return create_engine(get_database_url(database), pool_pre_ping=True, future=True)


def reset_engine_cache() -> None:
    get_engine.cache_clear()


def check_connection(engine: Engine | None = None) -> bool:
    engine = engine or get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - depends on infrastructure
        raise DatabaseConfigError(f"Cannot connect to PostgreSQL: {exc}") from exc


def ensure_database_exists(db_name: str | None = None) -> bool:
    """Create the project database if it does not exist. Returns True if created."""
    db_name = db_name or os.getenv("POSTGRES_DB", DEFAULT_DB_NAME)
    admin = create_engine(get_database_url("postgres"), isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": db_name}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                logger.info("Created database %s", db_name)
                return True
    finally:
        admin.dispose()
    return False


@contextmanager
def raw_connection(engine: Engine | None = None) -> Iterator:
    """Yield a DB-API (psycopg2) connection; commits on success, rolls back on error."""
    engine = engine or get_engine()
    conn = engine.raw_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_sql_file(path: Path | str, engine: Engine | None = None) -> None:
    """Execute a SQL script (multiple statements) in a single transaction."""
    path = Path(path)
    sql = path.read_text(encoding="utf-8")
    with raw_connection(engine) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    logger.info("Executed %s", path.name)


def execute(sql: str, params: dict | None = None, engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def read_sql(sql: str, params: dict | None = None, engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_engine()
    with engine.connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params or {})


def copy_dataframe(
    df: pd.DataFrame,
    table: str,
    columns: Sequence[str] | None = None,
    conn=None,
    engine: Engine | None = None,
) -> int:
    """Bulk load a DataFrame with PostgreSQL ``COPY ... FROM STDIN`` (CSV).

    ``table`` may be schema-qualified. If ``conn`` (psycopg2) is given the copy
    joins the caller's transaction; otherwise it runs in its own transaction.
    """
    columns = list(columns or df.columns)
    if df.empty:
        return 0
    buffer = io.StringIO()
    df[columns].to_csv(buffer, index=False, header=False, na_rep="\\N", date_format="%Y-%m-%d %H:%M:%S")
    buffer.seek(0)
    col_sql = ", ".join(f'"{c}"' for c in columns)
    copy_sql = f"COPY {table} ({col_sql}) FROM STDIN WITH (FORMAT csv, NULL '\\N')"

    def _do(c):
        with c.cursor() as cur:
            cur.copy_expert(copy_sql, buffer)

    if conn is not None:
        _do(conn)
    else:
        with raw_connection(engine) as own:
            _do(own)
    return len(df)


def upsert_dataframe(
    df: pd.DataFrame,
    table: str,
    key_columns: Sequence[str],
    engine: Engine | None = None,
    update_columns: Sequence[str] | None = None,
    conn=None,
) -> int:
    """Idempotent upsert via a temporary table + ``INSERT ... ON CONFLICT``.

    Pass ``conn`` (psycopg2) to join an existing transaction.
    """
    if df.empty:
        return 0
    columns = list(df.columns)
    update_columns = [c for c in (update_columns or columns) if c not in key_columns]
    tmp = "tmp_upsert_" + table.replace(".", "_")
    col_sql = ", ".join(f'"{c}"' for c in columns)
    conflict = ", ".join(f'"{c}"' for c in key_columns)
    if update_columns:
        action = "DO UPDATE SET " + ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_columns)
    else:
        action = "DO NOTHING"
    def _do(c) -> int:
        with c.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {tmp}")
            cur.execute(f"CREATE TEMP TABLE {tmp} (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
        copy_dataframe(df, tmp, columns, conn=c)
        with c.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table} ({col_sql}) SELECT {col_sql} FROM {tmp} "
                f"ON CONFLICT ({conflict}) {action}"
            )
            n = cur.rowcount
            cur.execute(f"DROP TABLE IF EXISTS {tmp}")
        return n

    if conn is not None:
        return _do(conn)
    with raw_connection(engine) as own:
        return _do(own)
