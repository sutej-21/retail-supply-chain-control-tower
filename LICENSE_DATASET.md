# Dataset Attribution and License

This project analyses the **Brazilian E-Commerce Public Dataset by Olist**:
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce

* **Publisher:** Olist (olistbr) on Kaggle
* **License:** Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)
  https://creativecommons.org/licenses/by-nc-sa/4.0/

Conditions that follow from the license:
1. **Attribution:** credit Olist and link to the dataset (as above) wherever results are shown, including
   dashboards, reports and the README.
2. **NonCommercial:** the data and derived works may not be used for commercial purposes.
3. **ShareAlike:** if you redistribute adapted data, use the same license.

The raw CSV files are **not** included in this repository (see `.gitignore`). Every user downloads them from Kaggle
and accepts the license terms there.

## Simulated and synthetic data
* `src/simulation/*` generates **simulated** inventory, warehouse, supplier and purchase-order data. It is not
  Olist data and does not describe real companies. Supplier names are fictional ("Simulated Supplier 001 Ltda").
* `src/ingestion/sample_data.py` generates a **synthetic sample** in the Olist file format for tests and offline
  demos. It contains no Olist records.

The project source code is provided for educational and portfolio purposes. The dataset license above governs the
Olist data and any derived data.
