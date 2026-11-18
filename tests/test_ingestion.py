"""CSV ingestion, dataset acquisition helpers and raw-layer loading."""
from __future__ import annotations

import stat
import zipfile

import pandas as pd
import pytest

from config.settings import OLIST_FILES
from src.ingestion import kaggle_download as kd
from src.ingestion.load_data import SOURCE_COLUMNS, SchemaMismatchError, check_columns, to_typed
from src.utils.helpers import MissingDataError


def test_sample_dataset_has_all_files_and_columns(sample_frames, tmp_root):
    for key, name in OLIST_FILES.items():
        path = tmp_root / "sample" / name
        assert path.exists(), name
        header = pd.read_csv(path, nrows=0).columns.tolist()
        assert header == SOURCE_COLUMNS[key]


def test_verify_source_files_ok(sample_frames):
    paths = kd.verify_source_files()
    assert set(paths) == set(OLIST_FILES)


def test_missing_files_fail_clearly(tmp_path):
    (tmp_path / OLIST_FILES["orders"]).write_text("order_id\n1\n")
    with pytest.raises(MissingDataError) as exc:
        kd.verify_source_files(tmp_path)
    msg = str(exc.value)
    assert "8 required Olist file(s) missing" in msg
    assert "kaggle_download" in msg and "DATA_SOURCE=sample" in msg


def test_empty_file_counts_as_missing(tmp_path, sample_frames, tmp_root):
    for name in OLIST_FILES.values():
        (tmp_path / name).write_bytes((tmp_root / "sample" / name).read_bytes())
    (tmp_path / OLIST_FILES["sellers"]).write_text("")
    assert kd.missing_source_files(tmp_path) == [OLIST_FILES["sellers"]]


def test_check_columns_detects_schema_mismatch():
    with pytest.raises(SchemaMismatchError):
        check_columns("orders", pd.DataFrame(columns=["order_id", "customer_id"]))


def test_load_source_csvs_text_and_typed(raw_frames):
    assert all(df.dtypes.eq(object).all() for df in raw_frames.values())
    typed = to_typed("orders", raw_frames["orders"])
    assert pd.api.types.is_datetime64_any_dtype(typed["order_purchase_timestamp"])
    items = to_typed("order_items", raw_frames["order_items"])
    assert pd.api.types.is_float_dtype(items["price"])


def test_invalid_values_are_coerced_not_dropped():
    df = pd.DataFrame({"order_id": ["a", "b"], "order_item_id": ["1", "x"], "product_id": ["p", "p"],
                       "seller_id": ["s", "s"], "shipping_limit_date": ["2018-01-01 00:00:00", "not a date"],
                       "price": ["10.5", "abc"], "freight_value": ["1", "2"]})
    typed = to_typed("order_items", df)
    assert len(typed) == 2
    assert typed["price"].isna().sum() == 1 and typed["shipping_limit_date"].isna().sum() == 1


def test_manual_zip_extraction_protects_originals(sample_frames, tmp_root, tmp_path):
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in OLIST_FILES.values():
            zf.write(tmp_root / "sample" / name, arcname=f"nested/{name}")
    dest = tmp_path / "raw"
    kd.extract_manual_download(archive, dest)
    assert not kd.missing_source_files(dest)
    mode = (dest / OLIST_FILES["orders"]).stat().st_mode
    assert not mode & stat.S_IWUSR, "original files must be read-only"
    assert (dest / "_manifest.json").exists()


def test_manual_zip_missing_archive(tmp_path):
    with pytest.raises(MissingDataError):
        kd.extract_manual_download(tmp_path / "nope.zip", tmp_path / "raw")


def test_resolve_source_dir_rejects_unknown(monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "ftp")
    with pytest.raises(ValueError):
        kd.resolve_source_dir()


# ----------------------------- integration ---------------------------------
@pytest.mark.integration
def test_database_connectivity(pg_engine):
    from config.database import check_connection, read_sql
    assert check_connection(pg_engine)
    assert read_sql("SELECT 1 AS x", engine=pg_engine).iloc[0, 0] == 1


@pytest.mark.integration
def test_raw_load_is_idempotent(built_db, raw_frames):
    from config.database import read_sql
    from src.ingestion.load_data import RAW_TABLES, load_raw_tables
    before = {k: int(read_sql(f"SELECT COUNT(*) FROM {t}", engine=built_db).iloc[0, 0]) for k, t in RAW_TABLES.items()}
    assert before == {k: len(df) for k, df in raw_frames.items()}
    second = load_raw_tables(engine=built_db)
    assert all(r["skipped"] for r in second.values()), "unchanged files must be skipped"
    load_raw_tables(engine=built_db, force=True)   # forced reload replaces, never appends
    after = {k: int(read_sql(f"SELECT COUNT(*) FROM {t}", engine=built_db).iloc[0, 0]) for k, t in RAW_TABLES.items()}
    assert after == before
