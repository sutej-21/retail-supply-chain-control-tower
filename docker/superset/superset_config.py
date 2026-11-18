"""Superset configuration for the Control Tower stack (values come from environment variables)."""
import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:{os.environ.get('POSTGRES_PORT', '5432')}/superset"
)
FEATURE_FLAGS = {"DASHBOARD_NATIVE_FILTERS": True, "DASHBOARD_CROSS_FILTERS": True}
WTF_CSRF_ENABLED = True
TALISMAN_ENABLED = False          # local demo; enable behind HTTPS in production
ROW_LIMIT = 50000
SUPERSET_WEBSERVER_TIMEOUT = 120
