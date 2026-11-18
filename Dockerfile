# Airflow image with the project's Python dependencies.
# Base: Airflow 2.10.5 on Python 3.11 (requirements.txt is pinned to be compatible with it).
FROM apache/airflow:2.10.5-python3.11

USER root
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
USER airflow

COPY requirements.txt /tmp/requirements.txt
# Install together with the pinned Airflow version so pip never up/downgrades Airflow itself.
RUN pip install --no-cache-dir "apache-airflow==2.10.5" -r /tmp/requirements.txt pytest==8.3.4

ENV PROJECT_ROOT=/opt/project \
    PYTHONPATH=/opt/project
WORKDIR /opt/project
