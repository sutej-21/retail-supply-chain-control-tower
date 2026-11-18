"""Demand forecasting with Prophet (overall, category and product level).

Method
------
1. Aggregate order-item quantities (units) by purchase date for each entity
   (revenue orders only). Missing dates are filled with 0 (no sale that day).
2. Restrict to ``FORECAST_TRAIN_START`` .. *effective data end* - the Olist
   extract ends with weeks of near-empty days, which are excluded
   (``src.utils.helpers.effective_data_end``) instead of being learnt as a collapse.
3. Data-quality gate per series: minimum history, minimum share of non-zero days,
   no negative values.
4. Seasonality: weekly only if a Kruskal-Wallis test shows day-of-week
   differences (p < 0.05); yearly only with >= 2 years of history (Olist has ~20
   months, so yearly seasonality is OFF by default). Brazilian holidays optional.
5. Accuracy: chronological hold-out (last ``FORECAST_HOLDOUT_DAYS``). The
   hold-out model is trained ONLY on data before the hold-out start (no leakage).
   MAE, RMSE, MAPE (non-zero days), WAPE and a seasonal-naive (t-7) MAE benchmark
   are stored in ``analytics.forecast_accuracy``.
6. Final model refit on all data up to the forecast origin; 60-day forecast with
   prediction intervals saved to ``analytics.fact_demand_forecast`` (7/30/60-day
   horizons are subsets of it).

Limitation: the forecast origin is the end of the historical data (2018), not
today's date - the Olist data stops there; we never extrapolate beyond 60 days.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import kruskal
from sqlalchemy.engine import Engine

from config.database import get_engine, read_sql, upsert_dataframe
from config.settings import ForecastSettings, get_settings
from src.utils.helpers import effective_data_end, utc_now
from src.utils.logging_config import get_logger

logger = get_logger(__name__)
for _name in ("cmdstanpy", "prophet", "prophet.plot"):
    logging.getLogger(_name).setLevel(logging.ERROR)

MAX_HORIZON = 60


class InsufficientDataError(ValueError):
    """Raised when a series does not pass the forecasting data-quality gate."""


@dataclass
class SeriesQuality:
    n_days: int
    nonzero_share: float
    total_units: float
    start: pd.Timestamp
    end: pd.Timestamp
    has_negative: bool
    sufficient: bool
    reason: str = ""


# --------------------------------------------------------------------------- #
# Series preparation
# --------------------------------------------------------------------------- #
def to_daily_series(df: pd.DataFrame, date_col: str = "ds", value_col: str = "y",
                    start: str | None = None, end=None) -> pd.DataFrame:
    """Aggregate to one row per day and fill missing dates with 0."""
    s = df.groupby(pd.to_datetime(df[date_col]).dt.normalize())[value_col].sum()
    lo = pd.Timestamp(start) if start else s.index.min()
    hi = pd.Timestamp(end) if end is not None else s.index.max()
    s = s.reindex(pd.date_range(lo, hi, freq="D"), fill_value=0).astype(float)
    return pd.DataFrame({"ds": s.index, "y": s.values})


def check_series_quality(series: pd.DataFrame, cfg: ForecastSettings) -> SeriesQuality:
    n = len(series)
    nonzero = float((series["y"] > 0).mean()) if n else 0.0
    neg = bool((series["y"] < 0).any())
    reasons = []
    if n < cfg.min_history_days:
        reasons.append(f"history {n}d < {cfg.min_history_days}d")
    if nonzero < cfg.min_nonzero_share:
        reasons.append(f"non-zero share {nonzero:.2f} < {cfg.min_nonzero_share}")
    if neg:
        reasons.append("negative values")
    return SeriesQuality(n, round(nonzero, 3), float(series["y"].sum()), series["ds"].min(), series["ds"].max(),
                         neg, not reasons, "; ".join(reasons))


def weekly_seasonality_supported(series: pd.DataFrame, alpha: float = 0.05) -> bool:
    if len(series) < 28:
        return False
    groups = [g["y"].values for _, g in series.groupby(series["ds"].dt.dayofweek)]
    if any(len(g) < 3 for g in groups) or all(np.allclose(g, g[0]) for g in groups):
        return False
    try:
        return bool(kruskal(*groups).pvalue < alpha)
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Modelling
# --------------------------------------------------------------------------- #
def _make_model(weekly: bool, yearly: bool, cfg: ForecastSettings):
    from prophet import Prophet  # imported lazily: heavy import

    stan_logger = logging.getLogger("cmdstanpy")   # cmdstanpy configures DEBUG handlers on import
    stan_logger.setLevel(logging.WARNING)
    stan_logger.propagate = False

    model = Prophet(weekly_seasonality=weekly, yearly_seasonality=yearly, daily_seasonality=False,
                    interval_width=cfg.interval_width, changepoint_prior_scale=0.05)
    if cfg.use_br_holidays:
        model.add_country_holidays(country_name="BR")
    return model


def fit_predict(train: pd.DataFrame, horizon: int, cfg: ForecastSettings,
                weekly: bool | None = None, yearly: bool | None = None) -> pd.DataFrame:
    """Fit Prophet on ``train`` (ds, y) and forecast ``horizon`` days after its last date."""
    weekly = weekly_seasonality_supported(train) if weekly is None else weekly
    yearly = (len(train) >= cfg.yearly_min_days) if yearly is None else yearly
    model = _make_model(weekly, yearly, cfg)
    model.fit(train[["ds", "y"]])
    future = model.make_future_dataframe(periods=horizon, freq="D", include_history=False)
    fc = model.predict(future)[["ds", "yhat", "yhat_lower", "yhat_upper"]]
    for c in ("yhat", "yhat_lower", "yhat_upper"):
        fc[c] = fc[c].clip(lower=0)   # demand cannot be negative
    fc.attrs.update(weekly=weekly, yearly=yearly)
    return fc


def accuracy_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    err = actual - predicted
    nz = actual > 0
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "mape": float(np.mean(np.abs(err[nz]) / actual[nz]) * 100) if nz.any() else None,
        "wape": float(np.sum(np.abs(err)) / np.sum(actual) * 100) if actual.sum() > 0 else None,
    }


def evaluate_holdout(series: pd.DataFrame, cfg: ForecastSettings) -> dict:
    """Chronological hold-out evaluation. The model only sees data before the hold-out."""
    h = cfg.holdout_days
    if len(series) <= h + 28:
        raise InsufficientDataError("series too short for a hold-out evaluation")
    train, test = series.iloc[:-h], series.iloc[-h:]
    fc = fit_predict(train, h, cfg)
    merged = test.merge(fc, on="ds", how="left")
    metrics = accuracy_metrics(merged["y"], merged["yhat"])
    naive = series["y"].shift(7).iloc[-h:]  # seasonal naive uses only values >= 7 days old
    metrics["naive_mae"] = float(np.mean(np.abs(test["y"].values - naive.values)))
    metrics.update(holdout_start=test["ds"].min().date(), holdout_end=test["ds"].max().date(),
                   train_days=len(train), weekly_seasonality=fc.attrs["weekly"],
                   yearly_seasonality=fc.attrs["yearly"])
    merged.attrs["metrics"] = metrics
    return {"metrics": metrics, "holdout_frame": merged}


def forecast_entity(series: pd.DataFrame, cfg: ForecastSettings, horizon: int = MAX_HORIZON) -> dict:
    quality = check_series_quality(series, cfg)
    if not quality.sufficient:
        raise InsufficientDataError(quality.reason)
    if horizon > MAX_HORIZON:
        raise ValueError(f"horizon limited to {MAX_HORIZON} days (history supports short-term forecasts only)")
    evaluation = evaluate_holdout(series, cfg)
    fc = fit_predict(series, horizon, cfg)
    fc["horizon_day"] = np.arange(1, len(fc) + 1)
    return {"quality": quality, "evaluation": evaluation, "forecast": fc}


# --------------------------------------------------------------------------- #
# Data access & orchestration
# --------------------------------------------------------------------------- #
def load_item_history(engine: Engine) -> pd.DataFrame:
    return read_sql("""
        SELECT oi.purchase_date AS ds, oi.product_id, p.category_name_en AS category, COUNT(*) AS y
        FROM analytics.fact_order_items oi JOIN analytics.dim_product p USING (product_id)
        WHERE oi.is_revenue_order
        GROUP BY 1, 2, 3
    """, engine=engine).assign(ds=lambda x: pd.to_datetime(x["ds"]))


def select_entities(items: pd.DataFrame, end: pd.Timestamp, cfg: ForecastSettings) -> dict[str, list[str]]:
    recent = items[items["ds"] > end - pd.Timedelta(days=90)]
    cats = recent.groupby("category")["y"].sum().sort_values(ascending=False)
    cats = [c for c in cats.index if c != "unknown"][: cfg.top_n_categories]
    prods = recent.groupby("product_id")["y"].sum().sort_values(ascending=False).index[: cfg.top_n_products * 3]
    return {"category": list(cats), "product": list(prods)}


def _plot(entity_label: str, history: pd.DataFrame, holdout: pd.DataFrame, forecast: pd.DataFrame, path) -> None:
    try:
        import plotly.graph_objects as go
    except ImportError:  # pragma: no cover
        return
    hist = history.iloc[-180:]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist["ds"], y=hist["y"], name="Actual units", line=dict(color="#455A64")))
    fig.add_trace(go.Scatter(x=holdout["ds"], y=holdout["yhat"], name="Hold-out prediction",
                             line=dict(color="#F9A825", dash="dot")))
    fig.add_trace(go.Scatter(x=forecast["ds"], y=forecast["yhat_upper"], line=dict(width=0), showlegend=False))
    fig.add_trace(go.Scatter(x=forecast["ds"], y=forecast["yhat_lower"], fill="tonexty", line=dict(width=0),
                             fillcolor="rgba(46,125,50,0.2)", name="Prediction interval"))
    fig.add_trace(go.Scatter(x=forecast["ds"], y=forecast["yhat"], name="Forecast", line=dict(color="#2E7D32")))
    m = holdout.attrs.get("metrics", {})
    fig.update_layout(title=f"Demand forecast - {entity_label} (hold-out MAE {m.get('mae', float('nan')):.2f}, "
                            f"WAPE {m.get('wape') or float('nan'):.1f}%)",
                      xaxis_title="Date", yaxis_title="Units per day", template="plotly_white")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(path, include_plotlyjs="cdn")


def run_forecasting(engine: Engine | None = None, levels: tuple[str, ...] = ("overall", "category", "product"),
                    make_plots: bool = True) -> dict:
    engine = engine or get_engine()
    settings = get_settings()
    cfg = settings.forecast
    run_id = f"fc-{utc_now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    items = load_item_history(engine)
    if items.empty:
        raise InsufficientDataError("No sales history available for forecasting")
    overall_daily = to_daily_series(items, start=cfg.train_start)
    end = effective_data_end(overall_daily.set_index("ds")["y"])
    logger.info("Forecast origin (effective data end): %s", end.date())
    entities = select_entities(items, end, cfg)

    jobs: list[tuple[str, str, pd.DataFrame]] = []
    if "overall" in levels:
        jobs.append(("overall", "ALL", to_daily_series(items, start=cfg.train_start, end=end)))
    if "category" in levels:
        for c in entities["category"]:
            jobs.append(("category", c, to_daily_series(items[items["category"] == c], start=cfg.train_start, end=end)))
    if "product" in levels:
        n_ok = 0
        for p in entities["product"]:
            if n_ok >= cfg.top_n_products:
                break
            sub = items[items["product_id"] == p]
            series = to_daily_series(sub, start=max(pd.Timestamp(cfg.train_start), sub["ds"].min()), end=end)
            if check_series_quality(series, cfg).sufficient:
                jobs.append(("product", p, series))
                n_ok += 1

    forecasts, accuracy, skipped = [], [], []
    for level, entity, series in jobs:
        try:
            res = forecast_entity(series, cfg)
        except InsufficientDataError as exc:
            skipped.append({"level": level, "entity": entity, "reason": str(exc)})
            continue
        fc = res["forecast"].rename(columns={"ds": "forecast_date"})
        fc["forecast_date"] = fc["forecast_date"].dt.date
        fc = fc.assign(run_id=run_id, model_level=level, entity_id=entity,
                       entity_name="All products" if level == "overall" else entity,
                       forecast_origin_date=end.date(), model_name="prophet")
        forecasts.append(fc)
        m = res["evaluation"]["metrics"]
        accuracy.append({"model_level": level, "entity_id": entity, "run_id": run_id, **m})
        if make_plots and (level != "product" or len(accuracy) <= 3):
            safe = "".join(ch if ch.isalnum() else "_" for ch in entity)[:40]
            _plot(f"{level}: {entity}", series, res["evaluation"]["holdout_frame"], res["forecast"],
                  settings.paths.reports_dir / "forecasts" / f"forecast_{level}_{safe}.html")
        logger.info("Forecast %-8s %-34s MAE=%.3f RMSE=%.3f WAPE=%s weekly=%s", level, entity[:34], m["mae"],
                    m["rmse"], None if m["wape"] is None else round(m["wape"], 1), m["weekly_seasonality"])

    if forecasts:
        out = pd.concat(forecasts, ignore_index=True)
        out[["yhat", "yhat_lower", "yhat_upper"]] = out[["yhat", "yhat_lower", "yhat_upper"]].round(3)
        upsert_dataframe(out[["run_id", "model_level", "entity_id", "entity_name", "forecast_origin_date",
                              "forecast_date", "horizon_day", "yhat", "yhat_lower", "yhat_upper", "model_name"]],
                         "analytics.fact_demand_forecast",
                         ["model_level", "entity_id", "forecast_origin_date", "forecast_date"], engine=engine)
        acc = pd.DataFrame(accuracy)
        for c in ("mae", "rmse", "mape", "wape", "naive_mae"):
            acc[c] = pd.to_numeric(acc[c]).round(4)
        upsert_dataframe(acc[["model_level", "entity_id", "holdout_start", "holdout_end", "run_id", "train_days", "mae",
                              "rmse", "mape", "wape", "naive_mae", "weekly_seasonality", "yearly_seasonality"]],
                         "analytics.forecast_accuracy", ["model_level", "entity_id", "holdout_end"], engine=engine)
    summary = {"run_id": run_id, "forecast_origin": str(end.date()), "models": len(forecasts),
               "skipped": skipped, "accuracy": accuracy}
    logger.info("Forecasting finished: %d models, %d skipped", len(forecasts), len(skipped))
    return summary


if __name__ == "__main__":
    s = run_forecasting()
    print(pd.DataFrame(s["accuracy"])[["model_level", "entity_id", "mae", "rmse", "mape", "wape", "naive_mae"]])
