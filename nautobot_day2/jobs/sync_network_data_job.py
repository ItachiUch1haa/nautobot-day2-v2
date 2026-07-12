"""
sync_network_data_job.py
NOC Network Data Sync Job — SSH/API sync for all vendors
Updates: serial, firmware, interfaces, LLDP cables, software_version
Runs per site/tenant/category — wraps sync_network_data.py
"""

import os
import sys
import importlib.util

from nautobot.extras.jobs import Job, StringVar, ChoiceVar, BooleanVar, ObjectVar
from nautobot.dcim.models import Device, Location
from nautobot.tenancy.models import Tenant

# Path to sync engine — resolved relative to this installed package, so it
# works the same whether run from a git checkout or a pip-installed App.
PACKAGE_ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONBOARDING_DIR = os.path.join(PACKAGE_ROOT, "onboarding")
SYNC_SCRIPT    = os.path.join(ONBOARDING_DIR, "sync_network_data.py")
LAB_DIR        = ONBOARDING_DIR

name = "NOC Network Sync"


def _load_tenant_env(tenant_slug):
    """Load tenant credentials into os.environ."""
    for path in [
        os.path.join(ONBOARDING_DIR, "profiles", f"{tenant_slug}.env"),
        f"/etc/nautobot/tenants/{tenant_slug}.env",
    ]:
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    os.environ[k.strip()] = v.strip()
        return path
    return None


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

    def run(self, data, commit):
        tenant_obj  = data["tenant"]
        site_obj    = data["site"]
        category    = data["category"]
        dry_run     = data["dry_run"]
        tenant_slug = tenant_obj.slug
        site_name   = site_obj.name

        self.log_info(
            obj=site_obj,
            message=f"Starting sync — site:{site_name} tenant:{tenant_slug} category:{category}"
        )

        # Load credentials
        env_path = _load_tenant_env(tenant_slug)
        if env_path:
            self.log_info(message=f"Credentials loaded from {env_path}")
        else:
            self.log_warning(
                message=f"No env file found for tenant '{tenant_slug}' — credentials may be missing"
            )

        # Load sync engine
        try:
            sync = _load_sync()
        except Exception as e:
            self.log_failure(message=f"Failed to load sync engine: {e}")
            return

        # Get devices
        try:
            devices = sync.get_devices_for_site(site_name, tenant_slug, category)
        except Exception as e:
            self.log_failure(message=f"Failed to get devices: {e}")
            return

        if not devices:
            self.log_warning(message=f"No devices found for site '{site_name}' tenant '{tenant_slug}'")
            return

        self.log_info(message=f"Found {len(devices)} devices to sync")

        # Sync each device
        ok = fail = 0
        total_interfaces = total_cables = 0

        for device in devices:
            dev_name = device.get('name', '?')
            try:
                # Get Nautobot Device object for logging
                try:
                    dev_obj = Device.objects.get(name=dev_name)
                except Device.DoesNotExist:
                    dev_obj = None

                result = sync.sync_device(device, dry_run)

                if result.status == 'success':
                    ok += 1
                    i = result.writes.get('interfaces', 0)
                    c = result.writes.get('cables', 0)
                    total_interfaces += i
                    total_cables     += c
                    self.log_success(
                        obj=dev_obj,
                        message=f"✅ {dev_name} — interfaces:{i} cables:{c} facts:{result.writes.get('facts',0)}"
                    )
                elif result.status == 'failed':
                    fail += 1
                    self.log_warning(
                        obj=dev_obj,
                        message=f"❌ {dev_name} — {result.error_msg[:120]}"
                    )
                else:
                    self.log_info(
                        obj=dev_obj,
                        message=f"⏭  {dev_name} — skipped ({result.status})"
                    )

            except Exception as e:
                fail += 1
                self.log_warning(message=f"❌ {dev_name} — exception: {str(e)[:120]}")

        # Summary
        self.log_info(
            message=(
                f"Sync complete — ✅ {ok} ❌ {fail} | "
                f"interfaces:{total_interfaces} cables:{total_cables}"
            )
        )

        if fail > 0:
            self.log_warning(
                message=f"{fail} device(s) failed — check credentials and reachability"
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

    def run(self, data, commit):
        tenant_obj  = data["tenant"]
        category    = data["category"]
        dry_run     = data["dry_run"]
        tenant_slug = tenant_obj.slug

        self.log_info(message=f"Syncing all sites for tenant: {tenant_obj.name}")

        # Load credentials once for this tenant
        env_path = _load_tenant_env(tenant_slug)
        if env_path:
            self.log_info(message=f"Credentials loaded from {env_path}")

        # Load sync engine
        try:
            sync = _load_sync()
        except Exception as e:
            self.log_failure(message=f"Failed to load sync engine: {e}")
            return

        # Find all sites that have devices for this tenant
        sites = Location.objects.filter(
            devices__tenant=tenant_obj
        ).distinct()

        self.log_info(message=f"Found {sites.count()} sites with devices")

        for site in sites:
            self.log_info(obj=site, message=f"Syncing site: {site.name}")
            try:
                devices = sync.get_devices_for_site(site.name, tenant_slug, category)
                self.log_info(message=f"  {site.name}: {len(devices)} devices")

                for device in devices:
                    dev_name = device.get('name','?')
                    try:
                        result = sync.sync_device(device, dry_run)
                        icon   = "✅" if result.status == "success" else "❌"
                        self.log_info(message=f"  {icon} {dev_name}")
                    except Exception as e:
                        self.log_warning(message=f"  ❌ {dev_name}: {str(e)[:80]}")

            except Exception as e:
                self.log_warning(message=f"Site {site.name} failed: {str(e)[:80]}")

        self.log_info(message=f"All sites sync complete for {tenant_obj.name}")
