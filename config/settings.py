"""Central configuration for the Retail and Supply Chain Control Tower.

Every tunable value is read from environment variables (optionally loaded from
a ``.env`` file) so that no credentials or environment-specific values are
hard-coded. Defaults are safe for local development.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv is optional at runtime (e.g. inside Airflow containers)
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return default if value in (None, "") else value


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    return str(_env(name, str(default))).strip().lower() in {"1", "true", "yes", "y", "on"}


# --------------------------------------------------------------------------- #
# Source data
# --------------------------------------------------------------------------- #
OLIST_FILES: dict[str, str] = {
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

KAGGLE_DATASET = "olistbr/brazilian-ecommerce"


@dataclass(frozen=True)
class Paths:
    root: Path = field(default_factory=lambda: PROJECT_ROOT)
    raw_dir: Path = field(default_factory=lambda: Path(_env("RAW_DATA_DIR", str(PROJECT_ROOT / "data" / "raw"))))
    processed_dir: Path = field(default_factory=lambda: Path(_env("PROCESSED_DATA_DIR", str(PROJECT_ROOT / "data" / "processed"))))
    simulated_dir: Path = field(default_factory=lambda: Path(_env("SIMULATED_DATA_DIR", str(PROJECT_ROOT / "data" / "simulated"))))
    sample_dir: Path = field(default_factory=lambda: Path(_env("SAMPLE_DATA_DIR", str(PROJECT_ROOT / "data" / "sample"))))
    reports_dir: Path = field(default_factory=lambda: Path(_env("REPORTS_DIR", str(PROJECT_ROOT / "reports"))))
    logs_dir: Path = field(default_factory=lambda: Path(_env("LOGS_DIR", str(PROJECT_ROOT / "logs"))))
    sql_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "sql")


@dataclass(frozen=True)
class SimulationSettings:
    """Synthetic operational data settings (inventory, suppliers, warehouses)."""

    random_seed: int = field(default_factory=lambda: _env_int("SIMULATION_RANDOM_SEED", 42))
    n_products: int = field(default_factory=lambda: _env_int("SIM_N_PRODUCTS", 300))          # top-N SKUs by units sold
    n_suppliers: int = field(default_factory=lambda: _env_int("SIM_N_SUPPLIERS", 25))
    bootstrap_hours: int = field(default_factory=lambda: _env_int("SIM_BOOTSTRAP_HOURS", 336))  # 14 days of history
    demand_lookback_days: int = field(default_factory=lambda: _env_int("SIM_DEMAND_LOOKBACK_DAYS", 90))
    low_stock_share: float = field(default_factory=lambda: _env_float("SIM_LOW_STOCK_SHARE", 0.12))
    lead_time_disruption_prob: float = field(default_factory=lambda: _env_float("SIM_DISRUPTION_PROB", 0.08))


@dataclass(frozen=True)
class InventorySettings:
    service_level: float = field(default_factory=lambda: _env_float("INV_SERVICE_LEVEL", 0.95))
    review_period_days: int = field(default_factory=lambda: _env_int("INV_REVIEW_PERIOD_DAYS", 7))
    warning_buffer: float = field(default_factory=lambda: _env_float("INV_WARNING_BUFFER", 0.20))   # within 20% of ROP
    demand_window_days: int = field(default_factory=lambda: _env_int("INV_DEMAND_WINDOW_DAYS", 90))
    min_order_qty: int = field(default_factory=lambda: _env_int("INV_MIN_ORDER_QTY", 1))


@dataclass(frozen=True)
class AnomalySettings:
    zscore_threshold: float = field(default_factory=lambda: _env_float("ANOMALY_Z_THRESHOLD", 3.0))
    zscore_window: int = field(default_factory=lambda: _env_int("ANOMALY_Z_WINDOW", 28))
    zscore_min_periods: int = field(default_factory=lambda: _env_int("ANOMALY_Z_MIN_PERIODS", 14))
    iforest_contamination: float = field(default_factory=lambda: _env_float("ANOMALY_IFOREST_CONTAMINATION", 0.01))
    severity_high_z: float = field(default_factory=lambda: _env_float("ANOMALY_SEVERITY_HIGH_Z", 4.0))
    severity_critical_z: float = field(default_factory=lambda: _env_float("ANOMALY_SEVERITY_CRITICAL_Z", 5.0))
    min_daily_volume: int = field(default_factory=lambda: _env_int("ANOMALY_MIN_DAILY_VOLUME", 5))
    supplier_lead_time_threshold_days: float = field(default_factory=lambda: _env_float("SUPPLIER_LEAD_TIME_THRESHOLD_DAYS", 14.0))
    depletion_ratio_threshold: float = field(default_factory=lambda: _env_float("DEPLETION_RATIO_THRESHOLD", 2.5))


@dataclass(frozen=True)
class ForecastSettings:
    horizons: tuple[int, ...] = (7, 30, 60)
    holdout_days: int = field(default_factory=lambda: _env_int("FORECAST_HOLDOUT_DAYS", 30))
    train_start: str = field(default_factory=lambda: _env("FORECAST_TRAIN_START", "2017-01-01"))
    top_n_categories: int = field(default_factory=lambda: _env_int("FORECAST_TOP_N_CATEGORIES", 10))
    top_n_products: int = field(default_factory=lambda: _env_int("FORECAST_TOP_N_PRODUCTS", 20))
    min_history_days: int = field(default_factory=lambda: _env_int("FORECAST_MIN_HISTORY_DAYS", 90))
    min_nonzero_share: float = field(default_factory=lambda: _env_float("FORECAST_MIN_NONZERO_SHARE", 0.30))
    yearly_min_days: int = field(default_factory=lambda: _env_int("FORECAST_YEARLY_MIN_DAYS", 730))
    use_br_holidays: bool = field(default_factory=lambda: _env_bool("FORECAST_USE_BR_HOLIDAYS", True))
    refresh_hours: int = field(default_factory=lambda: _env_int("FORECAST_REFRESH_HOURS", 24))
    interval_width: float = field(default_factory=lambda: _env_float("FORECAST_INTERVAL_WIDTH", 0.80))


@dataclass(frozen=True)
class AlertSettings:
    mode: str = field(default_factory=lambda: _env("ALERT_MODE", "local"))  # local | slack | email | all
    slack_webhook_url: str | None = field(default_factory=lambda: _env("SLACK_WEBHOOK_URL"))
    smtp_host: str | None = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _env_int("SMTP_PORT", 587))
    smtp_user: str | None = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str | None = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    smtp_use_tls: bool = field(default_factory=lambda: _env_bool("SMTP_USE_TLS", True))
    email_from: str | None = field(default_factory=lambda: _env("ALERT_EMAIL_FROM"))
    email_to: str | None = field(default_factory=lambda: _env("ALERT_EMAIL_TO"))  # comma separated
    cooldown_hours: int = field(default_factory=lambda: _env_int("ALERT_COOLDOWN_HOURS", 24))
    max_individual_alerts: int = field(default_factory=lambda: _env_int("ALERT_MAX_INDIVIDUAL", 10))
    min_severity_to_notify: str = field(default_factory=lambda: _env("ALERT_MIN_SEVERITY", "HIGH"))


@dataclass(frozen=True)
class Settings:
    paths: Paths = field(default_factory=Paths)
    simulation: SimulationSettings = field(default_factory=SimulationSettings)
    inventory: InventorySettings = field(default_factory=InventorySettings)
    anomaly: AnomalySettings = field(default_factory=AnomalySettings)
    forecast: ForecastSettings = field(default_factory=ForecastSettings)
    alerts: AlertSettings = field(default_factory=AlertSettings)
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))


SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
RISK_COLORS = {"Healthy": "#2E7D32", "Warning": "#F9A825", "High Risk": "#EF6C00", "Critical": "#C62828"}


def get_settings() -> Settings:
    """Return a fresh settings object (re-reads environment variables)."""
    return Settings()
