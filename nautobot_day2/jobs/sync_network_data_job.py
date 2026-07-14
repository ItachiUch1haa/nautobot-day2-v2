"""
sync_network_data_job.py
NOC Network Data Sync Job — SSH/API sync for all vendors
Updates: serial, firmware, interfaces, LLDP cables, software_version
Runs per site/tenant/category — wraps sync_network_data.py
"""

import os
import sys
import importlib.util

from celery import chord, group
from nautobot.extras.jobs import Job, StringVar, ChoiceVar, BooleanVar, ObjectVar
from nautobot.dcim.models import Location
from nautobot.tenancy.models import Tenant

from ..tasks import sync_device_task, sync_summary_callback, _load_tenant_env

# Path to sync engine — resolved relative to this installed package, so it
# works the same whether run from a git checkout or a pip-installed App.
PACKAGE_ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONBOARDING_DIR = os.path.join(PACKAGE_ROOT, "onboarding")
SYNC_SCRIPT    = os.path.join(ONBOARDING_DIR, "sync_network_data.py")
LAB_DIR        = ONBOARDING_DIR

name = "NOC Network Sync"


def _slugify(name):
    """Same slug logic as create_tenant.py -- must stay identical since the
    tenant slug used for env-file lookup and secrets-group naming is an
    app-level concept, not a native Nautobot Tenant field (Tenant has no
    .slug attribute in this Nautobot version)."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def _load_sync():
    """Dynamically load sync_network_data module."""
    if LAB_DIR not in sys.path:
        sys.path.insert(0, LAB_DIR)
    spec = importlib.util.spec_from_file_location("sync_network_data", SYNC_SCRIPT)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SyncNetworkData(Job):
    """
    Sync live network data from devices into Nautobot.
    Connects via SSH (switches/firewalls) or cloud API (APs/controllers).
    Updates serial, firmware, interfaces, LLDP cables per device.
    """

    tenant = ObjectVar(
        model=Tenant,
        label="Tenant (Customer)",
        description="Customer tenant to sync",
    )
    site = ObjectVar(
        model=Location,
        label="Site",
        description="Site to sync devices for",
    )
    category = ChoiceVar(
        choices=[
            ("all",       "All — switches + firewalls + APs"),
            ("switches",  "Switches only"),
            ("firewalls", "Firewalls only"),
            ("aps",       "Access Points only"),
        ],
        default="all",
        label="Device Category",
    )
    dry_run = BooleanVar(
        default=False,
        label="Dry Run",
        description="Fetch data but do not write to Nautobot",
    )

    class Meta:
        name             = "Sync Network Data"
        description      = "SSH/API sync — serial, firmware, interfaces, LLDP topology"
        commit_default   = True
        has_sensitive_variables = False
        soft_time_limit  = 900
        time_limit       = 1200

    def run(self, tenant, site, category, dry_run):
        tenant_obj  = tenant
        site_obj    = site
        tenant_slug = _slugify(tenant_obj.name)
        site_name   = site_obj.name

        self.logger.info(f"Starting sync — site:{site_name} tenant:{tenant_slug} category:{category}")

        # Load credentials
        env_path = _load_tenant_env(tenant_slug)
        if env_path:
            self.logger.info(f"Credentials loaded from {env_path}")
        else:
            self.logger.warning(f"No env file found for tenant '{tenant_slug}' — credentials may be missing")

        # Load sync engine
        try:
            sync = _load_sync()
        except Exception as e:
            self.logger.error(f"Failed to load sync engine: {e}")
            return

        # Get devices
        try:
            devices = sync.get_devices_for_site(site_name, tenant_slug, category)
        except Exception as e:
            self.logger.error(f"Failed to get devices: {e}")
            return

        if not devices:
            self.logger.warning(f"No devices found for site '{site_name}' tenant '{tenant_slug}'")
            return

        self.logger.info(f"Found {len(devices)} devices to sync")

        # Fan out — one Celery task per device instead of looping here.
        # This Job's own run() dispatches and returns; a summary log entry
        # is appended to this same Job's log once every device task
        # finishes (nautobot_day2.tasks.sync_summary_callback). Per-site
        # concurrency is capped regardless of worker pool size (see
        # nautobot_day2.concurrency) so a bigger pool doesn't mean more
        # simultaneous SSH sessions against one small site than it can take.
        site_key = f"{tenant_slug}:{site_name}"
        header = group(
            sync_device_task.s(device, tenant_slug, site_key, dry_run)
            for device in devices
        )
        chord(header)(sync_summary_callback.s(
            job_result_id=str(self.job_result.pk),
            site_name=site_name,
            tenant_slug=tenant_slug,
        ))

        self.logger.info(
            f"Dispatched {len(devices)} device sync task(s) to the "
            f"'nautobot_day2_sync' queue — this Job reports 'dispatched', "
            f"not 'complete'. A summary log entry will be added to this "
            f"Job's log once every device task finishes."
        )


class SyncAllSites(Job):
    """
    Sync all sites for a tenant in one job run.
    Useful for scheduled nightly syncs.
    """

    tenant = ObjectVar(
        model=Tenant,
        label="Tenant (Customer)",
        description="Run sync for all sites belonging to this tenant",
    )
    category = ChoiceVar(
        choices=[
            ("all",       "All — switches + firewalls + APs"),
            ("switches",  "Switches only"),
            ("firewalls", "Firewalls only"),
            ("aps",       "Access Points only"),
        ],
        default="all",
    )
    dry_run = BooleanVar(default=False)

    class Meta:
        name             = "Sync All Sites for Tenant"
        description      = "Run network sync for all sites belonging to a tenant"
        commit_default   = True
        has_sensitive_variables = False
        soft_time_limit  = 3600
        time_limit       = 4800

    def run(self, tenant, category, dry_run):
        tenant_obj  = tenant
        tenant_slug = _slugify(tenant_obj.name)

        self.logger.info(f"Syncing all sites for tenant: {tenant_obj.name}")

        # Load credentials once for this tenant
        env_path = _load_tenant_env(tenant_slug)
        if env_path:
            self.logger.info(f"Credentials loaded from {env_path}")

        # Load sync engine
        try:
            sync = _load_sync()
        except Exception as e:
            self.logger.error(f"Failed to load sync engine: {e}")
            return

        # Find all sites that have devices for this tenant
        sites = Location.objects.filter(
            devices__tenant=tenant_obj
        ).distinct()

        self.logger.info(f"Found {sites.count()} sites with devices")

        # Gather every device across every site first, tagged with its own
        # site_key, then fan out ALL of them as a single dispatch. Per-site
        # concurrency caps (nautobot_day2.concurrency) apply per device
        # regardless of which site it came from, so one big multi-site
        # tenant sync still can't overrun any single site's device limit.
        all_devices = []
        for site in sites:
            try:
                devices = sync.get_devices_for_site(site.name, tenant_slug, category)
                self.logger.info(f"{site.name}: {len(devices)} devices")
                site_key = f"{tenant_slug}:{site.name}"
                all_devices.extend((device, site_key) for device in devices)
            except Exception as e:
                self.logger.warning(f"Site {site.name} failed to enumerate devices: {str(e)[:80]}")

        if not all_devices:
            self.logger.warning("No devices found across any site for this tenant")
            return

        header = group(
            sync_device_task.s(device, tenant_slug, site_key, dry_run)
            for device, site_key in all_devices
        )
        chord(header)(sync_summary_callback.s(
            job_result_id=str(self.job_result.pk),
            site_name=f"ALL SITES ({sites.count()})",
            tenant_slug=tenant_slug,
        ))

        self.logger.info(
            f"Dispatched {len(all_devices)} device sync task(s) across "
            f"{sites.count()} site(s) to the 'nautobot_day2_sync' queue — "
            f"a summary log entry will be added once every device task finishes."
        )

from nautobot.core.celery import register_jobs
register_jobs(SyncNetworkData, SyncAllSites)
