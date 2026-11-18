# Methodology

## 1. Historical vs simulated data

| Domain | Source | How it is labelled |
|---|---|---|
| Orders, items, payments, reviews, customers, sellers, products, geolocation | **Olist (real, historical 2016-2018)** | `data_source = 'OLIST_HISTORICAL'` where relevant |
| Inventory, warehouses, suppliers, purchase orders, lead times, inbound shipments | **Simulated** (`src/simulation`) | `is_simulated = TRUE`, `data_source = 'SIMULATED'`, "[SIM]" in dashboards, fictional supplier names |
| `data/sample` CSVs | **Synthetic sample** in Olist schema (tests / offline demo) | `SAMPLE_DATA_README.txt`, `DATA_SOURCE=sample` |

Simulated values are never presented as Olist data. Demand in the simulation is derived from real Olist demand
(average and standard deviation of daily units over the last 90 days of history).

## 2. Data quality and cleaning

`src/preprocessing/data_validation.py` profiles every file (rows, columns, inferred types, nulls, distinct values,
duplicates) and runs these checks: non-empty tables, primary-key uniqueness and not-null, 9 foreign-key relations,
parse errors for numbers and timestamps, timestamp logic (e.g. delivered before purchase), value ranges (price ≤ 0,
review outside 1-5, coordinates outside Brazil), payment reconciliation, IQR outliers and missing or untranslated
categories. CRITICAL failures stop the pipeline. Everything else is reported in
`reports/data_quality/data_quality_report.md` and in `analytics.data_quality_log`.

The cleaning rules (`clean_data.py`) are listed in `reports/data_quality/cleaning_log.md`. Each entry gives the
rule, the number of rows affected, the action and the reason. Only two kinds of rows are removed: exact duplicate
rows, and geolocation points outside Brazil (they are excluded from the zip-code centroid). Everything else is
flagged:

* `has_timestamp_issue` → the order is excluded from delivery-time KPIs and kept in sales KPIs.
* `is_price_valid = false` → the item is excluded from average unit price.
* A missing category becomes `unknown`.

## 3. Avoiding double counting

Order items and payments are aggregated to the order grain in CTEs **before** they are joined to orders.
Reviews are reduced to the latest review per order. Seller delivery metrics use the order × seller shipment grain.
Integration tests assert that `SUM(fact_orders.items_value) = SUM(fact_order_items.price)` and that order-level
payments equal `SUM(fact_payments.payment_value)`.

## 4. Inventory simulation

* **SKU set:** the top `SIM_N_PRODUCTS` products by units sold. Each one is mapped to a simulated supplier (two per
  category) and to the warehouse in the state of its main seller (falling back to SP).
* **Hourly step:** (1) receive POs that have arrived, (2) ship the previous hour's reservations, which reduces
  on-hand stock, (3) start or stop demand surges (probability 0.15%/h, ×3-6 for 6-18 h), (4) generate new demand
  `Poisson(d/24 · intraday weight · surge · Gamma(2, 0.5))` and reserve it against available stock (unmet demand is
  counted as lost), (5) apply occasional shrinkage adjustments, (6) raise a PO when inventory position ≤ reorder
  level and no PO is open, (7) take a snapshot.
* **Lead time:** planned average + Normal(0, σ), plus a 5-15 day disruption with a probability that rises as the
  supplier's latent reliability falls.
* **Reproducibility:** the RNG for each hour is seeded with `(SIMULATION_RANDOM_SEED, hour index)`. IDs are
  deterministic hashes and all inserts use ON CONFLICT, so re-running the same hours changes nothing.
* **Catch-up:** if runs were missed, up to 168 hours are simulated in one run.

## 5. Inventory optimisation

* `SS = z · sqrt(L σd² + d² σL²)`, with z = Φ⁻¹(service level, default 0.95).
* `ROP = d · L + SS`.
* `S = d (L + R) + SS`, with review period R = 7 days.
* `Q = max(0, S − position)`, capped at maximum stock and rounded up to the MOQ.
* `d` is the Prophet 30-day mean forecast where a product forecast exists, otherwise the historical mean. σd is
  scaled so the historical coefficient of variation is kept. `L`/σL are the realised simulated lead-time
  statistics once 3 or more POs have been received, otherwise the planned values.
* Risk class (first match wins):
  1. **Critical:** net < SS.
  2. **High Risk:** cover < L.
  3. **Warning:** net ≤ ROP·(1 + 20%).
  4. **Healthy:** everything else.

## 6. Anomaly detection

| Type | Method | Notes |
|---|---|---|
| Demand spike (category × day) | z-score vs the previous 28 days (min 14), z ≥ 3 and units ≥ 5 | The minimum volume suppresses spikes in tiny categories |
| Sales drop (day) | revenue **or** order-count z ≤ −3 | Days after the incomplete data tail are ignored |
| Seller delay (seller × week) | robust z (median/MAD) of mean delay across all seller-weeks, ≥ 5 deliveries | |
| Order delay | Isolation Forest (delivery days, delay, freight, items), contamination 1%; only **late** outliers are kept | Unusually fast deliveries are not treated as problems |
| Unusual order quantity | Isolation Forest **and** item-count z ≥ 5 | Severity is relative to the 99th-percentile basket size |
| Supplier lead time **(SIM)** | z vs planned lead time ≥ 3, or > 14 days | |
| Rapid depletion **(SIM)** | Poisson z of 24 h consumption ≥ 3 **and** ratio ≥ 2.5× expected | |

All rolling statistics use only past observations. Severity: |z| ≥ 3 → MEDIUM, ≥ 4 → HIGH, ≥ 5 → CRITICAL.
By default only HIGH and CRITICAL anomalies create alerts.

**False-positive limitations:** promotions (e.g. Black Friday 2017), catalogue launches and data-extraction
artefacts are statistically unusual without being operational failures. Alerts therefore recommend *verification*
first. All thresholds can be set through `ANOMALY_*` environment variables.

## 7. Demand forecasting (Prophet)

1. Units per day (revenue orders), with missing dates filled with 0, from `FORECAST_TRAIN_START` (2017-01-01).
2. **Effective data end:** the last date with volume ≥ 10% of the median of the non-zero days in the last 180
   days. The near-empty days at the end of the Olist extract are excluded. This date is the forecast origin.
3. **Quality gate:** at least 90 days of history, at least 30% non-zero days, no negative values.
4. **Seasonality:** weekly seasonality is used only when a Kruskal-Wallis day-of-week test gives p < 0.05. Yearly
   seasonality needs at least 730 days, so it is **off** for Olist (≈ 20 months of usable history). Brazilian
   holidays are included.
5. **Accuracy:** a chronological hold-out of the last 30 days. The model is trained only on data before the
   hold-out, so there is no leakage. Metrics: MAE, RMSE, MAPE (non-zero days only), WAPE, and a seasonal-naive
   (t-7) MAE benchmark. All are stored in `forecast_accuracy`.
6. **Final forecast:** the model is refit on all data up to the origin and forecasts 60 days with an 80%
   interval. The 7- and 30-day horizons are subsets of it. Negative values are clipped to 0. Horizons beyond 60
   days are rejected.

*Example output from the synthetic sample (30k orders), not Olist results:* overall MAE 9.0 units/day,
WAPE 10.8%, against a seasonal-naive MAE of 11.5. Product-level WAPE is high (≈ 85%) because single products sell
about 1 unit/day, which is intermittent demand where point forecasts are inherently weak. Use the prediction
intervals and the category-level forecasts for planning. Run the pipeline on the real Kaggle data to get real
accuracy figures. They are written to `analytics.forecast_accuracy` and to the Forecasting dashboard; none are
quoted here.

## 8. What-if scenarios

A daily projection over 60 days compares a BASELINE with a MODIFIED scenario. The parameters are supplier delay,
demand growth, inventory adjustment, replenishment-quantity override and safety-stock multiplier. The planner's
reorder point stays at the baseline demand level. Outputs per SKU:

* projected and minimum stock
* stock-out date
* unfulfilled units
* additional cover days or units required
* **revenue exposure proxy** = unfulfilled units × historical average price

The exposure proxy is a *potential* exposure and not confirmed lost revenue, because customers may wait,
substitute or buy elsewhere.

## 9. Alerting

There are 7 rule families (see `notification_service.py`).

* **Fingerprint:** each alert's fingerprint is `(type, entity)`. Repeats only increase `occurrences`.
* **Re-notification:** an alert is sent again only when its severity escalates or the cooldown (24 h) has expired.
* **Resolution:** conditions that disappear are auto-resolved.
* **Delivery:** the top 10 alerts are sent individually and the rest in a digest.
* **Modes:** `local` (JSONL outbox + log, needs no credentials), `slack`, `email`, `all`. Delivery failures never
  stop the pipeline.
* **Pipeline failures:** Airflow task failures trigger `send_failure_notification`.
