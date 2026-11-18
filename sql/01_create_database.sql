-- =====================================================================
-- 01_create_database.sql
-- Creates the project database. Run with psql against the 'postgres'
-- maintenance database:   psql -h localhost -U postgres -f sql/01_create_database.sql
-- (Docker Compose does this automatically via docker/postgres-init.)
-- The Python pipeline also creates the database if missing
-- (config.database.ensure_database_exists).
-- =====================================================================
SELECT 'CREATE DATABASE retail_supply_chain'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'retail_supply_chain')\gexec

COMMENT ON DATABASE retail_supply_chain IS
  'Retail & Supply Chain Control Tower. raw/staging = Olist historical data (CC BY-NC-SA 4.0); analytics = star schema incl. clearly flagged SIMULATED operational data.';
