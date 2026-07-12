"""
Per-site concurrency guard for fanned-out device tasks.

Nautobot's Celery worker pool can run many device-sync tasks in parallel
across the whole fleet once nautobot_day2.tasks.sync_device_task fans out
one task per device (see nautobot_day2/jobs/sync_network_data_job.py). But
a single customer site's switch stack or WAN link can't take unlimited
concurrent SSH sessions — more workers must not mean "more sessions hit
one small site at once." This caps how many device tasks run at once per
site, using Django's cache (Redis, in Nautobot's standard deployment) as a
distributed counter, since a per-process limit wouldn't stop two different
worker processes from both hammering the same site.
"""

from contextlib import contextmanager

from django.core.cache import cache

DEFAULT_MAX_CONCURRENT_PER_SITE = 5

# Safety valve: if a task crashes hard enough to skip the `finally` release
# (e.g. the worker process is killed), the slot still frees itself after this
# many seconds instead of leaking forever.
SLOT_TTL_SECONDS = 600


class SiteAtCapacity(Exception):
    """Raised when a site's concurrency slot can't be acquired right now."""


def _cache_key(site_key):
    return f"nautobot_day2:site_inflight:{site_key}"


@contextmanager
def site_slot(site_key, max_concurrent=None):
    """
    Acquire one of `max_concurrent` concurrency slots for `site_key`
    (e.g. "acme-retail:Acme-BLR-01"). Raises SiteAtCapacity if none are
    free — callers should retry later rather than block.
    """
    max_concurrent = max_concurrent or DEFAULT_MAX_CONCURRENT_PER_SITE
    key = _cache_key(site_key)

    cache.get_or_set(key, 0, timeout=SLOT_TTL_SECONDS)
    in_flight = cache.incr(key)

    if in_flight > max_concurrent:
        cache.decr(key)
        raise SiteAtCapacity(
            f"Site '{site_key}' already has {max_concurrent} device syncs in flight"
        )

    try:
        yield
    finally:
        try:
            cache.decr(key)
        except ValueError:
            pass  # slot's TTL already expired — nothing left to release
