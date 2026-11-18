"""Generate a SYNTHETIC sample dataset with the exact Olist CSV schema.

Purpose
-------
* Lets the test-suite and a first end-to-end demo run **without** downloading
  the Kaggle dataset.
* Contains deliberately planted data-quality issues and anomalies so the
  validation and anomaly-detection modules can be verified.

IMPORTANT: This is NOT Olist data. Every value is randomly generated. Files are
written to ``data/sample`` (never to ``data/raw``) unless a directory is passed
explicitly, and a ``SAMPLE_DATA_README.txt`` marker is written alongside.

Planted anomalies (documented so tests can assert on them):
* ``PLANTED_SPIKE_DATE``: demand for the "PLANTED_SPIKE_CATEGORY" category x8.
* ``PLANTED_DROP_DATE``: almost no orders (simulated outage -> sales drop).
* ``PLANTED_DELAY_SELLER``: one seller with severe delivery delays in Q2 2018.
* One order with 25 items of the same product (unusual order quantity).
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import OLIST_FILES, get_settings

PLANTED_SPIKE_DATE = "2018-05-15"
PLANTED_SPIKE_CATEGORY = "informatica_acessorios"
PLANTED_DROP_DATE = "2018-03-20"
PLANTED_UNUSUAL_ORDER_ITEMS = 25

CATEGORIES = [
    ("cama_mesa_banho", "bed_bath_table", 1.6, 90),
    ("beleza_saude", "health_beauty", 1.5, 130),
    ("esporte_lazer", "sports_leisure", 1.3, 110),
    ("moveis_decoracao", "furniture_decor", 1.3, 95),
    ("informatica_acessorios", "computers_accessories", 1.2, 120),
    ("utilidades_domesticas", "housewares", 1.1, 80),
    ("relogios_presentes", "watches_gifts", 1.0, 200),
    ("telefonia", "telephony", 0.9, 70),
    ("ferramentas_jardim", "garden_tools", 0.8, 100),
    ("automotivo", "auto", 0.8, 140),
    ("brinquedos", "toys", 0.8, 115),
    ("cool_stuff", "cool_stuff", 0.6, 165),
    ("perfumaria", "perfumery", 0.6, 120),
    ("bebes", "baby", 0.5, 130),
    ("eletronicos", "electronics", 0.5, 60),
    ("papelaria", "stationery", 0.4, 90),
    ("fashion_bolsas_e_acessorios", "fashion_bags_accessories", 0.4, 75),
    ("pet_shop", "pet_shop", 0.4, 110),
    ("pc_gamer", None, 0.1, 170),                 # no English translation (as in Olist)
    ("portateis_cozinha_e_preparadores_de_alimentos", None, 0.1, 260),
]

STATES = {
    "SP": (0.42, -23.55, -46.63, 8), "RJ": (0.13, -22.90, -43.20, 12),
    "MG": (0.12, -19.92, -43.94, 12), "RS": (0.055, -30.03, -51.23, 15),
    "PR": (0.05, -25.43, -49.27, 12), "SC": (0.036, -27.59, -48.55, 14),
    "BA": (0.034, -12.97, -38.50, 19), "DF": (0.021, -15.79, -47.88, 13),
    "GO": (0.02, -16.68, -49.25, 15), "ES": (0.02, -20.32, -40.34, 15),
    "PE": (0.017, -8.05, -34.88, 18), "CE": (0.013, -3.73, -38.52, 21),
    "PA": (0.01, -1.46, -48.49, 24), "AM": (0.004, -3.12, -60.02, 26),
}
CITIES = {
    "SP": "sao paulo", "RJ": "rio de janeiro", "MG": "belo horizonte", "RS": "porto alegre",
    "PR": "curitiba", "SC": "florianopolis", "BA": "salvador", "DF": "brasilia",
    "GO": "goiania", "ES": "vitoria", "PE": "recife", "CE": "fortaleza",
    "PA": "belem", "AM": "manaus",
}


def _hex_id(rng: np.random.Generator, n: int, salt: str) -> list[str]:
    raw = rng.integers(0, 2**62, size=n)
    return [hashlib.md5(f"{salt}-{v}".encode()).hexdigest() for v in raw]


def generate_sample_dataset(
    output_dir: Path | str | None = None,
    n_orders: int = 30000,
    n_products: int = 600,
    n_sellers: int = 120,
    start: str = "2017-01-01",
    end: str = "2018-08-31",
    seed: int = 7,
) -> dict[str, pd.DataFrame]:
    """Create Olist-schema CSVs and return them as DataFrames."""
    rng = np.random.default_rng(seed)
    output_dir = Path(output_dir) if output_dir else get_settings().paths.sample_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    state_codes = list(STATES)
    state_p = np.array([STATES[s][0] for s in state_codes])
    state_p = state_p / state_p.sum()

    # ---------------- category translation ----------------
    translation = pd.DataFrame(
        [(pt, en) for pt, en, _, _ in CATEGORIES if en is not None],
        columns=["product_category_name", "product_category_name_english"],
    )

    # ---------------- geolocation ----------------
    zip_rows = []
    zip_by_state: dict[str, list[int]] = {}
    for i, st in enumerate(state_codes):
        base = 1000 + i * 6000
        zips = list(range(base, base + 40))
        zip_by_state[st] = zips
        _, lat, lng, _ = STATES[st]
        for z in zips:
            for _ in range(int(rng.integers(2, 5))):   # several points per prefix (as in Olist)
                zip_rows.append((z, lat + rng.normal(0, 0.15), lng + rng.normal(0, 0.15), CITIES[st], st))
    geolocation = pd.DataFrame(
        zip_rows,
        columns=["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng",
                 "geolocation_city", "geolocation_state"],
    )
    geolocation = pd.concat([geolocation, geolocation.head(25)], ignore_index=True)  # exact duplicates

    # ---------------- products ----------------
    cat_names = [c[0] for c in CATEGORIES]
    cat_w = np.array([c[2] for c in CATEGORIES])
    cat_w = cat_w / cat_w.sum()
    cat_price = {c[0]: c[3] for c in CATEGORIES}
    product_ids = _hex_id(rng, n_products, "product")
    prod_cat = rng.choice(cat_names, size=n_products, p=cat_w)
    products = pd.DataFrame({
        "product_id": product_ids,
        "product_category_name": prod_cat,
        "product_name_lenght": rng.integers(10, 70, n_products).astype(float),
        "product_description_lenght": rng.integers(50, 3000, n_products).astype(float),
        "product_photos_qty": rng.integers(1, 8, n_products).astype(float),
        "product_weight_g": rng.gamma(2.0, 900, n_products).round(),
        "product_length_cm": rng.integers(10, 100, n_products).astype(float),
        "product_height_cm": rng.integers(2, 60, n_products).astype(float),
        "product_width_cm": rng.integers(8, 80, n_products).astype(float),
    })
    missing_cat_idx = rng.choice(n_products, size=max(2, n_products // 60), replace=False)
    products.loc[missing_cat_idx, ["product_category_name", "product_name_lenght",
                                   "product_description_lenght", "product_photos_qty"]] = np.nan
    products.loc[products.index[-1], ["product_weight_g", "product_length_cm"]] = np.nan
    base_price = np.array([cat_price.get(c, 100) if isinstance(c, str) else 100 for c in products["product_category_name"]])
    products_price = np.round(base_price * rng.lognormal(0, 0.5, n_products), 2)
    popularity = 1.0 / np.arange(1, n_products + 1) ** 1.05   # Zipf-like popularity
    rng.shuffle(popularity)
    popularity = popularity / popularity.sum()

    # ---------------- sellers ----------------
    seller_ids = _hex_id(rng, n_sellers, "seller")
    seller_state = rng.choice(state_codes, size=n_sellers, p=np.array([0.7 if s == "SP" else 0.3 / (len(state_codes) - 1) for s in state_codes]))
    sellers = pd.DataFrame({
        "seller_id": seller_ids,
        "seller_zip_code_prefix": [int(rng.choice(zip_by_state[s])) for s in seller_state],
        "seller_city": [CITIES[s] for s in seller_state],
        "seller_state": seller_state,
    })
    delay_seller = seller_ids[0]
    product_seller = rng.choice(seller_ids, size=n_products)
    # make sure the planted delay seller sells several popular products
    top_products = np.argsort(-popularity)[:40]
    product_seller[top_products[::5]] = delay_seller

    # ---------------- order dates (trend + weekly seasonality + events) ----------------
    days = pd.date_range(start, end, freq="D")
    t = np.arange(len(days))
    trend = 0.35 + 0.65 * t / len(days)
    weekday_factor = np.array([1.15, 1.12, 1.08, 1.02, 0.95, 0.80, 0.88])[days.dayofweek]
    weights = trend * weekday_factor
    bf = days.get_indexer([pd.Timestamp("2017-11-24")])[0]
    if bf >= 0:
        weights[bf] *= 4.0
    drop = days.get_indexer([pd.Timestamp(PLANTED_DROP_DATE)])[0]
    if drop >= 0:
        weights[drop] *= 0.03
    weights[-4:] *= 0.05                         # truncated tail, mimics Olist's data cut-off
    weights = weights / weights.sum()
    order_day_idx = np.sort(rng.choice(len(days), size=n_orders, p=weights))
    purchase_ts = days[order_day_idx] + pd.to_timedelta(rng.integers(0, 86400, n_orders), unit="s")

    # ---------------- customers (customer_id is per order; unique_id repeats) ----------------
    n_unique = int(n_orders * 0.97)
    unique_ids = np.array(_hex_id(rng, n_unique, "cust-unique"))
    cust_unique = np.concatenate([unique_ids, rng.choice(unique_ids, n_orders - n_unique)])
    rng.shuffle(cust_unique)
    customer_ids = _hex_id(rng, n_orders, "customer")
    cust_state = rng.choice(state_codes, size=n_orders, p=state_p)
    customers = pd.DataFrame({
        "customer_id": customer_ids,
        "customer_unique_id": cust_unique,
        "customer_zip_code_prefix": [int(rng.choice(zip_by_state[s])) for s in cust_state],
        "customer_city": [CITIES[s] for s in cust_state],
        "customer_state": cust_state,
    })

    # ---------------- orders ----------------
    order_ids = _hex_id(rng, n_orders, "order")
    status = rng.choice(
        ["delivered", "shipped", "canceled", "unavailable", "invoiced", "processing", "approved", "created"],
        size=n_orders, p=[0.968, 0.011, 0.007, 0.006, 0.003, 0.003, 0.001, 0.001],
    )
    approved = purchase_ts + pd.to_timedelta(rng.gamma(1.2, 6, n_orders), unit="h")
    carrier = approved + pd.to_timedelta(rng.gamma(2.0, 1.4, n_orders), unit="D")
    base_transit = np.array([STATES[s][3] for s in cust_state])
    transit = rng.gamma(4.0, base_transit / 4.0)
    delivered = carrier + pd.to_timedelta(transit, unit="D")
    estimated = (purchase_ts + pd.to_timedelta(base_transit + rng.integers(8, 16, n_orders), unit="D")).normalize()

    orders = pd.DataFrame({
        "order_id": order_ids,
        "customer_id": customer_ids,
        "order_status": status,
        "order_purchase_timestamp": purchase_ts,
        "order_approved_at": approved,
        "order_delivered_carrier_date": carrier,
        "order_delivered_customer_date": delivered,
        "order_estimated_delivery_date": estimated,
    })
    not_shipped = orders["order_status"].isin(["canceled", "unavailable", "invoiced", "processing", "approved", "created"])
    orders.loc[not_shipped, ["order_delivered_carrier_date", "order_delivered_customer_date"]] = pd.NaT
    orders.loc[orders["order_status"] == "shipped", "order_delivered_customer_date"] = pd.NaT
    orders.loc[orders["order_status"] == "created", "order_approved_at"] = pd.NaT
    delivered_idx = orders.index[orders["order_status"] == "delivered"]
    orders.loc[rng.choice(delivered_idx, 5, replace=False), "order_delivered_customer_date"] = pd.NaT  # missing
    bad_ts = rng.choice(delivered_idx, 3, replace=False)
    orders.loc[bad_ts, "order_delivered_customer_date"] = orders.loc[bad_ts, "order_purchase_timestamp"] - pd.Timedelta(days=2)

    # ---------------- order items ----------------
    n_items = rng.choice([1, 2, 3, 4], size=n_orders, p=[0.88, 0.09, 0.025, 0.005])
    spike_ts = pd.Timestamp(PLANTED_SPIKE_DATE)
    spike_products = np.where(products["product_category_name"].values == PLANTED_SPIKE_CATEGORY)[0]
    rows = []
    for i in range(n_orders):
        pidx = rng.choice(n_products, p=popularity)
        if len(spike_products) and purchase_ts[i].normalize() == spike_ts:
            pidx = rng.choice(spike_products)
        k = int(n_items[i])
        distinct = rng.random() < 0.3
        for item in range(1, k + 1):
            p = rng.choice(n_products, p=popularity) if (distinct and item > 1) else pidx
            price = round(products_price[p] * rng.uniform(0.95, 1.05), 2)
            freight = round(max(5.0, rng.gamma(2.0, 9.0)), 2)
            rows.append((order_ids[i], item, product_ids[p], product_seller[p],
                         purchase_ts[i] + pd.Timedelta(days=int(rng.integers(2, 7))), price, freight))
    # unusual order quantity
    big_order = int(rng.choice(delivered_idx))
    big_p = int(np.argmax(popularity))
    for item in range(len([r for r in rows if r[0] == order_ids[big_order]]) + 1, PLANTED_UNUSUAL_ORDER_ITEMS + 1):
        rows.append((order_ids[big_order], item, product_ids[big_p], product_seller[big_p],
                     purchase_ts[big_order] + pd.Timedelta(days=3), round(products_price[big_p], 2), 8.5))
    order_items = pd.DataFrame(rows, columns=["order_id", "order_item_id", "product_id", "seller_id",
                                              "shipping_limit_date", "price", "freight_value"])
    order_items.loc[order_items.sample(2, random_state=seed).index, "price"] = 0.0  # invalid numeric value

    # extra demand spike: 8x orders of spike category products on spike date
    spike_day_orders = orders.index[orders["order_purchase_timestamp"].dt.normalize() == spike_ts]
    if len(spike_products) and len(spike_day_orders):
        extra = []
        for oi in spike_day_orders:
            for _ in range(7):
                p = int(rng.choice(spike_products))
                n_existing = len(extra) + 1
                extra.append((order_ids[oi], 100 + n_existing, product_ids[p], product_seller[p],
                              purchase_ts[oi] + pd.Timedelta(days=3), round(products_price[p], 2), 12.0))
        extra_df = pd.DataFrame(extra, columns=order_items.columns)
        extra_df["order_item_id"] = extra_df.groupby("order_id").cumcount() + 50
        order_items = pd.concat([order_items, extra_df], ignore_index=True)

    # planted seller delay: Q2 2018 orders of delay seller arrive very late
    first_seller = order_items.drop_duplicates("order_id").set_index("order_id")["seller_id"]
    orders["_seller"] = orders["order_id"].map(first_seller)
    mask = (
        (orders["_seller"] == delay_seller)
        & orders["order_purchase_timestamp"].between("2018-04-01", "2018-06-30")
        & orders["order_delivered_customer_date"].notna()
        & (orders["order_delivered_customer_date"] > orders["order_purchase_timestamp"])
    )
    orders.loc[mask, "order_delivered_customer_date"] += pd.to_timedelta(rng.integers(25, 45, mask.sum()), unit="D")
    orders = orders.drop(columns="_seller")

    # ---------------- payments ----------------
    totals = order_items.groupby("order_id")[["price", "freight_value"]].sum().sum(axis=1)
    pay_rows = []
    for oid in order_ids:
        total = round(float(totals.get(oid, 0.0)), 2)
        if total == 0:
            continue
        ptype = rng.choice(["credit_card", "boleto", "voucher", "debit_card"], p=[0.74, 0.19, 0.055, 0.015])
        inst = int(rng.integers(1, 11)) if ptype == "credit_card" else 1
        if rng.random() < 0.03:
            v = round(total * rng.uniform(0.1, 0.5), 2)
            pay_rows.append((oid, 1, "voucher", 1, v))
            pay_rows.append((oid, 2, ptype if ptype != "voucher" else "credit_card", inst, round(total - v, 2)))
        else:
            pay_rows.append((oid, 1, ptype, inst, total))
    payments = pd.DataFrame(pay_rows, columns=["order_id", "payment_sequential", "payment_type",
                                               "payment_installments", "payment_value"])

    # ---------------- reviews (score depends on delivery delay) ----------------
    rev_rows = []
    late = (orders["order_delivered_customer_date"] > orders["order_estimated_delivery_date"]).values
    for i, oid in enumerate(order_ids):
        if rng.random() < 0.01:
            continue  # no review
        if late[i]:
            score = int(rng.choice([1, 2, 3, 4, 5], p=[0.45, 0.12, 0.15, 0.13, 0.15]))
        elif orders.at[i, "order_status"] in ("canceled", "unavailable"):
            score = int(rng.choice([1, 2, 3], p=[0.7, 0.2, 0.1]))
        else:
            score = int(rng.choice([1, 2, 3, 4, 5], p=[0.06, 0.03, 0.08, 0.20, 0.63]))
        ref = orders.at[i, "order_delivered_customer_date"]
        ref = orders.at[i, "order_estimated_delivery_date"] if pd.isna(ref) else ref
        created = pd.Timestamp(ref).normalize() + pd.Timedelta(days=1)
        msg = None if rng.random() < 0.6 else ("produto atrasou" if score <= 2 else "recomendo")
        n_rev = 2 if rng.random() < 0.01 else 1
        for r in range(n_rev):
            rev_rows.append((hashlib.md5(f"review-{oid}-{r}".encode()).hexdigest(), oid, score if r == 0 else max(1, score - 1),
                             None, msg, created + pd.Timedelta(days=r),
                             created + pd.Timedelta(days=r, hours=int(rng.integers(5, 72)))))
    reviews = pd.DataFrame(rev_rows, columns=["review_id", "order_id", "review_score", "review_comment_title",
                                              "review_comment_message", "review_creation_date",
                                              "review_answer_timestamp"])

    frames = {
        "orders": orders, "order_items": order_items, "customers": customers, "products": products,
        "sellers": sellers, "payments": payments, "reviews": reviews, "geolocation": geolocation,
        "category_translation": translation,
    }
    for key, df in frames.items():
        df.to_csv(output_dir / OLIST_FILES[key], index=False, date_format="%Y-%m-%d %H:%M:%S")
    (output_dir / "SAMPLE_DATA_README.txt").write_text(
        "SYNTHETIC SAMPLE DATA - NOT OLIST DATA.\n"
        "Generated by src/ingestion/sample_data.py for tests and offline demos.\n"
        f"seed={seed}, n_orders={n_orders}, planted spike={PLANTED_SPIKE_DATE} ({PLANTED_SPIKE_CATEGORY}), "
        f"planted sales drop={PLANTED_DROP_DATE}, planted delay seller={delay_seller}\n",
        encoding="utf-8",
    )
    return frames


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic Olist-schema sample data.")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--n-orders", type=int, default=30000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    frames = generate_sample_dataset(args.output_dir, n_orders=args.n_orders, seed=args.seed)
    for k, v in frames.items():
        print(f"{k:22s} {len(v):>8,d} rows")


if __name__ == "__main__":
    main()
