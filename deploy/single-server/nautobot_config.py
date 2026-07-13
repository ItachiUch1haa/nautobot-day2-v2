"""
Single-server test config for nautobot_day2 — NOT for production.

Mounted into the official Nautobot image at /opt/nautobot/nautobot_config.py.
Reads connection info from environment variables (set in docker-compose.yml)
rather than hardcoding hostnames, so the same file works whether Postgres/
Redis are containers on this box or, later, on separate machines.
"""

import os

from nautobot.core.settings import *  # noqa: F401,F403
from nautobot.core.settings_funcs import is_truthy

SECRET_KEY = os.environ.get("NAUTOBOT_SECRET_KEY")

ALLOWED_HOSTS = os.environ.get("NAUTOBOT_ALLOWED_HOSTS", "*").split(",")

DATABASES = {
    "default": {
        "NAME": os.environ.get("NAUTOBOT_DB_NAME", "nautobot"),
        "USER": os.environ.get("NAUTOBOT_DB_USER", "nautobot"),
        "PASSWORD": os.environ.get("NAUTOBOT_DB_PASSWORD", ""),
        "HOST": os.environ.get("NAUTOBOT_DB_HOST", "localhost"),
        "PORT": os.environ.get("NAUTOBOT_DB_PORT", "5432"),
        "ENGINE": "django.db.backends.postgresql",
    }
}

REDIS_HOST = os.environ.get("NAUTOBOT_REDIS_HOST", "localhost")
REDIS_PORT = os.environ.get("NAUTOBOT_REDIS_PORT", "6379")
REDIS_PASSWORD = os.environ.get("NAUTOBOT_REDIS_PASSWORD", "")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/1",
    }
}

CELERY_BROKER_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0"
CELERY_RESULT_BACKEND = CELERY_BROKER_URL

# ── nautobot_day2 — the App built this session ───────────────────────────────
PLUGINS = ["nautobot_day2"]
PLUGINS_CONFIG = {
    "nautobot_day2": {
        "tenants_dir": "/opt/nautobot/nautobot_day2_tenants",
        "max_concurrent_per_site": 5,
    }
}
