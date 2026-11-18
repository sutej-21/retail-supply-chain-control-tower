# Superset Dashboard Setup

The five dashboards are built **as code** by `superset/bootstrap_dashboards.py` through the Superset REST API.
It was verified against Superset 4.1.2: all charts rendered without errors (screenshots in `docs/images`).

## 1. Prerequisites
* The stack is running (`docker compose up -d`), the Superset UI is reachable at http://localhost:8088, and you can
  log in with `SUPERSET_ADMIN_USER` / `SUPERSET_ADMIN_PASSWORD` from `.env`.
* The pipeline has run at least once, so the `analytics` views contain data. Run one scenario as well, so the
  scenario charts have content:
  `docker compose exec airflow-webserver python -m src.optimization.scenario_analysis --delay 7`

## 2. Create everything
```bash
./scripts/bootstrap_superset.sh            # Docker stack
# or, from a local Python environment:
python superset/bootstrap_dashboards.py --url http://localhost:8088 --username admin \
  --password "$SUPERSET_ADMIN_PASSWORD" \
  --db-uri "postgresql+psycopg2://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/retail_supply_chain"
```
Use host `postgres` when Superset runs in Docker, and `localhost` when Superset runs on the host.
The script is idempotent:
* the database connection and the datasets are reused (and refreshed);
* dashboards are matched by slug, then deleted and recreated together with their charts.

## 3. What gets created
| Dashboard | Slug | Charts | Native filters |
|---|---|---|---|
| 1. Executive Control Tower | `executive-control-tower` | 6 KPI tiles, daily revenue, weekly orders, revenue by category, delivery performance, inventory risk | Date range, Product category, Seller, Customer state, Order status |
| 2. Inventory Monitoring [SIMULATED] | `inventory-monitoring` | 6 KPI tiles, inventory by category and risk, stock vs reorder level, consumption trend, forecast vs available, reorder recommendations | Product category, Product, Warehouse |
| 3. Delivery & Supplier Performance | `delivery-supplier-performance` | 4 KPI tiles, delays by state, delivery days by state, seller ranking, review by delay, supplier lead time, supplier reliability | Date range, Seller, Customer state |
| 4. Demand Forecasting (Prophet) | `demand-forecasting` | 5 KPI tiles, forecast vs history, 7/30/60-day forecasts, category forecast, top products, demand growth, accuracy table | Date range, Forecast entity |
| 5. Alerts & Scenario Analysis | `alerts-scenarios` | 4 KPI tiles, alerts by severity and type, alert history, active alerts table, scenario projection, scenario comparison, all scenarios, affected products | Product, Alert severity |

Colour convention, set through `label_colors` in the dashboard metadata:
* Healthy / LOW / on_time = **green #2E7D32**
* Warning / MEDIUM = **yellow #F9A825**
* High Risk / HIGH = **orange #EF6C00**
* Critical / CRITICAL / late = **red #C62828**

Each dashboard opens with a Markdown header that states which figures are historical (Olist) and which are
SIMULATED. Dashboards auto-refresh every hour, matching the DAG schedule.

## 4. Manual adjustments (optional)
* **Tooltips / descriptions:** edit a chart, then open *Customize → Description*.
* **Native-filter scope:** in a dashboard, open *Filters → ⚙ → Scoping*. A filter only applies to datasets that
  contain its column.
* **Exporting:** *Dashboard → … → Download → Export YAML* produces a version-controlled copy.

## 5. Troubleshooting
| Symptom | Fix |
|---|---|
| `Custom SQL fields cannot contain sub-queries` | Use the `vw_latest_scenario_*` views (already used by the script). |
| `Connection refused` creating the database | Use host `postgres` (Docker network), not `localhost`. |
| Empty scenario charts | Run `python -m src.optimization.scenario_analysis` once. |
| 401 / CSRF errors | Check the admin credentials; the script fetches a CSRF token automatically. |
