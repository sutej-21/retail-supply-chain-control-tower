# Superset 4.1.2 + PostgreSQL driver (the official image does not ship psycopg2).
FROM apache/superset:4.1.2
USER root
RUN pip install --no-cache-dir psycopg2-binary==2.9.10 requests==2.32.3
USER superset
