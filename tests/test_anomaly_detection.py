"""Anomaly detection: Z-score, Isolation Forest and the planted test anomalies."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.settings import AnomalySettings
from src.ml import anomaly_detection as ad

CFG = AnomalySettings()


def test_rolling_zscore_uses_only_past_values():
    s = pd.Series([10.0] * 20 + [100.0] + [10.0] * 5)
    z, mean = ad.rolling_zscore(s, window=10, min_periods=5)
    assert mean.iloc[20] == 10.0          # the spike is not part of its own baseline
    assert np.isnan(z.iloc[20])           # zero-variance baseline -> undefined z, never flagged
    assert mean.iloc[21] == pytest.approx(19.0)   # afterwards the spike enters the PAST window


def test_rolling_zscore_detects_spike_with_noise():
    rng = np.random.default_rng(0)
    s = pd.Series(rng.normal(50, 5, 60))
    s.iloc[45] = 120
    z, _ = ad.rolling_zscore(s, 28, 14)
    assert z.idxmax() == 45 and z.iloc[45] > CFG.zscore_threshold


def test_severity_mapping():
    assert ad.severity_from_z([2.0, 3.2, 4.5, 6.0], CFG).tolist() == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def test_isolation_forest_flags_outlier():
    rng = np.random.default_rng(1)
    x = pd.DataFrame({"a": rng.normal(0, 1, 500), "b": rng.normal(0, 1, 500)})
    x.loc[0] = [12, 12]
    score, flag = ad.isolation_forest_scores(x, contamination=0.01)
    assert flag[0] and score[0] == score.max()


def test_small_input_is_not_flagged():
    score, flag = ad.isolation_forest_scores(pd.DataFrame({"a": [1, 2, 3]}))
    assert not flag.any()


def test_output_schema():
    daily = pd.DataFrame({"date": pd.date_range("2018-01-01", periods=60), "category": "x",
                          "units": [9, 11] * 25 + [80] + [10] * 9})
    out = ad.detect_demand_spikes(daily, CFG)
    assert list(out.columns) == ad.OUTPUT_COLUMNS
    assert len(out) == 1 and out["record_id"].iloc[0] == "x|2018-02-20"
    assert out["anomaly_id"].is_unique and out["recommended_action"].str.len().gt(0).all()


def test_min_volume_avoids_false_positives():
    daily = pd.DataFrame({"date": pd.date_range("2018-01-01", periods=60), "category": "tiny",
                          "units": [0] * 50 + [3] + [0] * 9})
    assert ad.detect_demand_spikes(daily, CFG).empty


def test_supplier_and_depletion_detectors():
    pos = pd.DataFrame({"po_id": ["P1", "P2"], "supplier_id": ["S", "S"], "product_id": ["a", "b"],
                        "status": ["RECEIVED", "RECEIVED"], "simulated_lead_time_days": [5.5, 19.0],
                        "average_lead_time_days": [5.0, 5.0], "lead_time_std_days": [1.0, 1.0],
                        "actual_arrival_ts": pd.to_datetime(["2026-01-01", "2026-01-02"])})
    sl = ad.detect_supplier_lead_time_anomalies(pos, CFG)
    assert sl["record_id"].tolist() == ["P2"] and sl["data_source"].iloc[0] == "SIMULATED"
    cons = pd.DataFrame({"product_id": ["a", "b"], "warehouse_id": ["W", "W"], "units_24h": [12, 2],
                         "expected_daily": [2.0, 2.0], "window_end": pd.Timestamp("2026-01-01 10:00"),
                         "net_available": [5, 50]})
    dep = ad.detect_rapid_depletion(cons, CFG)
    assert dep["record_id"].tolist() == ["a|W|2026-01-01T10"]


# ----------------------------- integration ---------------------------------
@pytest.mark.integration
def test_planted_anomalies_are_detected(simulated_db, sample_frames):
    from src.ingestion.sample_data import PLANTED_DROP_DATE, PLANTED_SPIKE_DATE, PLANTED_UNUSUAL_ORDER_ITEMS
    res = ad.run_anomaly_detection(simulated_db)
    spikes = res[res["anomaly_type"] == "DEMAND_SPIKE"]
    assert f"computers_accessories|{PLANTED_SPIKE_DATE}" in set(spikes["record_id"])
    drops = res[res["anomaly_type"] == "SALES_DROP"]
    assert f"ALL|{PLANTED_DROP_DATE}" in set(drops["record_id"])
    unusual = res[res["anomaly_type"] == "UNUSUAL_ORDER_QUANTITY"]
    assert (unusual["metric_value"] == PLANTED_UNUSUAL_ORDER_ITEMS).any()
    delay_seller = sample_frames["sellers"]["seller_id"].iloc[0]
    seller_hits = res[res["anomaly_type"] == "DELIVERY_DELAY_SELLER"]["record_id"].str.split("|").str[0]
    assert delay_seller in set(seller_hits)
    # idempotent: anomaly ids are deterministic
    again = ad.run_anomaly_detection(simulated_db)
    assert set(again["anomaly_id"]) == set(res["anomaly_id"])
