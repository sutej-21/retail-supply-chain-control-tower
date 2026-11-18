"""Prophet forecasting: preparation, quality gate, accuracy, leakage and persistence."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.settings import get_settings
from src.ml import demand_forecasting as f
from src.utils.helpers import effective_data_end


def _weekly_series(n=200, seed=0, start="2018-01-01"):
    rng = np.random.default_rng(seed)
    ds = pd.date_range(start, periods=n, freq="D")
    y = 20 + np.array([8, 6, 4, 2, 0, -8, -6])[ds.dayofweek] + rng.normal(0, 1.5, n) + np.linspace(0, 5, n)
    return pd.DataFrame({"ds": ds, "y": np.clip(y, 0, None)})


def test_to_daily_series_fills_missing_dates():
    raw = pd.DataFrame({"ds": pd.to_datetime(["2018-01-01", "2018-01-01", "2018-01-04"]), "y": [1, 2, 5]})
    s = f.to_daily_series(raw)
    assert s["y"].tolist() == [3.0, 0.0, 0.0, 5.0]


def test_quality_gate():
    cfg = get_settings().forecast
    short = _weekly_series(30)
    q = f.check_series_quality(short, cfg)
    assert not q.sufficient and "history" in q.reason
    sparse = _weekly_series(200).assign(y=0.0)
    sparse.loc[::20, "y"] = 1
    assert "non-zero share" in f.check_series_quality(sparse, cfg).reason
    assert f.check_series_quality(_weekly_series(200), cfg).sufficient


def test_weekly_seasonality_detection():
    assert f.weekly_seasonality_supported(_weekly_series(120))
    rng = np.random.default_rng(1)
    noise = pd.DataFrame({"ds": pd.date_range("2018-01-01", periods=120), "y": rng.normal(10, 1, 120)})
    assert not f.weekly_seasonality_supported(noise)


def test_accuracy_metrics_values():
    m = f.accuracy_metrics(np.array([10, 0, 5]), np.array([8, 1, 5]))
    assert m["mae"] == pytest.approx(1.0)
    assert m["rmse"] == pytest.approx(np.sqrt(5 / 3))
    assert m["mape"] == pytest.approx(10.0)   # zero actual excluded
    assert m["wape"] == pytest.approx(20.0)


def test_effective_data_end_trims_incomplete_tail():
    s = pd.Series([50.0] * 100 + [1.0, 0.0, 2.0], index=pd.date_range("2018-01-01", periods=103))
    assert effective_data_end(s) == pd.Timestamp("2018-04-10")


@pytest.mark.slow
def test_holdout_has_no_leakage_and_forecast_shape():
    cfg = get_settings().forecast
    series = _weekly_series(200)
    ev = f.evaluate_holdout(series, cfg)
    m = ev["metrics"]
    assert m["train_days"] == len(series) - cfg.holdout_days
    assert pd.Timestamp(m["holdout_start"]) == series["ds"].iloc[-cfg.holdout_days]
    assert m["weekly_seasonality"] is True and m["yearly_seasonality"] is False
    assert m["mae"] < m["naive_mae"] * 1.5   # sanity: competitive with seasonal naive
    res = f.forecast_entity(series, cfg)
    fc = res["forecast"]
    assert len(fc) == 60 and fc["horizon_day"].tolist() == list(range(1, 61))
    assert fc["ds"].min() == series["ds"].max() + pd.Timedelta(days=1)
    assert (fc["yhat_lower"] <= fc["yhat"] + 1e-9).all() and (fc["yhat"] <= fc["yhat_upper"] + 1e-9).all()
    assert (fc[["yhat", "yhat_lower", "yhat_upper"]] >= 0).all().all()


def test_horizon_beyond_support_is_rejected():
    with pytest.raises(ValueError):
        f.forecast_entity(_weekly_series(200), get_settings().forecast, horizon=90)


def test_insufficient_series_raises():
    with pytest.raises(f.InsufficientDataError):
        f.forecast_entity(_weekly_series(40), get_settings().forecast)


# ----------------------------- integration ---------------------------------
@pytest.mark.integration
@pytest.mark.slow
def test_run_forecasting_persists(built_db):
    from config.database import read_sql
    summary = f.run_forecasting(built_db, levels=("overall", "category"), make_plots=False)
    assert summary["models"] >= 2
    fc = read_sql("SELECT model_level, COUNT(*) AS n, MIN(horizon_day) AS h1, MAX(horizon_day) AS h60 "
                  "FROM analytics.fact_demand_forecast GROUP BY 1", engine=built_db).set_index("model_level")
    assert fc.loc["overall", "n"] == 60 and fc.loc["overall", "h60"] == 60
    acc = read_sql("SELECT * FROM analytics.forecast_accuracy WHERE model_level = 'overall'", engine=built_db)
    assert len(acc) == 1 and acc["mae"].iloc[0] >= 0
    f.run_forecasting(built_db, levels=("overall",), make_plots=False)   # idempotent per origin
    n = read_sql("SELECT COUNT(*) FROM analytics.fact_demand_forecast WHERE model_level='overall'", engine=built_db)
    assert n.iloc[0, 0] == 60
