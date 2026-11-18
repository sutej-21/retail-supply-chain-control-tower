"""Clean and standardise the Olist source data into STAGING-ready frames.

Principles
----------
* Original files are never modified - cleaned copies go to ``data/processed``.
* Records are **flagged rather than deleted**. The only removals are exact
  duplicate rows and geolocation points outside Brazil (both logged).
* Every rule is recorded in a cleaning log (rule, rows affected, reason) which
  is written to ``reports/data_quality/cleaning_log.md``.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.ingestion.load_data import to_typed
from src.preprocessing.data_validation import BRAZIL_LAT, BRAZIL_LNG
from src.utils.helpers import utc_now, write_json
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

STAGING_COLUMNS: dict[str, list[str]] = {
    "orders": ["order_id", "customer_id", "order_status", "order_purchase_ts", "order_approved_ts",
               "order_delivered_carrier_ts", "order_delivered_customer_ts", "order_estimated_delivery_ts",
               "has_timestamp_issue", "timestamp_issue"],
    "order_items": ["order_id", "order_item_id", "product_id", "seller_id", "shipping_limit_ts", "price",
                    "freight_value", "is_price_valid"],
    "customers": ["customer_id", "customer_unique_id", "zip_code_prefix", "city", "state"],
    "products": ["product_id", "category_name_pt", "category_name_en", "is_category_missing", "name_length",
                 "description_length", "photos_qty", "weight_g", "length_cm", "height_cm", "width_cm"],
    "sellers": ["seller_id", "zip_code_prefix", "city", "state"],
    "payments": ["order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value"],
    "reviews": ["review_id", "order_id", "review_score", "review_comment_title", "review_comment_message",
                "review_creation_ts", "review_answer_ts", "has_comment"],
    "geolocation": ["zip_code_prefix", "latitude", "longitude", "city", "state", "n_points"],
    "category_translation": ["category_name_pt", "category_name_en"],
}


@dataclass
class CleaningLog:
    entries: list[dict] = field(default_factory=list)

    def add(self, table: str, rule: str, rows: int, action: str, reason: str) -> None:
        self.entries.append({"table": table, "rule": rule, "rows_affected": int(rows), "action": action,
                             "reason": reason})
        if rows:
            logger.info("[%s] %s: %d rows -> %s", table, rule, rows, action)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.entries)


def normalize_text(series: pd.Series) -> pd.Series:
    """Lower-case, trim, strip accents (Olist city names mix accented/unaccented spellings)."""
    def _norm(v):
        if pd.isna(v):
            return v
        v = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode("ascii")
        return " ".join(v.strip().lower().split())
    return series.map(_norm)


def pad_zip(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.zfill(5)


def _drop_exact_duplicates(df: pd.DataFrame, table: str, log: CleaningLog) -> pd.DataFrame:
    n = int(df.duplicated().sum())
    log.add(table, "exact_duplicate_rows", n, "removed (keep first)",
            "identical rows carry no extra information and would double count")
    return df.drop_duplicates().reset_index(drop=True)


def clean_orders(df: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    o = to_typed("orders", _drop_exact_duplicates(df, "orders", log))
    o["order_status"] = o["order_status"].str.strip().str.lower()
    out = pd.DataFrame({
        "order_id": o["order_id"], "customer_id": o["customer_id"], "order_status": o["order_status"],
        "order_purchase_ts": o["order_purchase_timestamp"], "order_approved_ts": o["order_approved_at"],
        "order_delivered_carrier_ts": o["order_delivered_carrier_date"],
        "order_delivered_customer_ts": o["order_delivered_customer_date"],
        "order_estimated_delivery_ts": o["order_estimated_delivery_date"],
    })
    p = out["order_purchase_ts"]
    issues = {
        "approved_before_purchase": out["order_approved_ts"] < p,
        "delivered_before_purchase": out["order_delivered_customer_ts"] < p,
        "delivered_before_carrier": out["order_delivered_customer_ts"] < out["order_delivered_carrier_ts"],
        "carrier_before_purchase": out["order_delivered_carrier_ts"] < p,
        "delivered_without_date": (out["order_status"] == "delivered") & out["order_delivered_customer_ts"].isna(),
    }
    issue_text = pd.Series("", index=out.index, dtype=object)
    for name, mask in issues.items():
        log.add("orders", name, mask.sum(), "flagged (timestamp_issue)",
                "kept for sales KPIs; material issues excluded from delivery-time KPIs")
        issue_text = issue_text.where(~mask, issue_text + name + ";")
    out["timestamp_issue"] = issue_text.str.rstrip(";").replace("", None)
    # carrier_before_purchase is a minor clock artefact in Olist; only material issues block delivery KPIs
    material = issues["delivered_before_purchase"] | issues["delivered_without_date"] | issues["delivered_before_carrier"]
    out["has_timestamp_issue"] = material
    return out[STAGING_COLUMNS["orders"]]


def clean_order_items(df: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    oi = to_typed("order_items", _drop_exact_duplicates(df, "order_items", log))
    oi["is_price_valid"] = oi["price"] > 0
    log.add("order_items", "price<=0", (~oi["is_price_valid"]).sum(), "flagged (is_price_valid=false)",
            "kept for unit counts; excluded from unit-price averages")
    oi = oi.rename(columns={"shipping_limit_date": "shipping_limit_ts"})
    oi["order_item_id"] = oi["order_item_id"].astype("Int64")
    return oi[STAGING_COLUMNS["order_items"]]


def clean_customers(df: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    c = _drop_exact_duplicates(df, "customers", log)
    out = pd.DataFrame({
        "customer_id": c["customer_id"], "customer_unique_id": c["customer_unique_id"],
        "zip_code_prefix": pad_zip(c["customer_zip_code_prefix"]), "city": normalize_text(c["customer_city"]),
        "state": c["customer_state"].str.strip().str.upper(),
    })
    log.add("customers", "normalize_city_state_zip", len(out), "standardised",
            "lower-case/unaccented city, upper-case state, 5-digit zip prefix")
    return out


def clean_sellers(df: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    s = _drop_exact_duplicates(df, "sellers", log)
    return pd.DataFrame({
        "seller_id": s["seller_id"], "zip_code_prefix": pad_zip(s["seller_zip_code_prefix"]),
        "city": normalize_text(s["seller_city"]), "state": s["seller_state"].str.strip().str.upper(),
    })


def clean_products(df: pd.DataFrame, translation: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    p = to_typed("products", _drop_exact_duplicates(df, "products", log))
    mapping = dict(zip(translation["product_category_name"], translation["product_category_name_english"]))
    missing = p["product_category_name"].isna()
    log.add("products", "missing_category", missing.sum(), "category set to 'unknown'",
            "products are kept so their sales are not lost")
    cat_pt = p["product_category_name"].fillna("unknown").str.strip()
    cat_en = cat_pt.map(mapping)
    untranslated = cat_en.isna() & ~missing
    log.add("products", "untranslated_category", untranslated.sum(), "English name = Portuguese name",
            "translation file lacks some categories")
    cat_en = cat_en.fillna(cat_pt)
    for col in ["product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]:
        bad = p[col] <= 0
        if bad.any():
            log.add("products", f"{col}<=0", bad.sum(), "set to NULL", "physically impossible dimension")
            p.loc[bad, col] = np.nan
    return pd.DataFrame({
        "product_id": p["product_id"], "category_name_pt": cat_pt, "category_name_en": cat_en,
        "is_category_missing": missing, "name_length": p["product_name_lenght"],
        "description_length": p["product_description_lenght"], "photos_qty": p["product_photos_qty"],
        "weight_g": p["product_weight_g"], "length_cm": p["product_length_cm"],
        "height_cm": p["product_height_cm"], "width_cm": p["product_width_cm"],
    })


def clean_payments(df: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    pay = to_typed("payments", _drop_exact_duplicates(df, "payments", log))
    pay["payment_type"] = pay["payment_type"].str.strip().str.lower()
    zero_inst = pay["payment_installments"] < 1
    log.add("payments", "installments<1", zero_inst.sum(), "set to 1", "a payment has at least one installment")
    pay.loc[zero_inst, "payment_installments"] = 1
    return pay[STAGING_COLUMNS["payments"]]


def clean_reviews(df: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    r = to_typed("reviews", _drop_exact_duplicates(df, "reviews", log))
    r = r.sort_values("review_answer_timestamp")
    dup = r.duplicated(subset=["review_id", "order_id"], keep="last")
    log.add("reviews", "duplicate_review_id_order_id", dup.sum(), "kept latest answer",
            "the composite key must be unique in staging")
    r = r[~dup]
    r = r.rename(columns={"review_creation_date": "review_creation_ts", "review_answer_timestamp": "review_answer_ts"})
    r["has_comment"] = r["review_comment_message"].notna() & (r["review_comment_message"].str.strip() != "")
    r["review_score"] = r["review_score"].astype("Int64")
    return r[STAGING_COLUMNS["reviews"]].reset_index(drop=True)


def clean_geolocation(df: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    g = to_typed("geolocation", _drop_exact_duplicates(df, "geolocation", log))
    oob = ~g["geolocation_lat"].between(*BRAZIL_LAT) | ~g["geolocation_lng"].between(*BRAZIL_LNG)
    log.add("geolocation", "outside_brazil_bbox", oob.sum(), "excluded from zip-prefix centroid",
            "coordinates outside Brazil are data-entry errors")
    g = g[~oob].copy()
    g["zip_code_prefix"] = pad_zip(g["geolocation_zip_code_prefix"])
    g["city"] = normalize_text(g["geolocation_city"])
    agg = g.groupby("zip_code_prefix").agg(
        latitude=("geolocation_lat", "median"), longitude=("geolocation_lng", "median"),
        city=("city", lambda s: s.mode().iat[0]), state=("geolocation_state", lambda s: s.mode().iat[0]),
        n_points=("geolocation_lat", "size"),
    ).reset_index()
    log.add("geolocation", "aggregate_to_zip_prefix", len(g) - len(agg), "collapsed to one row per prefix (median)",
            "many points per prefix would fan-out joins to customers/sellers")
    return agg[STAGING_COLUMNS["geolocation"]]


def clean_translation(df: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    t = _drop_exact_duplicates(df, "category_translation", log)
    return pd.DataFrame({"category_name_pt": t["product_category_name"].str.strip(),
                         "category_name_en": t["product_category_name_english"].str.strip()})


def clean_all(frames: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], CleaningLog]:
    """Clean every table. ``frames`` are the raw (string) frames from ``load_source_csvs``."""
    log = CleaningLog()
    cleaned = {
        "orders": clean_orders(frames["orders"], log),
        "order_items": clean_order_items(frames["order_items"], log),
        "customers": clean_customers(frames["customers"], log),
        "sellers": clean_sellers(frames["sellers"], log),
        "products": clean_products(frames["products"], frames["category_translation"], log),
        "payments": clean_payments(frames["payments"], log),
        "reviews": clean_reviews(frames["reviews"], log),
        "geolocation": clean_geolocation(frames["geolocation"], log),
        "category_translation": clean_translation(frames["category_translation"], log),
    }
    return cleaned, log


def write_processed(cleaned: dict[str, pd.DataFrame], log: CleaningLog, output_dir: Path | None = None) -> Path:
    settings = get_settings()
    output_dir = Path(output_dir) if output_dir else settings.paths.processed_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    for key, df in cleaned.items():
        df.to_csv(output_dir / f"{key}.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
    report_dir = settings.paths.reports_dir / "data_quality"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_df = log.to_frame()
    write_json({"generated_at_utc": utc_now(), "entries": log.entries}, report_dir / "cleaning_log.json")
    md = ["# Cleaning Log", "", "| Table | Rule | Rows affected | Action | Reason |", "|---|---|---:|---|---|"]
    md += [f"| {r.table} | {r.rule} | {r.rows_affected:,} | {r.action} | {r.reason} |" for r in log_df.itertuples()]
    (report_dir / "cleaning_log.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return output_dir


def read_processed(input_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    input_dir = Path(input_dir) if input_dir else get_settings().paths.processed_dir
    frames = {}
    for key, cols in STAGING_COLUMNS.items():
        path = input_dir / f"{key}.csv"
        if not path.exists():
            from src.utils.helpers import MissingDataError
            raise MissingDataError(f"Processed file {path} missing - run the clean step first")
        frames[key] = pd.read_csv(path, dtype={"zip_code_prefix": str})[cols]
    return frames


if __name__ == "__main__":
    from src.ingestion.load_data import load_source_csvs
    cleaned_frames, cleaning_log = clean_all(load_source_csvs(as_text=True))
    print(write_processed(cleaned_frames, cleaning_log))
