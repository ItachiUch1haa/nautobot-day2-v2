"""
Celery tasks for fanned-out, per-device onboarding/sync work.

Before this, nautobot_day2.jobs.sync_network_data_job looped over every
device in a site sequentially inside a single Job run — one Celery task
occupying one worker slot for as long as the whole site took, with no
retry on transient SSH/API failures. sync_device_task is the fan-out unit:
the dispatcher enqueues one of these per device, and however many Celery
worker processes are running pick them up and run them in parallel,
capped per-site by nautobot_day2.concurrency.site_slot so a bigger worker
pool doesn't turn into more concurrent SSH sessions against one small
customer switch stack than it can take.
"""

import importlib.util
import os
import sys

from celery.utils.log import get_task_logger
from dotenv import load_dotenv
from nautobot.core.celery import nautobot_task

from .concurrency import SiteAtCapacity, site_slot

logger = get_task_logger(__name__)

PACKAGE_ROOT   = os.path.dirname(os.path.abspath(__file__))
ONBOARDING_DIR = os.path.join(PACKAGE_ROOT, "onboarding")

# Message prefixes sync_network_data.py's SSH layer raises on transient,
# worth-a-retry failures (see _ssh_connect in onboarding/sync_network_data.py).
# Anything else (e.g. AUTH_FAILURE) is treated as terminal — retrying a bad
# password doesn't fix it, and can lock the account out.
_RETRYABLE_PREFIXES = ("TIMEOUT:", "SSH_ERROR:")


def _load_sync_engine():
    """Load sync_network_data.py the same way the dispatcher Job does."""
    if ONBOARDING_DIR not in sys.path:
        sys.path.insert(0, ONBOARDING_DIR)
    spec = importlib.util.spec_from_file_location(
        "sync_network_data", os.path.join(ONBOARDING_DIR, "sync_network_data.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_tenant_env(tenant_slug):
    """
    Read this tenant's credential .env file fresh from disk and load it
    into the current process's environment, overriding any stale values
    already there. Called right before every device sync so an admin
    editing credentials via the onboarding app takes effect on the very
    next sync run -- no worker restart needed.
    """
    from django.conf import settings

    app_settings = settings.PLUGINS_CONFIG.get("nautobot_day2", {})
    tenants_dir = app_settings.get("tenants_dir")
    if not tenants_dir:
        logger.warning("PLUGINS_CONFIG['nautobot_day2']['tenants_dir'] not set — skipping env reload")
        return

    env_path = os.path.join(tenants_dir, f"{tenant_slug}.env")
    if not os.path.isfile(env_path):
        logger.warning("Tenant env file not found for '%s' at %s", tenant_slug, env_path)
        return

    load_dotenv(env_path, override=True)


@nautobot_task(
    bind=True,
    queue="nautobot_day2_sync",
    max_retries=3,
)
def sync_device_task(self, device, tenant_slug, site_key, dry_run=False, max_concurrent_per_site=None):
    """
    Sync exactly one device. Returns a small result dict rather than
    raising, except when retrying — the dispatcher's chord callback
    (sync_summary_callback) aggregates these into one summary log entry.
    """
    from django.conf import settings

    app_settings = settings.PLUGINS_CONFIG.get("nautobot_day2", {})
    max_concurrent = max_concurrent_per_site or app_settings.get("max_concurrent_per_site", 5)
    device_name = device.get("name", "?")

    try:
        with site_slot(site_key, max_concurrent):
            _load_tenant_env(tenant_slug)
            sync = _load_sync_engine()
            result = sync.sync_device(device, dry_run)
    except SiteAtCapacity as exc:
        logger.info("Site '%s' at capacity — requeueing %s", site_key, device_name)
        # Unrelated to this device — give it its own, much larger retry
        # budget so a busy site doesn't burn the device's real retry count.
        raise self.retry(exc=exc, countdown=15, max_retries=20)
    except Exception as exc:
        msg = str(exc)
        if msg.startswith(_RETRYABLE_PREFIXES) and self.request.retries < self.max_retries:
            backoff = 15 * (2 ** self.request.retries)
            logger.warning("Retrying %s in %ss after: %s", device_name, backoff, msg)
            raise self.retry(exc=exc, countdown=backoff)
        logger.warning("Device %s sync failed (terminal): %s", device_name, msg)
        return {"device": device_name, "status": "failed", "writes": {}, "error": msg[:200]}

    return {
        "device": device_name,
        "status": result.status,
        "writes": getattr(result, "writes", {}) or {},
        "error": getattr(result, "error_msg", None),
    }


@nautobot_task(queue="nautobot_day2_sync")
def sync_summary_callback(results, job_result_id, site_name, tenant_slug):
    """
    Celery chord callback — runs once every sync_device_task in the group
    has finished. Appends one summary JobLogEntry to the dispatching Job's
    JobResult, since the Job's own run() already returned right after
    dispatching (it doesn't block waiting for hundreds of device tasks).
    """
    from nautobot.extras.choices import LogLevelChoices
    from nautobot.extras.models import JobResult

    results = [r for r in results if r]
    ok = sum(1 for r in results if r.get("status") == "success")
    failed = sum(1 for r in results if r.get("status") == "failed")
    total_interfaces = sum(r.get("writes", {}).get("interfaces", 0) for r in results)
    total_cables = sum(r.get("writes", {}).get("cables", 0) for r in results)

    message = (
        f"Sync complete — site:{site_name} tenant:{tenant_slug} — "
        f"✅ {ok} ❌ {failed} of {len(results)} devices | "
        f"interfaces:{total_interfaces} cables:{total_cables}"
    )
    if failed:
        failed_names = ", ".join(r["device"] for r in results if r.get("status") == "failed")
        message += f" | failed: {failed_names}"

    try:
        job_result = JobResult.objects.get(pk=job_result_id)
        job_result.job_log_entries.create(
            log_level=LogLevelChoices.LOG_WARNING if failed else LogLevelChoices.LOG_INFO,
            grouping="sync_summary",
            message=message,
        )
    except JobResult.DoesNotExist:
        logger.warning("JobResult %s no longer exists — summary was: %s", job_result_id, message)
