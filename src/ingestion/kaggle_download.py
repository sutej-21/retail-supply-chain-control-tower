"""Acquire the Olist Brazilian E-Commerce dataset from Kaggle.

Two supported methods:

1. **Kaggle API** (``--method api``): needs ``KAGGLE_USERNAME``/``KAGGLE_KEY``
   environment variables or ``~/.kaggle/kaggle.json``.
2. **Manual download** (``--method manual --zip <path>``): download
   ``archive.zip`` from https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
   and point the script at it (or extract the CSVs into ``data/raw`` yourself
   and run ``--method verify``).

Original files are never modified: after acquisition they are made read-only
and a SHA-256 manifest is written to ``data/raw/_manifest.json``.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from config.settings import KAGGLE_DATASET, OLIST_FILES, get_settings
from src.utils.helpers import MissingDataError, file_sha256, utc_now, write_json
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

DATASET_URL = "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce"


def resolve_source_dir() -> Path:
    """Directory the pipeline reads CSVs from.

    ``DATA_SOURCE=kaggle`` (default) -> ``data/raw`` (real Olist files)
    ``DATA_SOURCE=sample``           -> ``data/sample`` (synthetic, for demos/tests)
    """
    settings = get_settings()
    source = os.getenv("DATA_SOURCE", "kaggle").strip().lower()
    if source == "sample":
        return settings.paths.sample_dir
    if source != "kaggle":
        raise ValueError(f"DATA_SOURCE must be 'kaggle' or 'sample', got {source!r}")
    return settings.paths.raw_dir


def missing_source_files(directory: Path) -> list[str]:
    return [
        name for name in OLIST_FILES.values()
        if not (directory / name).exists() or (directory / name).stat().st_size == 0
    ]


def verify_source_files(directory: Path | None = None) -> dict[str, Path]:
    """Return ``{key: path}`` for all nine files or raise a clear ``MissingDataError``."""
    directory = Path(directory) if directory else resolve_source_dir()
    missing = missing_source_files(directory)
    if missing:
        raise MissingDataError(
            f"{len(missing)} required Olist file(s) missing or empty in {directory}: "
            f"{', '.join(missing)}.\n"
            f"Download the dataset with `python -m src.ingestion.kaggle_download --method api` "
            f"(requires Kaggle credentials) or manually from {DATASET_URL} and run "
            f"`python -m src.ingestion.kaggle_download --method manual --zip <archive.zip>`.\n"
            f"For an offline demo set DATA_SOURCE=sample and run `python -m src.ingestion.sample_data`."
        )
    return {key: directory / name for key, name in OLIST_FILES.items()}


def _protect_and_manifest(directory: Path, origin: str) -> Path:
    files = {}
    for name in OLIST_FILES.values():
        path = directory / name
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # read-only originals
        files[name] = {"sha256": file_sha256(path), "bytes": path.stat().st_size}
    manifest = {
        "dataset": KAGGLE_DATASET,
        "source_url": DATASET_URL,
        "license": "CC BY-NC-SA 4.0",
        "acquired_at_utc": utc_now(),
        "method": origin,
        "files": files,
    }
    return write_json(manifest, directory / "_manifest.json")


def _copy_csvs(src_dir: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    found = {p.name: p for p in src_dir.rglob("*.csv")}
    for name in OLIST_FILES.values():
        if name not in found:
            raise MissingDataError(f"{name} not found in the downloaded archive")
        target = dest / name
        if target.exists():
            target.chmod(stat.S_IWUSR | stat.S_IRUSR)
        shutil.copy2(found[name], target)


def download_with_kaggle_api(dest: Path | None = None, force: bool = False) -> Path:
    dest = Path(dest) if dest else get_settings().paths.raw_dir
    if not force and not missing_source_files(dest):
        logger.info("All source files already present in %s - skipping download", dest)
        return dest
    try:
        # NOTE: importing the kaggle package authenticates immediately and raises without credentials
        from kaggle.api.kaggle_api_extended import KaggleApi
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("The 'kaggle' package is not installed: pip install kaggle") from exc
    except Exception as exc:  # pragma: no cover - missing/invalid credentials
        raise RuntimeError(
            "Kaggle authentication failed. Set KAGGLE_USERNAME and KAGGLE_KEY in .env "
            "or place kaggle.json in ~/.kaggle/ (chmod 600)."
        ) from exc
    api = KaggleApi()
    try:
        api.authenticate()
    except Exception as exc:  # pragma: no cover - needs credentials
        raise RuntimeError(
            "Kaggle authentication failed. Set KAGGLE_USERNAME and KAGGLE_KEY in .env "
            "or place kaggle.json in ~/.kaggle/ (chmod 600)."
        ) from exc
    with tempfile.TemporaryDirectory() as tmp:
        logger.info("Downloading %s via Kaggle API ...", KAGGLE_DATASET)
        api.dataset_download_files(KAGGLE_DATASET, path=tmp, unzip=True, quiet=False)
        _copy_csvs(Path(tmp), dest)
    _protect_and_manifest(dest, "kaggle_api")
    logger.info("Dataset stored in %s", dest)
    return dest


def extract_manual_download(zip_path: Path | str, dest: Path | None = None) -> Path:
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise MissingDataError(f"Archive not found: {zip_path}. Download it from {DATASET_URL}")
    dest = Path(dest) if dest else get_settings().paths.raw_dir
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():  # guard against path traversal
                if member.endswith(".csv") and ".." not in Path(member).parts:
                    zf.extract(member, tmp)
        _copy_csvs(Path(tmp), dest)
    _protect_and_manifest(dest, "manual_zip")
    logger.info("Extracted %s into %s", zip_path.name, dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Acquire the Olist dataset")
    parser.add_argument("--method", choices=["api", "manual", "verify"], default="api")
    parser.add_argument("--zip", help="Path to manually downloaded archive.zip")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.method == "api":
        download_with_kaggle_api(force=args.force)
    elif args.method == "manual":
        if not args.zip:
            parser.error("--zip is required for --method manual")
        extract_manual_download(args.zip)
    paths = verify_source_files(get_settings().paths.raw_dir if args.method != "verify" else None)
    for key, path in paths.items():
        print(f"OK  {key:22s} {path}")


if __name__ == "__main__":
    main()
