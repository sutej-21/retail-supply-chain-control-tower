"""Create the PostgreSQL connection, datasets, charts and the five Control Tower
dashboards in Apache Superset through its REST API (tested with Superset 4.1.2).

Usage (Superset must be running):
    python superset/bootstrap_dashboards.py \
        --url http://localhost:8088 --username admin --password <pwd> \
        --db-uri postgresql+psycopg2://<user>:<pwd>@postgres:5432/retail_supply_chain

Environment variable equivalents: SUPERSET_URL, SUPERSET_ADMIN_USER,
SUPERSET_ADMIN_PASSWORD, SUPERSET_ANALYTICS_DB_URI.

The script is idempotent: dashboards (by slug) and their charts are deleted and
recreated on every run; the database connection and datasets are reused.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

import requests

DB_NAME = "Retail Supply Chain (PostgreSQL)"
SCHEMA = "analytics"
RISK_COLORS = {"Critical": "#C62828", "High Risk": "#EF6C00", "Warning": "#F9A825", "Healthy": "#2E7D32",
               "CRITICAL": "#C62828", "HIGH": "#EF6C00", "MEDIUM": "#F9A825", "LOW": "#2E7D32",
               "on_time": "#2E7D32", "late": "#C62828", "not_delivered": "#9E9E9E", "invalid_timestamps": "#757575",
               "BASELINE": "#2E7D32", "MODIFIED": "#C62828"}

DATASETS = [
    "vw_sales_items_enriched", "vw_daily_sales", "vw_monthly_sales", "vw_delivery_daily", "vw_delivery_by_state",
    "vw_seller_performance", "vw_review_by_delivery_status", "vw_review_by_delay_bucket", "vw_satisfaction_trend",
    "vw_current_inventory", "vw_inventory_kpis", "vw_inventory_by_category", "vw_inventory_trend",
    "vw_inventory_consumption_daily", "vw_forecast_vs_inventory", "vw_supplier_kpis", "vw_forecast_latest",
    "vw_demand_actual_vs_forecast", "vw_product_demand_growth", "vw_category_demand_growth", "vw_top_products",
    "vw_active_alerts", "alert_history", "vw_scenario_summary", "scenario_projection", "scenario_results",
    "forecast_accuracy", "anomaly_events", "vw_latest_scenario_projection", "vw_latest_scenario_results",
    "vw_latest_scenario_summary",
]


def m(sql: str, label: str) -> dict:
    """Ad-hoc SQL metric."""
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label, "optionName": f"metric_{uuid.uuid4().hex[:10]}"}


def where(sql: str) -> dict:
    return {"expressionType": "SQL", "sqlExpression": sql, "clause": "WHERE", "filterOptionName": uuid.uuid4().hex[:10]}


# --------------------------------------------------------------------------- #
# Chart builders (Superset 4.1 form_data)
# --------------------------------------------------------------------------- #
def big_number(name, ds, metric, subheader, fmt="SMART_NUMBER", filters=None):
    return {"name": name, "ds": ds, "viz": "big_number_total",
            "params": {"metric": metric, "subheader": subheader, "y_axis_format": fmt,
                       "header_font_size": 0.4, "subheader_font_size": 0.125, "adhoc_filters": filters or [],
                       "time_range": "No filter"}}


def line(name, ds, x, metrics, groupby=None, filters=None, grain="P1D", y_fmt="SMART_NUMBER", title_y="", limit=None,
         series_limit=None):
    p = {"x_axis": x, "time_grain_sqla": grain, "metrics": metrics, "groupby": groupby or [],
         "adhoc_filters": filters or [], "row_limit": 50000, "rich_tooltip": True, "show_legend": True,
         "legendType": "scroll", "legendOrientation": "top", "y_axis_format": y_fmt, "y_axis_title": title_y,
         "y_axis_title_margin": 30, "truncateYAxis": False, "time_range": "No filter", "markerEnabled": False,
         "zoomable": True}
    if series_limit:
        p.update(limit=series_limit, timeseries_limit_metric=metrics[0], order_desc=True)
    return {"name": name, "ds": ds, "viz": "echarts_timeseries_line", "params": p}


def bar(name, ds, x, metrics, groupby=None, filters=None, horizontal=False, limit=15, sort_metric=True,
        stack=False, y_fmt="SMART_NUMBER", sort_asc=False):
    p = {"x_axis": x, "metrics": metrics, "groupby": groupby or [], "adhoc_filters": filters or [],
         "row_limit": limit, "orientation": "horizontal" if horizontal else "vertical", "show_legend": bool(groupby),
         "rich_tooltip": True, "y_axis_format": y_fmt, "time_range": "No filter", "show_value": not stack,
         "stack": "Stack" if stack else None, "x_axis_sort_asc": sort_asc,
         "legendOrientation": "top"}
    if sort_metric:
        p["x_axis_sort"] = metrics[0]["label"]
        if horizontal:
            p["x_axis_sort_asc"] = True   # echarts draws horizontal bars bottom-up: largest on top
    return {"name": name, "ds": ds, "viz": "echarts_timeseries_bar", "params": p}


def pie(name, ds, groupby, metric, filters=None, donut=True):
    return {"name": name, "ds": ds, "viz": "pie",
            "params": {"groupby": [groupby], "metric": metric, "adhoc_filters": filters or [], "row_limit": 20,
                       "show_labels": True, "label_type": "key_percent", "donut": donut, "show_legend": True,
                       "legendOrientation": "right", "time_range": "No filter", "innerRadius": 45,
                       "outerRadius": 75, "number_format": "SMART_NUMBER"}}


def table(name, ds, columns, order_by=None, asc=True, filters=None, limit=200):
    return {"name": name, "ds": ds, "viz": "table",
            "params": {"query_mode": "raw", "all_columns": columns,
                       "order_by_cols": [json.dumps([order_by, asc])] if order_by else [],
                       "adhoc_filters": filters or [], "row_limit": limit, "server_pagination": False,
                       "include_search": True, "page_length": 15, "show_cell_bars": True, "time_range": "No filter"}}


def markdown(text, width=12, height=14):
    return {"markdown": text, "width": width, "height": height}


REV = where("is_revenue_order")

DASHBOARDS = [
    {
        "slug": "executive-control-tower", "title": "1. Executive Control Tower",
        "rows": [
            [markdown("### Executive Control Tower\nSales, delivery and customer KPIs come from **Olist historical data "
                      "(2016-2018)**. Inventory risk and alerts use **SIMULATED** operational data (labelled [SIM]). "
                      "Colours: green = Healthy, yellow = Warning, orange = High Risk, red = Critical.", 12, 12)],
            [big_number("Total Revenue (BRL)", "vw_sales_items_enriched", m("SUM(price)", "Revenue"), "Item revenue, excl. canceled", ",.0f", [REV]),
             big_number("Total Orders", "vw_sales_items_enriched", m("COUNT(DISTINCT order_id)", "Orders"), "Revenue orders", ",.0f", [REV]),
             big_number("Average Order Value (BRL)", "vw_sales_items_enriched", m("SUM(price) / NULLIF(COUNT(DISTINCT order_id), 0)", "AOV"), "Revenue / orders", ",.2f", [REV]),
             big_number("On-Time Delivery Rate", "vw_delivery_daily", m("100.0 * (SUM(delivered_orders) - SUM(late_orders)) / NULLIF(SUM(delivered_orders), 0)", "On-time %"), "% delivered by estimated date", ",.1f"),
             big_number("Products at Risk [SIM]", "vw_current_inventory", m("COUNT(*) FILTER (WHERE risk_status <> 'Healthy')", "At risk"), "SKU-locations not Healthy", ",.0f"),
             big_number("Critical Alerts", "vw_active_alerts", m("COUNT(*) FILTER (WHERE severity = 'CRITICAL')", "Critical"), "Active critical alerts", ",.0f")],
            [line("Daily Revenue Trend", "vw_sales_items_enriched", "purchase_date", [m("SUM(price)", "Revenue (BRL)")], filters=[REV], title_y="BRL"),
             line("Weekly Order Trend", "vw_sales_items_enriched", "purchase_date", [m("COUNT(DISTINCT order_id)", "Orders")], filters=[REV], grain="P1W")],
            [bar("Revenue by Category (Top 15)", "vw_sales_items_enriched", "product_category", [m("SUM(price)", "Revenue (BRL)")], filters=[REV], horizontal=True),
             pie("Delivery Performance", "vw_review_by_delivery_status", "delivery_status", m("SUM(orders_with_review)", "Orders")),
             pie("Inventory Risk Distribution [SIM]", "vw_current_inventory", "risk_status", m("COUNT(*)", "SKU-locations"))],
        ],
    },
    {
        "slug": "inventory-monitoring", "title": "2. Inventory Monitoring [SIMULATED]",
        "rows": [
            [markdown("### Inventory Monitoring - SIMULATED operational data\nStock levels, warehouses and suppliers are "
                      "synthetic (seeded simulation driven by real Olist demand). Reorder points use z * sqrt(L*sd^2 + d^2*sL^2).", 12, 10)],
            [big_number("Units Available [SIM]", "vw_current_inventory", m("SUM(available_stock)", "Units"), "Current simulated stock", ",.0f"),
             big_number("Low-Stock SKUs", "vw_current_inventory", m("COUNT(*) FILTER (WHERE net_available <= reorder_point)", "Low stock"), "Net available <= reorder point", ",.0f"),
             big_number("Critical Stock Alerts", "vw_current_inventory", m("COUNT(*) FILTER (WHERE risk_status = 'Critical')", "Critical"), "Below safety stock", ",.0f"),
             big_number("Days of Inventory on Hand", "vw_current_inventory", m("SUM(net_available) / NULLIF(SUM(planning_daily_demand), 0)", "Days"), "Net stock / daily demand", ",.1f"),
             big_number("Inventory Value (BRL, proxy)", "vw_current_inventory", m("SUM(inventory_value_proxy)", "Value"), "Units x simulated unit cost", ",.0f"),
             big_number("Recommended Reorder Units", "vw_current_inventory", m("SUM(reorder_qty)", "Units"), "Sum of reorder quantities", ",.0f")],
            [bar("Inventory by Category and Risk", "vw_inventory_by_category", "product_category", [m("SUM(sku_locations)", "SKU-locations")], groupby=["risk_status"], horizontal=True, stack=True, limit=200, sort_metric=False),
             bar("Stock vs Reorder Point - Most At-Risk SKUs", "vw_current_inventory", "product_id", [m("SUM(net_available)", "Net available"), m("SUM(reorder_point)", "Reorder point"), m("SUM(safety_stock)", "Safety stock")], filters=[where("risk_status <> 'Healthy'")], limit=15, sort_metric=False)],
            [line("Inventory Consumption Trend (units/day)", "vw_inventory_consumption_daily", "movement_date", [m("SUM(units)", "Units")], groupby=["movement_type"]),
             bar("Forecast Demand (30d) vs Available - Top Gaps", "vw_forecast_vs_inventory", "product_id", [m("SUM(demand_next_30d)", "Demand next 30d"), m("SUM(net_available)", "Net available")], limit=15, sort_metric=True)],
            [table("Reorder Recommendations", "vw_current_inventory", ["risk_status", "product_id", "product_category", "warehouse_id", "supplier_id", "net_available", "safety_stock", "reorder_point", "reorder_qty", "days_of_cover", "lead_time_days", "recommended_action"], order_by="risk_rank", filters=[where("risk_status <> 'Healthy'")])],
        ],
    },
    {
        "slug": "delivery-supplier-performance", "title": "3. Delivery & Supplier Performance",
        "rows": [
            [markdown("### Delivery (Olist historical) & Supplier (SIMULATED) performance\nOn-time = delivered on or before the estimated delivery date. "
                      "Supplier lead times and reliability come from simulated purchase orders.", 12, 10)],
            [big_number("On-Time Delivery %", "vw_delivery_daily", m("100.0 * (SUM(delivered_orders) - SUM(late_orders)) / NULLIF(SUM(delivered_orders), 0)", "On-time %"), "Delivered orders", ",.1f"),
             big_number("Avg Delivery Duration (days)", "vw_delivery_daily", m("SUM(avg_delivery_days * delivered_orders) / NULLIF(SUM(delivered_orders), 0)", "Days"), "Purchase -> customer", ",.1f"),
             big_number("Delayed Orders", "vw_delivery_daily", m("SUM(late_orders)", "Late"), "Delivered after estimate", ",.0f"),
             big_number("Avg Supplier Lead Time [SIM]", "vw_supplier_kpis", m("AVG(avg_actual_lead_time_days)", "Days"), "Received simulated POs", ",.1f")],
            [bar("Late Delivery Rate by Customer State", "vw_delivery_by_state", "customer_state", [m("100 - AVG(on_time_rate_pct)", "Late %")], limit=30, y_fmt=",.1f"),
             bar("Avg Delivery Days by Customer State", "vw_delivery_by_state", "customer_state", [m("AVG(avg_delivery_days)", "Days")], limit=30, y_fmt=",.1f")],
            [table("Seller Performance Ranking (>= 10 deliveries)", "vw_seller_performance", ["performance_rank", "seller_id", "seller_state", "delivered", "late", "on_time_rate_pct", "avg_delivery_days", "avg_review_score", "revenue"], order_by="performance_rank", filters=[where("performance_rank IS NOT NULL")]),
             bar("Review Score by Delay Bucket", "vw_review_by_delay_bucket", "delay_bucket", [m("AVG(avg_review_score)", "Avg review")], sort_metric=False, y_fmt=",.2f")],
            [bar("Average Supplier Lead Time [SIM]", "vw_supplier_kpis", "supplier_id", [m("AVG(average_lead_time_days)", "Planned"), m("AVG(avg_actual_lead_time_days)", "Actual (simulated)")], limit=30, sort_metric=False),
             bar("Supplier Reliability [SIM]", "vw_supplier_kpis", "supplier_id", [m("AVG(reliability_score_pct)", "Reliability %")], limit=30, y_fmt=",.0f", sort_asc=True)],
        ],
    },
    {
        "slug": "demand-forecasting", "title": "4. Demand Forecasting (Prophet)",
        "rows": [
            [markdown("### Demand forecasting with Prophet\nForecast origin = last complete day of the Olist history (2018). "
                      "Shaded band = prediction interval. Accuracy is measured on a chronological 30-day hold-out.", 12, 10)],
            [big_number("Actual Units (history)", "vw_demand_actual_vs_forecast", m("SUM(actual_units)", "Units"), "Overall, all history", ",.0f", [where("model_level = 'overall'")]),
             big_number("Forecast Units - next 7 days", "vw_forecast_latest", m("SUM(yhat)", "Units"), "Overall demand", ",.0f", [where("model_level = 'overall' AND horizon_day <= 7")]),
             big_number("Forecast Units - next 30 days", "vw_forecast_latest", m("SUM(yhat)", "Units"), "Overall demand", ",.0f", [where("model_level = 'overall' AND horizon_day <= 30")]),
             big_number("Forecast Units - next 60 days", "vw_forecast_latest", m("SUM(yhat)", "Units"), "Overall demand", ",.0f", [where("model_level = 'overall'")]),
             big_number("Overall Hold-out WAPE %", "forecast_accuracy", m("AVG(wape)", "WAPE"), "Lower is better", ",.1f", [where("model_level = 'overall'")])],
            [line("Forecast vs Historical Demand (overall)", "vw_demand_actual_vs_forecast", "ds", [m("SUM(actual_units)", "Actual"), m("SUM(forecast_units)", "Forecast"), m("SUM(forecast_lower)", "Lower"), m("SUM(forecast_upper)", "Upper")], filters=[where("model_level = 'overall' AND ds >= DATE '2018-03-01'")])],
            [line("7-Day Forecast (overall)", "vw_forecast_latest", "forecast_date", [m("SUM(yhat)", "Forecast"), m("SUM(yhat_lower)", "Lower"), m("SUM(yhat_upper)", "Upper")], filters=[where("model_level = 'overall' AND horizon_day <= 7")]),
             line("30-Day Forecast (overall)", "vw_forecast_latest", "forecast_date", [m("SUM(yhat)", "Forecast"), m("SUM(yhat_lower)", "Lower"), m("SUM(yhat_upper)", "Upper")], filters=[where("model_level = 'overall' AND horizon_day <= 30")]),
             line("60-Day Forecast (overall)", "vw_forecast_latest", "forecast_date", [m("SUM(yhat)", "Forecast"), m("SUM(yhat_lower)", "Lower"), m("SUM(yhat_upper)", "Upper")], filters=[where("model_level = 'overall'")])],
            [line("Product Category Forecast (30 days)", "vw_forecast_latest", "forecast_date", [m("SUM(yhat)", "Forecast units")], groupby=["entity_name"], filters=[where("model_level = 'category' AND horizon_day <= 30")]),
             bar("Top Products by Demand", "vw_top_products", "product_id", [m("SUM(units_sold)", "Units sold")], horizontal=True, limit=15)],
            [bar("Category Demand Growth (last 30d vs previous 30d, %)", "vw_category_demand_growth", "product_category", [m("AVG(demand_growth_pct)", "Growth %")], limit=20, y_fmt=",.1f"),
             table("Forecast Accuracy (hold-out)", "forecast_accuracy", ["model_level", "entity_id", "mae", "rmse", "mape", "wape", "naive_mae", "weekly_seasonality", "holdout_start", "holdout_end"], order_by="model_level")],
        ],
    },
    {
        "slug": "alerts-scenarios", "title": "5. Alerts & Scenario Analysis",
        "rows": [
            [markdown("### Alerts & what-if scenarios\nAlerts are de-duplicated by fingerprint. Scenario revenue exposure = unfulfilled units x historical "
                      "average price - a **potential exposure proxy, not confirmed lost revenue**. Run new scenarios with "
                      "`python -m src.optimization.scenario_analysis --delay 7 --growth 10`.", 12, 12)],
            [big_number("Active Alerts", "vw_active_alerts", m("COUNT(*)", "Active"), "All severities", ",.0f"),
             big_number("Critical Alerts", "vw_active_alerts", m("COUNT(*) FILTER (WHERE severity = 'CRITICAL')", "Critical"), "Active", ",.0f"),
             big_number("Affected Products", "vw_active_alerts", m("COUNT(DISTINCT product_id)", "Products"), "Products with active alerts", ",.0f"),
             big_number("Revenue Exposure (proxy, latest scenario)", "vw_latest_scenario_summary", m("SUM(revenue_exposure_proxy)", "BRL"), "Modified scenario, potential", ",.0f", [where("variant = 'MODIFIED'")])],
            [pie("Active Alerts by Severity", "vw_active_alerts", "severity", m("COUNT(*)", "Alerts")),
             bar("Active Alerts by Type", "vw_active_alerts", "alert_type", [m("COUNT(*)", "Alerts")], groupby=["severity"], horizontal=True, stack=True, sort_metric=False, limit=100),
             line("Alert History (new alerts per day)", "alert_history", "created_at", [m("COUNT(*)", "Alerts")], groupby=["severity"])],
            [table("Active Alerts", "vw_active_alerts", ["severity", "alert_type", "title", "product_id", "entity_id", "explanation", "recommended_action", "occurrences", "last_seen_at"], order_by="severity_rank")],
            [line("Latest Scenario: Projected Inventory, Baseline vs Modified", "vw_latest_scenario_projection", "projection_date", [m("SUM(projected_inventory)", "Units")], groupby=["variant"]),
             bar("Latest Scenario: Unfulfilled Units & Products Stocking Out", "vw_latest_scenario_summary", "variant", [m("SUM(unfulfilled_units)", "Unfulfilled units"), m("SUM(products_stocking_out)", "Products stocking out")], sort_metric=False, limit=10)],
            [table("All Scenarios (baseline vs modified)", "vw_scenario_summary", ["scenario_name", "variant", "supplier_delay_days", "demand_growth_pct", "products_stocking_out", "products_affected", "unfulfilled_units", "revenue_exposure_proxy", "created_at"], order_by="created_at", asc=False)],
            [table("Products Affected by Latest Scenario", "vw_latest_scenario_results", ["product_id", "category_name_en", "supplier_id", "starting_stock", "daily_demand", "lead_time_days", "stockout_date", "unfulfilled_units", "revenue_exposure_proxy"], order_by="revenue_exposure_proxy", asc=False, filters=[where("variant = 'MODIFIED' AND is_affected")])],
        ],
    },
]

# Native filters: (name, filter type, dataset, column, dashboards)
FILTERS = [
    ("Date range", "filter_time", None, None, ["executive-control-tower", "delivery-supplier-performance", "demand-forecasting"]),
    ("Product category", "filter_select", "vw_sales_items_enriched", "product_category", ["executive-control-tower", "inventory-monitoring"]),
    ("Product", "filter_select", "vw_current_inventory", "product_id", ["inventory-monitoring", "alerts-scenarios"]),
    ("Seller", "filter_select", "vw_sales_items_enriched", "seller_id", ["executive-control-tower", "delivery-supplier-performance"]),
    ("Customer state", "filter_select", "vw_sales_items_enriched", "customer_state", ["executive-control-tower", "delivery-supplier-performance"]),
    ("Order status", "filter_select", "vw_sales_items_enriched", "order_status", ["executive-control-tower"]),
    ("Alert severity", "filter_select", "vw_active_alerts", "severity", ["alerts-scenarios"]),
    ("Warehouse", "filter_select", "vw_current_inventory", "warehouse_id", ["inventory-monitoring"]),
    ("Forecast entity", "filter_select", "vw_forecast_latest", "entity_name", ["demand-forecasting"]),
]

CSS = """
.dashboard-header { background: #0B1F33; }
.dashboard-header .editable-title input, .dashboard-header .dynamic-title { color: #FFFFFF !important; }
.dashboard-component-chart-holder { border-radius: 10px; box-shadow: 0 1px 4px rgba(16,24,40,.12); }
.header-title { font-weight: 600; }
"""


class Superset:
    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.s = requests.Session()
        r = self.s.post(f"{self.url}/api/v1/security/login",
                        json={"username": username, "password": password, "provider": "db", "refresh": True}, timeout=30)
        r.raise_for_status()
        self.s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        csrf = self.s.get(f"{self.url}/api/v1/security/csrf_token/", timeout=30)
        if csrf.ok:
            self.s.headers["X-CSRFToken"] = csrf.json()["result"]
        self.s.headers["Referer"] = self.url

    def req(self, method: str, path: str, **kw):
        r = self.s.request(method, f"{self.url}{path}", timeout=60, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.text else {}

    def find(self, resource: str, col: str, value: str) -> list[dict]:
        q = f"(filters:!((col:{col},opr:eq,value:'{value}')),page_size:100)"
        return self.req("GET", f"/api/v1/{resource}/?q={q}").get("result", [])


def ensure_database(api: Superset, uri: str) -> int:
    found = api.find("database", "database_name", DB_NAME)
    payload = {"database_name": DB_NAME, "sqlalchemy_uri": uri, "expose_in_sqllab": True, "allow_run_async": False}
    if found:
        api.req("PUT", f"/api/v1/database/{found[0]['id']}", json=payload)
        return found[0]["id"]
    return api.req("POST", "/api/v1/database/", json=payload)["id"]


def ensure_datasets(api: Superset, db_id: int) -> dict[str, int]:
    ids = {}
    for name in DATASETS:
        found = [d for d in api.find("dataset", "table_name", name) if d.get("schema") == SCHEMA]
        if found:
            ids[name] = found[0]["id"]
            api.req("PUT", f"/api/v1/dataset/{ids[name]}/refresh")
        else:
            ids[name] = api.req("POST", "/api/v1/dataset/", json={"database": db_id, "schema": SCHEMA,
                                                                  "table_name": name})["id"]
    return ids


def _position(title: str, rows: list[list[tuple[str, dict]]]) -> dict:
    pos = {"DASHBOARD_VERSION_KEY": "v2", "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
           "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
           "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": title}}}
    for r_i, row in enumerate(rows):
        row_id = f"ROW-{r_i}"
        pos["GRID_ID"]["children"].append(row_id)
        pos[row_id] = {"type": "ROW", "id": row_id, "children": [], "parents": ["ROOT_ID", "GRID_ID"],
                       "meta": {"background": "BACKGROUND_TRANSPARENT"}}
        for c_i, (kind, meta) in enumerate(row):
            cid = f"{kind}-{r_i}-{c_i}"
            pos[row_id]["children"].append(cid)
            pos[cid] = {"type": kind, "id": cid, "children": [], "parents": ["ROOT_ID", "GRID_ID", row_id], "meta": meta}
    return pos


def build_dashboards(api: Superset, ds_ids: dict[str, int]) -> list[str]:
    created = []
    for spec in DASHBOARDS:
        for existing in api.find("dashboard", "slug", spec["slug"]):
            charts = api.req("GET", f"/api/v1/dashboard/{existing['id']}/charts").get("result", [])
            api.req("DELETE", f"/api/v1/dashboard/{existing['id']}")
            for c in charts:
                api.req("DELETE", f"/api/v1/chart/{c['id']}")
        dash_id = api.req("POST", "/api/v1/dashboard/", json={"dashboard_title": spec["title"], "slug": spec["slug"],
                                                              "published": True, "css": CSS})["id"]
        layout_rows, chart_ids = [], []
        for row in spec["rows"]:
            n_charts = len([c for c in row if "markdown" not in c])
            width = max(2, 12 // max(1, len(row)))
            out_row = []
            for item in row:
                if "markdown" in item:
                    out_row.append(("MARKDOWN", {"width": item["width"], "height": item["height"], "code": item["markdown"]}))
                    continue
                params = {**item["params"], "viz_type": item["viz"], "datasource": f"{ds_ids[item['ds']]}__table"}
                chart = api.req("POST", "/api/v1/chart/", json={
                    "slice_name": item["name"], "viz_type": item["viz"], "datasource_id": ds_ids[item["ds"]],
                    "datasource_type": "table", "params": json.dumps(params), "dashboards": [dash_id]})
                chart_ids.append(chart["id"])
                height = 22 if item["viz"] == "big_number_total" else (60 if item["viz"] == "table" else 50)
                w = 12 if n_charts == 1 else width
                out_row.append(("CHART", {"width": w, "height": height, "chartId": chart["id"], "sliceName": item["name"]}))
            layout_rows.append(out_row)
        position = _position(spec["title"], layout_rows)
        native = []
        for name, ftype, ds, col, slugs in FILTERS:
            if spec["slug"] not in slugs:
                continue
            f = {"id": f"NATIVE_FILTER-{uuid.uuid4().hex[:10]}", "name": name, "filterType": ftype, "type": "NATIVE_FILTER",
                 "targets": [{}] if ds is None else [{"datasetId": ds_ids[ds], "column": {"name": col}}],
                 "defaultDataMask": {"filterState": {}, "extraFormData": {}}, "cascadeParentIds": [],
                 "scope": {"rootPath": ["ROOT_ID"], "excluded": []}, "description": "",
                 "controlValues": {"multiSelect": True, "enableEmptyFilter": False, "searchAllOptions": True}
                 if ftype == "filter_select" else {}}
            native.append(f)
        metadata = {"label_colors": RISK_COLORS, "color_scheme": "supersetColors", "native_filter_configuration": native,
                    "refresh_frequency": 3600, "cross_filters_enabled": True,
                    "chart_configuration": {}, "positions": position}
        api.req("PUT", f"/api/v1/dashboard/{dash_id}", json={"position_json": json.dumps(position),
                                                             "json_metadata": json.dumps(metadata)})
        created.append(f"{api.url}/superset/dashboard/{spec['slug']}/")
        print(f"Dashboard '{spec['title']}': {len(chart_ids)} charts, {len(native)} filters")
    return created


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.getenv("SUPERSET_URL", "http://localhost:8088"))
    ap.add_argument("--username", default=os.getenv("SUPERSET_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.getenv("SUPERSET_ADMIN_PASSWORD"))
    ap.add_argument("--db-uri", default=os.getenv("SUPERSET_ANALYTICS_DB_URI"))
    a = ap.parse_args()
    if not a.password or not a.db_uri:
        ap.error("--password/SUPERSET_ADMIN_PASSWORD and --db-uri/SUPERSET_ANALYTICS_DB_URI are required")
    api = Superset(a.url, a.username, a.password)
    db_id = ensure_database(api, a.db_uri)
    ds_ids = ensure_datasets(api, db_id)
    print(f"Database id={db_id}; {len(ds_ids)} datasets ready")
    for url in build_dashboards(api, ds_ids):
        print("  ", url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
