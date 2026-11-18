#!/usr/bin/env bash
# Create the Superset database connection, datasets and the 5 dashboards (Docker stack).
# Run after `docker compose up -d` and at least one successful pipeline run.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec airflow-webserver bash -c \
  'python superset/bootstrap_dashboards.py --url http://superset:8088 \
     --username "$SUPERSET_ADMIN_USER" --password "$SUPERSET_ADMIN_PASSWORD" \
     --db-uri "postgresql+psycopg2://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB"'
