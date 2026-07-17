"""Nautobot Day 2 Operations — customer onboarding and network sync automation."""

__version__ = "0.1.0"

from nautobot.apps import NautobotAppConfig


class NautobotDay2Config(NautobotAppConfig):
    name = "nautobot_day2"
    verbose_name = "Nautobot Day 2 Operations"
    description = (
        "Customer/site onboarding pipeline and day-2 network data sync "
        "(SSH + vendor cloud APIs) for multi-vendor networks."
    )
    version = __version__
    author = "Airowire"
    required_settings = []
    default_settings = {
        # Base directory for tenant credential .env files, overridable per
        # deployment (e.g. a shared/mounted path when running multiple workers).
        "tenants_dir": "/opt/nautobot/nautobot_day2_tenants",
        # Max concurrent device-sync tasks per site, regardless of how many
        # Celery workers are running — protects small switch stacks/WAN
        # links from being hit by the whole worker pool at once.
        "max_concurrent_per_site": 5,
    }
    caching_config = {}


config = NautobotDay2Config
