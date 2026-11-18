"""Project-wide logging configuration (console + rotating file)."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from config.settings import get_settings

_CONFIGURED = False
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(level: str | None = None, log_file: str = "control_tower.log") -> logging.Logger:
    """Configure root logging once; safe to call repeatedly (e.g. from Airflow tasks)."""
    global _CONFIGURED
    settings = get_settings()
    root = logging.getLogger()
    root.setLevel((level or settings.log_level).upper())
    if not _CONFIGURED:
        formatter = logging.Formatter(LOG_FORMAT)
        if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
            console = logging.StreamHandler()
            console.setFormatter(formatter)
            root.addHandler(console)
        try:
            log_dir: Path = settings.paths.logs_dir
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:  # read-only file systems: console logging only
            root.warning("File logging disabled: cannot write to %s", settings.paths.logs_dir)
        for noisy in ("cmdstanpy", "prophet", "matplotlib", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        _CONFIGURED = True
    return root


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
