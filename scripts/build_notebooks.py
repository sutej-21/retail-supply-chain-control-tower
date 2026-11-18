"""Generate the four analysis notebooks (kept as code so they stay in sync with the modules)."""
from pathlib import Path

import nbformat as nbf

SETUP = """import sys, os
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == 'notebooks' else Path.cwd()
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
import pandas as pd, numpy as np
import plotly.express as px
from config.database import read_sql
pd.set_option('display.max_columns', 50)
# NOTE: results reflect whatever data the pipeline loaded (DATA_SOURCE=kaggle -> real Olist, sample -> SYNTHETIC)."""

NOTEBOOKS = {
    "01_exploratory_data_analysis.ipynb": [
        ("md", "# 01 - Exploratory Data Analysis\nOlist Brazilian E-Commerce (CC BY-NC-SA 4.0). Requires one pipeline run "
               "(`python -m src.pipeline.run_pipeline`)."),
        ("code", SETUP),
        ("code", "read_sql('SELECT * FROM analytics.vw_data_window')"),
        ("code", "read_sql('SELECT * FROM analytics.vw_executive_kpis').T"),
        ("md", "## Sales over time"),
        ("code", "daily = read_sql('SELECT * FROM analytics.vw_daily_sales ORDER BY sales_date')\n"
                 "px.line(daily, x='sales_date', y='revenue', title='Daily revenue (BRL)')"),
        ("code", "monthly = read_sql('SELECT * FROM analytics.vw_monthly_sales ORDER BY month')\n"
                 "px.bar(monthly, x='month', y='orders', title='Monthly orders')"),
        ("md", "## Category, product and payment mix"),
        ("code", "cat = read_sql('SELECT product_category, SUM(units) units, SUM(revenue) revenue FROM analytics.vw_category_demand GROUP BY 1 ORDER BY 3 DESC LIMIT 15')\n"
                 "px.bar(cat, x='revenue', y='product_category', orientation='h', title='Top categories by revenue')"),
        ("code", "read_sql('SELECT primary_payment_type, COUNT(*) orders, ROUND(AVG(max_installments),2) avg_installments FROM analytics.fact_orders GROUP BY 1 ORDER BY 2 DESC')"),
        ("code", "read_sql('SELECT customer_state, COUNT(*) orders, ROUND(SUM(items_value)) revenue FROM analytics.fact_orders WHERE is_revenue_order GROUP BY 1 ORDER BY 2 DESC LIMIT 10')"),
        ("md", "## Customers\n`customer_id` is unique per order, so repeat behaviour must use `customer_unique_id`."),
        ("code", "read_sql('SELECT * FROM analytics.vw_customer_kpis').T"),
    ],
    "02_data_quality_analysis.ipynb": [
        ("md", "# 02 - Data Quality Analysis\nRe-runs the profiling and checks from `src/preprocessing/data_validation.py` "
               "and shows the cleaning log. Nothing is deleted silently."),
        ("code", SETUP),
        ("code", "from src.ingestion.load_data import load_source_csvs\n"
                 "from src.preprocessing.data_validation import run_data_quality_checks, profile_table\n"
                 "frames = load_source_csvs(as_text=True)\n{k: v.shape for k, v in frames.items()}"),
        ("code", "checks = run_data_quality_checks(frames)\nchecks['status'].value_counts()"),
        ("code", "checks[checks.status != 'PASS'].sort_values('failing_count', ascending=False)"),
        ("code", "prof = profile_table('orders', frames['orders'])\npd.DataFrame(prof['column_profile'])"),
        ("md", "## Cleaning decisions"),
        ("code", "import json\nlog = json.loads((ROOT / 'reports/data_quality/cleaning_log.json').read_text())\npd.DataFrame(log['entries'])"),
        ("code", "read_sql(\"SELECT run_id, status, COUNT(*) FROM analytics.data_quality_log GROUP BY 1,2 ORDER BY 1 DESC LIMIT 10\")"),
    ],
    "03_delivery_analysis.ipynb": [
        ("md", "# 03 - Delivery Analysis\nHow do delivery delays affect customer satisfaction?"),
        ("code", SETUP),
        ("code", "read_sql('SELECT * FROM analytics.vw_delivery_kpis').T"),
        ("code", "b = read_sql('SELECT * FROM analytics.vw_review_by_delay_bucket ORDER BY sort_key')\n"
                 "px.bar(b, x='delay_bucket', y='avg_review_score', text='orders', title='Average review score by delivery delay')"),
        ("code", "read_sql('SELECT * FROM analytics.vw_review_by_delivery_status')"),
        ("code", "s = read_sql('SELECT * FROM analytics.vw_delivery_by_state ORDER BY on_time_rate_pct')\n"
                 "px.bar(s, x='customer_state', y='on_time_rate_pct', title='On-time rate by customer state')"),
        ("code", "read_sql('SELECT * FROM analytics.vw_seller_performance WHERE performance_rank IS NOT NULL ORDER BY on_time_rate_pct LIMIT 15')"),
        ("code", "o = read_sql('SELECT delay_days, review_score FROM analytics.fact_orders WHERE delay_days IS NOT NULL AND review_score IS NOT NULL')\n"
                 "print('Spearman correlation delay vs review:', round(o.delay_days.corr(o.review_score, method='spearman'), 3))"),
        ("code", "read_sql(\"SELECT anomaly_type, severity, COUNT(*) FROM analytics.anomaly_events WHERE anomaly_type LIKE 'DELIVERY%%' GROUP BY 1,2\")"),
    ],
    "04_demand_forecasting.ipynb": [
        ("md", "# 04 - Demand Forecasting (Prophet)\nChronological hold-out evaluation and 60-day forecast for overall demand, "
               "using the same functions as the pipeline (`src/ml/demand_forecasting.py`)."),
        ("code", SETUP),
        ("code", "from config.settings import get_settings\nfrom src.ml import demand_forecasting as f\n"
                 "from src.utils.helpers import effective_data_end\ncfg = get_settings().forecast\n"
                 "from config.database import get_engine\nitems = f.load_item_history(get_engine())\n"
                 "daily = f.to_daily_series(items, start=cfg.train_start)\nend = effective_data_end(daily.set_index('ds')['y'])\n"
                 "series = daily[daily.ds <= end]\nprint('forecast origin:', end.date(), '| days:', len(series))"),
        ("code", "q = f.check_series_quality(series, cfg)\nq, 'weekly seasonality supported:', f.weekly_seasonality_supported(series)"),
        ("code", "res = f.forecast_entity(series, cfg)\npd.Series(res['evaluation']['metrics'])"),
        ("code", "ho = res['evaluation']['holdout_frame']; fc = res['forecast']\n"
                 "fig = px.line(series.tail(150), x='ds', y='y', title='Actual vs hold-out prediction vs forecast')\n"
                 "fig.add_scatter(x=ho.ds, y=ho.yhat, name='hold-out prediction')\n"
                 "fig.add_scatter(x=fc.ds, y=fc.yhat, name='forecast')\n"
                 "fig.add_scatter(x=fc.ds, y=fc.yhat_upper, name='upper', line=dict(dash='dot'))\n"
                 "fig.add_scatter(x=fc.ds, y=fc.yhat_lower, name='lower', line=dict(dash='dot'))\nfig"),
        ("code", "pd.DataFrame({h: [fc[fc.horizon_day <= h].yhat.sum()] for h in (7, 30, 60)}, index=['forecast units'])"),
        ("code", "read_sql('SELECT model_level, COUNT(*) models, ROUND(AVG(mae),3) mae, ROUND(AVG(rmse),3) rmse, ROUND(AVG(wape),1) wape, ROUND(AVG(naive_mae),3) naive_mae FROM analytics.forecast_accuracy GROUP BY 1')"),
    ],
}

for name, cells in NOTEBOOKS.items():
    nb = nbf.v4.new_notebook()
    nb.cells = [nbf.v4.new_markdown_cell(c) if t == "md" else nbf.v4.new_code_cell(c) for t, c in cells]
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nbf.write(nb, Path(__file__).resolve().parents[1] / "notebooks" / name)
    print("wrote", name)
