#!/usr/bin/env bash
# Offline demo WITHOUT Kaggle: synthetic Olist-schema sample -> full pipeline -> 7-day delay scenario.
# Requires a reachable PostgreSQL configured in .env.
set -euo pipefail
cd "$(dirname "$0")/.."
export DATA_SOURCE=sample
python -m src.ingestion.sample_data
python -m src.pipeline.run_pipeline
python -m src.optimization.scenario_analysis --name "All suppliers delayed 7 days" --delay 7
echo "Done. Reports in ./reports, data in PostgreSQL schema 'analytics'."
