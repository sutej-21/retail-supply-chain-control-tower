"""Inventory optimisation, scenario analysis and the simulation engine."""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from config.settings import InventorySettings
from src.optimization import inventory_optimization as io
from src.optimization.scenario_analysis import ScenarioParams, compare, project_inventory, summarize
from src.simulation import supplier_simulator as sup


def test_safety_stock_and_reorder_point():
    z = io.z_value(0.95)
    assert z == pytest.approx(1.6449, abs=1e-4)
    ss = io.safety_stock(z, daily_demand=10, demand_std=3, lead_time=4, lead_time_std=1)
    assert float(ss) == pytest.approx(z * np.sqrt(4 * 9 + 100 * 1))
    assert float(io.reorder_point(10, 4, 20)) == 60.0
    assert float(io.safety_stock(z, 10, 0, 4, 0)) == 0.0


def test_invalid_service_level():
    with pytest.raises(ValueError):
        io.z_value(1.2)


def test_reorder_quantity():
    # order-up-to = 10*(4+7)+20 = 130; position = 50-10+20 = 60 -> 70
    q = io.reorder_quantity(10, 4, 7, 20, available=50, reserved=10, on_order=20)
    assert int(q) == 70
    assert int(io.reorder_quantity(10, 4, 7, 20, available=500, reserved=0, on_order=0)) == 0
    assert int(io.reorder_quantity(10, 4, 7, 20, 50, 10, 20, maximum_stock=100)) == 40   # capped
    assert int(io.reorder_quantity(0.1, 4, 7, 0, 1, 0, 0, min_order_qty=5)) == 5


def test_days_of_cover_and_risk_classes():
    cover = io.days_of_cover([5, 30, 45, 200, 10], [5, 5, 5, 5, 0])
    assert cover[0] == 1.0 and np.isnan(cover[4])
    risk = io.classify_risk(net_available=[5, 30, 45, 200, 10], safety_stock_units=[10, 10, 10, 10, 0],
                            reorder_point_units=[40, 40, 40, 40, 0], cover_days=cover,
                            lead_time=[7, 7, 7, 7, 7], daily_demand=[5, 5, 5, 5, 0])
    assert risk.tolist() == ["Critical", "High Risk", "Warning", "Healthy", "Healthy"]


def test_build_policy_and_actions():
    df = pd.DataFrame({"product_id": ["a", "b"], "supplier_id": ["S1", "S2"], "avg_daily_demand": [4.0, 1.0],
                       "demand_std": [2.0, 0.5], "planning_daily_demand": [4.0, 1.0], "lead_time_days": [7.0, 5.0],
                       "lead_time_std_days": [1.0, 1.0], "available_stock": [3, 200], "reserved_stock": [0, 0],
                       "on_order_qty": [0, 0]})
    p = io.build_policy(df, InventorySettings())
    assert p.loc[0, "risk_status"] == "Critical" and p.loc[1, "risk_status"] == "Healthy"
    assert p.loc[0, "reorder_qty"] > 0 and "Initiate replenishment immediately" in p.loc[0, "recommended_action"]
    assert p.loc[1, "recommended_action"] == "No action required."
    assert (p["reorder_point"] >= p["safety_stock"]).all()


def _scenario_inputs():
    return pd.DataFrame({
        "product_id": ["p1", "p2", "p3"], "warehouse_id": ["WH-SP"] * 3, "supplier_id": ["S1", "S1", "S2"],
        "category_name_en": ["x", "x", "y"], "net_available": [40, 15, 100], "planning_daily_demand": [4.0, 2.0, 1.0],
        "lead_time_days": [5.0, 5.0, 5.0], "safety_stock": [8, 4, 2], "reorder_point": [28, 14, 7],
        "reorder_qty": [60, 30, 20], "unit_price_proxy": [100.0, 50.0, 10.0],
        "open_po_qty": [0, 30, 0], "open_po_arrival_day": [None, 3, None]})


def test_zero_delay_equals_baseline():
    inputs = _scenario_inputs()
    p = ScenarioParams(supplier_delay_days=0)
    b, _ = project_inventory(inputs, p.baseline(), date(2026, 1, 1))
    m, _ = project_inventory(inputs, p, date(2026, 1, 1))
    pd.testing.assert_frame_equal(b, m)


def test_seven_day_supplier_delay_increases_risk():
    inputs = _scenario_inputs()
    params = ScenarioParams("Supplier S1 delayed 7 days", supplier_id="S1", supplier_delay_days=7, horizon_days=30)
    base, base_daily = project_inventory(inputs, params.baseline(), date(2026, 1, 1))
    mod, mod_daily = project_inventory(inputs, params, date(2026, 1, 1))
    mod = compare(base, mod)
    s = summarize(base, mod)
    assert s["modified"]["unfulfilled_units"] > s["baseline"]["unfulfilled_units"]
    assert s["delta"]["revenue_exposure_proxy"] > 0
    affected = set(mod.loc[mod["is_affected"], "product_id"])
    assert affected and affected <= {"p1", "p2"}, "only S1 products can be affected"
    # exposure is unfulfilled units x historical price proxy
    assert np.allclose(mod["revenue_exposure_proxy"], (mod["unfulfilled_units"] * mod["unit_price_proxy"]).round(2), atol=0.02)
    assert len(mod_daily) == 30 and mod_daily["projected_inventory"].min() >= 0
    assert (mod.set_index("product_id").loc["p1", "lead_time_days"]) == 12.0


def test_demand_growth_and_inventory_adjustment():
    inputs = _scenario_inputs()
    p = ScenarioParams(supplier_delay_days=0, demand_growth_pct=50, inventory_adjustment_pct=-50, horizon_days=20)
    b, _ = project_inventory(inputs, p.baseline())
    m, _ = project_inventory(inputs, p)
    assert (m["daily_demand"] == b["daily_demand"] * 1.5).all()
    assert (m["starting_stock"] <= b["starting_stock"]).all()
    assert m["unfulfilled_units"].sum() >= b["unfulfilled_units"].sum()


def test_scenario_missing_columns():
    with pytest.raises(ValueError):
        project_inventory(pd.DataFrame({"product_id": ["x"]}), ScenarioParams())


def test_supplier_generation_is_reproducible():
    a = sup.generate_suppliers(10, seed=5)
    b = sup.generate_suppliers(10, seed=5)
    pd.testing.assert_frame_equal(a, b)
    assert a["supplier_name"].str.contains("Simulated").all()
    rng1, rng2 = np.random.default_rng(1), np.random.default_rng(1)
    lt1 = sup.sample_lead_times(np.array([5.0, 7.0]), np.array([1.0, 1.0]), np.array([0.9, 0.5]), rng1, 0.1)
    lt2 = sup.sample_lead_times(np.array([5.0, 7.0]), np.array([1.0, 1.0]), np.array([0.9, 0.5]), rng2, 0.1)
    assert np.array_equal(lt1[0], lt2[0]) and (lt1[0] >= 1).all()


# ----------------------------- integration ---------------------------------
@pytest.mark.integration
def test_simulation_generates_operational_records(simulated_db):
    from config.database import read_sql
    c = read_sql("""SELECT (SELECT COUNT(*) FROM analytics.fact_inventory) AS snaps,
                           (SELECT COUNT(*) FROM analytics.fact_inventory_movements) AS movs,
                           (SELECT COUNT(*) FROM analytics.fact_supplier_performance) AS pos,
                           (SELECT COUNT(*) FROM analytics.dim_supplier WHERE is_simulated) AS sups,
                           (SELECT COUNT(*) FROM analytics.fact_inventory WHERE NOT is_simulated) AS not_sim,
                           (SELECT COUNT(*) FROM analytics.fact_inventory WHERE available_stock < 0) AS negative""",
                 engine=simulated_db).iloc[0]
    assert c.snaps == 60 * 72 and c.movs > 0 and c.pos > 0 and c.sups == 25
    assert c.not_sim == 0 and c.negative == 0


@pytest.mark.integration
def test_simulation_is_idempotent_and_incremental(simulated_db):
    from config.database import read_sql
    from src.simulation.inventory_simulator import InventorySimulator
    before = read_sql("SELECT COUNT(*) FROM analytics.fact_inventory", engine=simulated_db).iloc[0, 0]
    r = InventorySimulator(simulated_db).run(datetime(2026, 1, 10, 12))
    assert r.hours_simulated == 0
    assert read_sql("SELECT COUNT(*) FROM analytics.fact_inventory", engine=simulated_db).iloc[0, 0] == before
    r = InventorySimulator(simulated_db).run(datetime(2026, 1, 10, 14))
    assert r.hours_simulated == 2
    assert read_sql("SELECT COUNT(*) FROM analytics.fact_inventory", engine=simulated_db).iloc[0, 0] == before + 120


@pytest.mark.integration
def test_replenishment_recommendations_generated(simulated_db):
    from config.database import read_sql
    t = read_sql("SELECT risk_status, reorder_qty, recommended_action, reorder_point, safety_stock "
                 "FROM analytics.inventory_thresholds", engine=simulated_db)
    assert len(t) == 60
    assert set(t["risk_status"]) <= set(io.RISK_LEVELS)
    assert (t["reorder_point"] >= t["safety_stock"]).all()
    assert t["recommended_action"].str.len().gt(0).all()


@pytest.mark.integration
def test_seven_day_scenario_end_to_end(simulated_db):
    from config.database import read_sql
    from src.optimization.scenario_analysis import run_scenario
    res = run_scenario(ScenarioParams(supplier_delay_days=7, demand_growth_pct=20), simulated_db, make_plot=False)
    s = res["summary"]
    assert s["modified"]["unfulfilled_units"] >= s["baseline"]["unfulfilled_units"]
    assert s["delta"]["unfulfilled_units"] > 0, "a 7-day delay with +20% demand must change the outcome"
    stored = read_sql("SELECT variant, COUNT(*) AS n FROM analytics.scenario_results WHERE scenario_id = :s GROUP BY 1",
                      {"s": res["scenario_id"]}, engine=simulated_db).set_index("variant")["n"]
    assert stored["BASELINE"] == stored["MODIFIED"] == 60
