"""KPI formulas (unit) and SQL-vs-Python consistency (integration)."""
from __future__ import annotations

import pandas as pd
import pytest

from src.transformations import kpi_calculations as k


@pytest.fixture()
def orders():
    return pd.DataFrame({
        "order_id": ["o1", "o2", "o3", "o4", "o5"],
        "customer_unique_id": ["A", "A", "B", "C", "C"],
        "order_status": ["delivered", "delivered", "delivered", "canceled", "delivered"],
        "purchase_ts": pd.to_datetime(["2018-01-01", "2018-01-01", "2018-01-03", "2018-01-03", "2018-01-04"]),
        "items_value": [100.0, 50.0, 30.0, 999.0, 20.0],
        "item_count": [2, 1, 1, 5, 1],
        "is_delivered": [True, True, True, False, True],
        "is_late": [False, True, False, None, False],
        "delivery_days": [5.0, 15.0, 7.0, None, 3.0],
        "delay_days": [-3.0, 4.0, -1.0, None, -5.0],
        "delivery_status": ["on_time", "late", "on_time", "not_delivered", "on_time"],
        "review_score": [5, 1, 4, None, 5],
    })


def test_sales_kpis_exclude_canceled(orders):
    s = k.sales_kpis(orders)
    assert s == {"total_revenue": 200.0, "total_orders": 4, "avg_order_value": 50.0, "units_sold": 5}


def test_daily_sales_fills_missing_dates(orders):
    d = k.daily_sales(orders)
    assert len(d) == 4 and d.loc[d["sales_date"] == "2018-01-02", "orders"].iloc[0] == 0
    assert d.loc[d["sales_date"] == "2018-01-01", "revenue"].iloc[0] == 150.0


def test_delivery_kpis(orders):
    d = k.delivery_kpis(orders)
    assert d["delivered_orders"] == 4 and d["late_orders"] == 1
    assert d["on_time_delivery_rate_pct"] == 75.0 and d["late_delivery_rate_pct"] == 25.0
    assert d["avg_delivery_days"] == 7.5 and d["avg_delay_days_late_orders"] == 4.0


def test_repeat_customer_rate_uses_unique_id(orders):
    # A has 2 revenue orders; C's second order is canceled -> 1 of 3 persons repeat
    assert k.repeat_customer_rate(orders) == pytest.approx(33.33, abs=0.01)


def test_review_by_delivery_status(orders):
    r = k.review_by_delivery_status(orders).set_index("delivery_status")
    assert r.loc["late", "avg_review_score"] == 1.0
    assert r.loc["on_time", "avg_review_score"] == pytest.approx(4.667, abs=1e-3)


def test_inventory_kpis():
    assert k.inventory_turnover(300, 150) == 2.0
    assert k.inventory_turnover(10, 0) == 0.0
    assert k.days_of_inventory_on_hand(40, 4) == 10.0
    assert k.days_of_inventory_on_hand(40, 0) is None
    assert k.stockout_risk_pct(pd.Series(["Critical", "High Risk", "Warning", "Healthy"])) == 50.0
    assert k.inventory_value(pd.Series([2, 3]), pd.Series([10.0, 1.5])) == 24.5


def test_supplier_kpis():
    pos = pd.DataFrame({"supplier_id": ["S1", "S1", "S2", "S2"], "status": ["RECEIVED"] * 3 + ["IN_TRANSIT"],
                        "simulated_lead_time_days": [5.0, 9.0, 4.0, 4.0], "delay_days": [0.0, 4.0, -1.0, None]})
    s = k.supplier_kpis(pos).set_index("supplier_id")
    assert s.loc["S1", "delay_rate_pct"] == 50.0 and s.loc["S2", "reliability_score_pct"] == 100.0
    assert s.loc["S2", "rank"] == 1 and s.loc["S1", "avg_lead_time_days"] == 7.0


def test_demand_growth():
    items = pd.DataFrame({"product_id": ["p"] * 6 + ["q"],
                          "purchase_date": pd.to_datetime(["2018-01-30"] * 4 + ["2018-01-10"] * 2 + ["2018-01-29"])})
    g = k.demand_growth(items, "2018-01-31", window_days=15).set_index("product_id")
    assert g.loc["p", "growth_pct"] == 100.0 and pd.isna(g.loc["q", "growth_pct"])


# ----------------------------- integration ---------------------------------
@pytest.mark.integration
def test_sql_views_match_python(built_db):
    from config.database import read_sql
    orders = read_sql("""SELECT order_id, customer_unique_id, order_status, purchase_ts, items_value, item_count,
                                is_delivered, is_late, delivery_days, delay_days, delivery_status, review_score
                         FROM analytics.fact_orders""", engine=built_db)
    for c in ("items_value", "delivery_days", "delay_days"):
        orders[c] = orders[c].astype(float)
    py_sales = k.sales_kpis(orders)
    sql_exec = read_sql("SELECT * FROM analytics.vw_executive_kpis", engine=built_db).iloc[0]
    assert float(sql_exec.total_revenue) == pytest.approx(py_sales["total_revenue"])
    assert int(sql_exec.total_orders) == py_sales["total_orders"]
    assert float(sql_exec.avg_order_value) == pytest.approx(py_sales["avg_order_value"], abs=0.01)
    py_del = k.delivery_kpis(orders)
    sql_del = read_sql("SELECT * FROM analytics.vw_delivery_kpis", engine=built_db).iloc[0]
    assert float(sql_del.on_time_delivery_rate_pct) == pytest.approx(py_del["on_time_delivery_rate_pct"], abs=0.01)
    assert float(sql_del.avg_delivery_days) == pytest.approx(py_del["avg_delivery_days"], abs=0.01)
    sql_cust = read_sql("SELECT * FROM analytics.vw_customer_kpis", engine=built_db).iloc[0]
    assert float(sql_cust.repeat_customer_rate_pct) == pytest.approx(k.repeat_customer_rate(orders), abs=0.01)


@pytest.mark.integration
def test_all_kpi_queries_execute(simulated_db):
    """Every statement in sql/08_kpi_queries.sql runs and returns rows."""
    from config.database import read_sql
    from config.settings import get_settings
    sql = (get_settings().paths.sql_dir / "08_kpi_queries.sql").read_text()
    statements = [s.strip() for s in sql.split(";") if "SELECT" in s.upper()]
    statements = ["\n".join(l for l in s.splitlines() if not l.strip().startswith("--")) for s in statements]
    assert len(statements) >= 15
    for stmt in statements:
        df = read_sql(stmt, engine=simulated_db)
        assert df is not None


@pytest.mark.integration
def test_kpi_snapshot(simulated_db, tmp_root):
    snap = k.fetch_kpi_snapshot(simulated_db)
    assert snap["executive"]["total_orders"] > 0
    assert snap["inventory_simulated"]["is_simulated"] is True
    assert (tmp_root / "reports" / "kpis" / "kpi_snapshot.json").exists()
