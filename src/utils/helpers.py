"""Small reusable helpers shared across modules."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


class MissingDataError(FileNotFoundError):
    """Raised when required input data is missing. The message explains how to fix it."""


def utc_now() -> datetime:
    """Timezone-naive UTC timestamp (PostgreSQL columns are TIMESTAMP WITHOUT TIME ZONE)."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(*parts: Any, length: int = 16) -> str:
    """Deterministic short hash used for alert fingerprints and IDs."""
    payload = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def write_json(data: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=json_default), encoding="utf-8")
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def json_default(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    try:
        if denominator in (0, None) or pd.isna(denominator):
            return default
        return float(numerator) / float(denominator)
    except (TypeError, ZeroDivisionError):
        return default


def chunked(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def to_records_json(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    """Serialise selected columns of each row to a JSON string (for JSONB columns)."""
    return df[columns].apply(lambda r: json.dumps(r.to_dict(), default=json_default), axis=1)


def effective_data_end(daily: pd.Series, ratio: float = 0.10, lookback: int = 180) -> pd.Timestamp:
    """Last date before an incomplete trailing tail.

    Olist's final weeks contain only a handful of orders (data-extraction
    cut-off). The reference level is the median of the non-zero days in the
    last ``lookback`` days; the effective end is the last date whose volume is at
    least ``ratio`` x that level. ``daily`` is indexed by date (zeros filled).
    """
    s = daily.sort_index()
    recent = s.iloc[-lookback:]
    nonzero = recent[recent > 0]
    if nonzero.empty:
        return s.index.max()
    threshold = ratio * float(nonzero.median())
    ok = s[s >= threshold]
    return ok.index.max() if not ok.empty else s.index.max()
