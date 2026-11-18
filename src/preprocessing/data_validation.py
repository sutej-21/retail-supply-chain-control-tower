"""Data profiling and data-quality validation for the Olist source files.

Each check returns a row with: table, check, column, status (PASS/WARN/FAIL),
severity (INFO/WARNING/CRITICAL), failing_count, total_count, details.

* CRITICAL failures (e.g. duplicated primary keys in ``orders``) stop the
  pipeline - loading them would corrupt the analytics model.
* WARNINGs are known source-data characteristics (e.g. products without a
  category, coordinates outside Brazil) that are handled and documented by
  ``clean_data.py`` instead of silently dropping records.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from config.settings import get_settings
from src.ingestion.load_data import DATE_COLUMNS, NUMERIC_COLUMNS, to_typed
from src.utils.helpers import utc_now, write_json
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

PRIMARY_KEYS: dict[str, tuple[list[str], str]] = {
    "orders": (["order_id"], "CRITICAL"),
    "order_items": (["order_id", "order_item_id"], "CRITICAL"),
    "customers": (["customer_id"], "CRITICAL"),
    "products": (["product_id"], "CRITICAL"),
    "sellers": (["seller_id"], "CRITICAL"),
    "payments": (["order_id", "payment_sequential"], "CRITICAL"),
    "reviews": (["review_id", "order_id"], "WARNING"),
    "category_translation": (["product_category_name"], "CRITICAL"),
}

# (child, child_cols, parent, parent_cols, severity)
FOREIGN_KEYS = [
    ("order_items", "order_id", "orders", "order_id", "CRITICAL"),
    ("order_items", "product_id", "products", "product_id", "CRITICAL"),
    ("order_items", "seller_id", "sellers", "seller_id", "CRITICAL"),
    ("orders", "customer_id", "customers", "customer_id", "CRITICAL"),
    ("payments", "order_id", "orders", "order_id", "WARNING"),
    ("reviews", "order_id", "orders", "order_id", "WARNING"),
    ("products", "product_category_name", "category_translation", "product_category_name", "WARNING"),
    ("customers", "customer_zip_code_prefix", "geolocation", "geolocation_zip_code_prefix", "WARNING"),
    ("sellers", "seller_zip_code_prefix", "geolocation", "geolocation_zip_code_prefix", "WARNING"),
]

# value rules: (table, column, rule-description, predicate returning INVALID mask, severity)
BRAZIL_LAT = (-34.0, 5.5)
BRAZIL_LNG = (-74.5, -28.5)


class DataQualityError(RuntimeError):
    """Raised when CRITICAL data-quality checks fail."""


@dataclass
class CheckResult:
    table: str
    check: str
    column: str
    status: str
    severity: str
    failing_count: int
    total_count: int
    details: str = ""

    @property
    def failing_pct(self) -> float:
        return round(100.0 * self.failing_count / self.total_count, 4) if self.total_count else 0.0


def _result(table, check, column, failing, total, severity, details="") -> CheckResult:
    status = "PASS" if failing == 0 else ("FAIL" if severity == "CRITICAL" else "WARN")
    return CheckResult(table, check, column, status, severity if failing else "INFO",
                       int(failing), int(total), details)


# --------------------------------------------------------------------------- #
# Profiling
# --------------------------------------------------------------------------- #
def profile_table(key: str, df: pd.DataFrame) -> dict:
    typed = to_typed(key, df)
    columns = []
    for col in df.columns:
        s = typed[col]
        info = {
            "column": col,
            "inferred_dtype": str(s.dtype),
            "null_count": int(df[col].isna().sum()),
            "null_pct": round(100 * df[col].isna().mean(), 3) if len(df) else 0.0,
            "distinct": int(df[col].nunique(dropna=True)),
        }
        if pd.api.types.is_numeric_dtype(s) and s.notna().any():
            info.update(min=float(s.min()), max=float(s.max()), mean=round(float(s.mean()), 4))
        elif pd.api.types.is_datetime64_any_dtype(s) and s.notna().any():
            info.update(min=str(s.min()), max=str(s.max()))
        columns.append(info)
    return {
        "table": key,
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "duplicate_rows": int(df.duplicated().sum()),
        "column_profile": columns,
    }


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_row_counts(frames: dict[str, pd.DataFrame]) -> list[CheckResult]:
    return [_result(k, "non_empty", "*", int(len(df) == 0), 1, "CRITICAL") for k, df in frames.items()]


def check_duplicates(frames) -> list[CheckResult]:
    return [_result(k, "duplicate_rows", "*", df.duplicated().sum(), len(df), "WARNING",
                    "exact duplicate rows (de-duplicated in staging)") for k, df in frames.items()]


def check_primary_keys(frames) -> list[CheckResult]:
    out = []
    for table, (cols, severity) in PRIMARY_KEYS.items():
        df = frames[table]
        null_keys = df[cols].isna().any(axis=1).sum()
        dup_keys = df.duplicated(subset=cols, keep="first").sum()
        out.append(_result(table, "pk_not_null", ",".join(cols), null_keys, len(df), severity))
        out.append(_result(table, "pk_unique", ",".join(cols), dup_keys, len(df), severity))
    return out


def check_foreign_keys(frames) -> list[CheckResult]:
    out = []
    for child, ccol, parent, pcol, severity in FOREIGN_KEYS:
        child_vals = frames[child][ccol].dropna()
        parent_vals = set(frames[parent][pcol].dropna().unique())
        orphans = (~child_vals.isin(parent_vals)).sum()
        out.append(_result(child, "foreign_key", f"{ccol}->{parent}.{pcol}", orphans, len(child_vals), severity,
                           f"{orphans} value(s) without a matching {parent} row"))
    return out


def check_parsing(frames) -> list[CheckResult]:
    """Non-null values that cannot be parsed as number / timestamp."""
    out = []
    for table, cols in {**{k: v for k, v in NUMERIC_COLUMNS.items()}}.items():
        df = frames[table]
        for col in cols:
            bad = (df[col].notna() & pd.to_numeric(df[col], errors="coerce").isna()).sum()
            out.append(_result(table, "numeric_parse", col, bad, df[col].notna().sum(), "CRITICAL"))
    for table, cols in DATE_COLUMNS.items():
        df = frames[table]
        for col in cols:
            bad = (df[col].notna() & pd.to_datetime(df[col], errors="coerce").isna()).sum()
            out.append(_result(table, "timestamp_parse", col, bad, df[col].notna().sum(), "CRITICAL"))
    return out


def check_timestamp_logic(frames) -> list[CheckResult]:
    o = to_typed("orders", frames["orders"])
    n = len(o)
    p = o["order_purchase_timestamp"]
    rules = {
        "approved_before_purchase": o["order_approved_at"] < p,
        "carrier_before_purchase": o["order_delivered_carrier_date"] < p,
        "delivered_before_purchase": o["order_delivered_customer_date"] < p,
        "delivered_before_carrier": o["order_delivered_customer_date"] < o["order_delivered_carrier_date"],
        "delivered_status_without_delivery_date": (o["order_status"] == "delivered")
        & o["order_delivered_customer_date"].isna(),
        "delivery_date_on_non_delivered_status": (o["order_status"] != "delivered")
        & o["order_delivered_customer_date"].notna(),
        "purchase_timestamp_missing": p.isna(),
    }
    out = [_result("orders", "timestamp_logic", rule, mask.sum(), n,
                   "CRITICAL" if rule == "purchase_timestamp_missing" else "WARNING")
           for rule, mask in rules.items()]
    r = to_typed("reviews", frames["reviews"])
    out.append(_result("reviews", "timestamp_logic", "answer_before_creation",
                       (r["review_answer_timestamp"] < r["review_creation_date"]).sum(), len(r), "WARNING"))
    return out


def check_value_ranges(frames) -> list[CheckResult]:
    oi = to_typed("order_items", frames["order_items"])
    pay = to_typed("payments", frames["payments"])
    rev = to_typed("reviews", frames["reviews"])
    prod = to_typed("products", frames["products"])
    geo = to_typed("geolocation", frames["geolocation"])
    out = [
        _result("order_items", "value_range", "price<=0", (oi["price"] <= 0).sum(), len(oi), "WARNING"),
        _result("order_items", "value_range", "freight_value<0", (oi["freight_value"] < 0).sum(), len(oi), "WARNING"),
        _result("payments", "value_range", "payment_value<0", (pay["payment_value"] < 0).sum(), len(pay), "WARNING"),
        _result("payments", "value_range", "payment_installments<1",
                (pay["payment_installments"] < 1).sum(), len(pay), "WARNING"),
        _result("reviews", "value_range", "review_score not in 1..5",
                (~rev["review_score"].between(1, 5)).sum(), len(rev), "CRITICAL"),
        _result("products", "value_range", "physical_dimensions<=0",
                (prod[["product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]] <= 0)
                .any(axis=1).sum(), len(prod), "WARNING"),
        _result("geolocation", "value_range", "lat/lng outside Brazil bounding box",
                (~geo["geolocation_lat"].between(*BRAZIL_LAT) | ~geo["geolocation_lng"].between(*BRAZIL_LNG)).sum(),
                len(geo), "WARNING"),
    ]
    # payments vs items (order-level reconciliation, tolerance 1 BRL)
    items_total = oi.groupby("order_id")[["price", "freight_value"]].sum().sum(axis=1)
    pay_total = pay.groupby("order_id")["payment_value"].sum()
    joined = pd.concat([items_total.rename("items"), pay_total.rename("paid")], axis=1, join="inner")
    mismatch = ((joined["items"] - joined["paid"]).abs() > 1.0).sum()
    out.append(_result("payments", "reconciliation", "sum(payment_value) vs items+freight (>1 BRL)",
                       mismatch, len(joined), "WARNING", "differences can come from vouchers/interest"))
    return out


def check_outliers(frames) -> list[CheckResult]:
    """IQR (Q3 + 3*IQR) extreme-outlier counts; informational, never blocking."""
    targets = [("order_items", "price"), ("order_items", "freight_value"), ("payments", "payment_value"),
               ("products", "product_weight_g")]
    out = []
    for table, col in targets:
        s = to_typed(table, frames[table])[col].dropna()
        if s.empty:
            continue
        q1, q3 = np.percentile(s, [25, 75])
        upper = q3 + 3 * (q3 - q1)
        n_out = int((s > upper).sum())
        res = _result(table, "outlier_iqr3", col, n_out, len(s), "WARNING",
                      f"upper fence={upper:.2f}; retained, flagged for analysis")
        if n_out:
            res.status, res.severity = "WARN", "INFO"
        out.append(res)
    return out


def check_categories(frames) -> list[CheckResult]:
    prod = frames["products"]
    trans = set(frames["category_translation"]["product_category_name"])
    missing_cat = prod["product_category_name"].isna().sum()
    untranslated = (prod["product_category_name"].notna() & ~prod["product_category_name"].isin(trans)).sum()
    return [
        _result("products", "missing_category", "product_category_name", missing_cat, len(prod), "WARNING",
                "mapped to 'unknown'"),
        _result("products", "untranslated_category", "product_category_name", untranslated, len(prod),
                "WARNING", "English name falls back to the Portuguese name"),
    ]


ALL_CHECKS = [check_row_counts, check_duplicates, check_primary_keys, check_foreign_keys, check_parsing,
              check_timestamp_logic, check_value_ranges, check_outliers, check_categories]


def run_data_quality_checks(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    results: list[CheckResult] = []
    for check in ALL_CHECKS:
        results.extend(check(frames))
    df = pd.DataFrame([{**asdict(r), "failing_pct": r.failing_pct} for r in results])
    return df


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def build_report(frames: dict[str, pd.DataFrame], output_dir: Path | None = None,
                 engine: Engine | None = None, run_id: str | None = None,
                 fail_on_critical: bool = True) -> dict:
    """Profile + check all tables, write Markdown/JSON reports, optionally persist to DB."""
    output_dir = Path(output_dir) if output_dir else get_settings().paths.reports_dir / "data_quality"
    output_dir.mkdir(parents=True, exist_ok=True)
    profiles = [profile_table(k, df) for k, df in frames.items()]
    checks = run_data_quality_checks(frames)
    summary = checks["status"].value_counts().to_dict()
    report = {"generated_at_utc": utc_now(), "summary": summary, "profiles": profiles,
              "checks": checks.to_dict(orient="records")}
    write_json(report, output_dir / "data_quality_report.json")
    (output_dir / "data_quality_report.md").write_text(_to_markdown(profiles, checks, summary), encoding="utf-8")
    logger.info("Data-quality report: %s", summary)

    if engine is not None:
        from config.database import copy_dataframe
        persisted = checks.assign(run_id=run_id or "manual", checked_at=utc_now())
        copy_dataframe(persisted[["run_id", "table", "check", "column", "status", "severity", "failing_count",
                                  "total_count", "failing_pct", "details", "checked_at"]]
                       .rename(columns={"table": "table_name", "check": "check_name", "column": "column_name"}),
                       "analytics.data_quality_log", engine=engine)

    critical = checks[(checks["status"] == "FAIL")]
    if fail_on_critical and not critical.empty:
        raise DataQualityError(
            "Critical data-quality failures:\n" + critical[["table", "check", "column", "failing_count"]].to_string()
        )
    return report


def _to_markdown(profiles, checks: pd.DataFrame, summary: dict) -> str:
    lines = ["# Data Quality Report", "", f"Generated (UTC): {utc_now()}", "",
             "## Summary", "", ", ".join(f"**{k}**: {v}" for k, v in summary.items()), "",
             "## Table profiles", "", "| Table | Rows | Columns | Duplicate rows | Columns with nulls |",
             "|---|---:|---:|---:|---|"]
    for p in profiles:
        nulls = ", ".join(f"{c['column']} ({c['null_pct']}%)" for c in p["column_profile"] if c["null_count"])
        lines.append(f"| {p['table']} | {p['rows']:,} | {p['columns']} | {p['duplicate_rows']:,} | {nulls or '-'} |")
    lines += ["", "## Checks (non-passing first)", "",
              "| Status | Severity | Table | Check | Column / rule | Failing | Total | % | Details |",
              "|---|---|---|---|---|---:|---:|---:|---|"]
    order = {"FAIL": 0, "WARN": 1, "PASS": 2}
    for r in checks.sort_values("status", key=lambda s: s.map(order)).itertuples():
        lines.append(f"| {r.status} | {r.severity} | {r.table} | {r.check} | {r.column} | {r.failing_count:,} | "
                     f"{r.total_count:,} | {r.failing_pct} | {r.details} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    from src.ingestion.load_data import load_source_csvs
    rep = build_report(load_source_csvs(as_text=True), fail_on_critical=False)
    print(rep["summary"])
