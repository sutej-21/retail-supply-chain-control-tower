#!/bin/bash
# Creates the project database plus the Airflow and Superset metadata databases.
set -euo pipefail
for db in "${PROJECT_DB:-retail_supply_chain}" airflow superset; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
    SELECT 'CREATE DATABASE "$db"' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')\gexec
SQL
done
echo "Databases ready"
