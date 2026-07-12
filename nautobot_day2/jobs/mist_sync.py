"""
Juniper Mist → Nautobot Sync Job
Syncs all devices from a Mist org into Nautobot
Populates: mist_site_id, mist_device_id, mist_api_url custom fields
Multi-tenant: one run per customer
"""
import requests
from nautobot.extras.jobs import Job, StringVar, ObjectVar, BooleanVar
from nautobot.extras.models import SecretsGroup, Status, Role
from nautobot.dcim.models import Device, Platform, Location, DeviceType, Manufacturer
from nautobot.tenancy.models import Tenant


name = "Juniper Mist SSoT"


class MistSyncJob(Job):
    """Sync all devices from Juniper Mist into Nautobot."""

    class Meta:
        name = "Juniper Mist: Sync Devices to Nautobot"
        description = "Pulls APs, Switches, SRX from Mist org. Multi-tenant: one run per customer."
        commit_default = False
        has_sensitive_variables = False

    api_url = StringVar(
        label="Mist API URL",
        description="e.g. https://api.eu.mist.com or https://api.mist.com",
        default="https://api.eu.mist.com",
    )
    secrets_group = ObjectVar(
        model=SecretsGroup,
        label="Secrets Group",
        description="Secrets Group containing Mist API token as password",
    )
    tenant = ObjectVar(
        model=Tenant,
        label="Tenant (Customer)",
        description="Nautobot tenant to assign all synced devices to",
        required=False,
    )
    default_location = ObjectVar(
        model=Location,
        label="Default Location",
        description="Fallback location if Mist site not found in Nautobot",
        required=False,
    )
    dry_run = BooleanVar(
        label="Dry Run",
        description="Show what would change without making changes",
        default=True,
    )

    def run(self, api_url, secrets_group, tenant, default_location, dry_run):
        """Main job execution."""

        # Step 1 — Get API token
        self.logger.info("Fetching API token from Secrets Group...")
        try:
            api_token = secrets_group.get_secret_value(
                access_type="Generic",
                secret_type="token",
            )
        except Exception as e:
            self.logger.error(f"Failed to get API token: {e}")
            return

        headers = {
            "Authorization": f"Token {api_token}",
            "Content-Type": "application/json",
        }

        # Step 2 — Connect to Mist
        self.logger.info(f"Connecting to Mist API: {api_url}")
        try:
            resp = requests.get(f"{api_url}/api/v1/self", headers=headers, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            self.logger.error(f"Failed to connect to Mist API: {e}")
            return

        self_data = resp.json()
        privileges = self_data.get("privileges", [])
        if not privileges:
            self.logger.error("No org privileges found for this token")
            return

        org_id = privileges[0].get("org_id")
        org_name = privileges[0].get("name", "Unknown Org")
        self.logger.info(f"Connected to Mist Org: {org_name} ({org_id})")

        # Step 3 — Get all sites
        try:
            sites_resp = requests.get(
                f"{api_url}/api/v1/orgs/{org_id}/sites",
                headers=headers, timeout=30
            )
            sites_resp.raise_for_status()
            sites = sites_resp.json()
        except Exception as e:
            self.logger.error(f"Failed to fetch sites: {e}")
            return

        self.logger.info(f"Found {len(sites)} Mist sites")

        # Step 4 — Get platform and status
        try:
            platform = Platform.objects.get(name="juniper-mist")
        except Platform.DoesNotExist:
            self.logger.error("Platform 'juniper-mist' not found. Create it first.")
            return

        active_status = Status.objects.get(name="Active")

        # Step 5 — Process all sites and devices
        total = created = updated = failed = 0

        for site in sites:
            site_id = site["id"]
            site_name = site["name"]
            self.logger.info(f"Processing site: {site_name}")

            try:
                site_location = Location.objects.get(name=site_name)
            except Location.DoesNotExist:
                if default_location:
                    site_location = default_location
                    self.logger.warning(f"Site '{site_name}' not in Nautobot, using default location")
                else:
                    self.logger.warning(f"Site '{site_name}' not found, no default set. Skipping.")
                    continue

            for device_type_str in ["ap", "switch", "gateway"]:
                try:
                    dev_resp = requests.get(
                        f"{api_url}/api/v1/sites/{site_id}/devices",
                        headers=headers,
                        params={"type": device_type_str},
                        timeout=30
                    )
                    dev_resp.raise_for_status()
                    devices = dev_resp.json()
                except Exception as e:
                    self.logger.error(f"Failed fetching {device_type_str} for {site_name}: {e}")
                    continue

                for mist_device in devices:
                    total += 1
                    result = self._sync_device(
                        mist_device, site_id, site_name, site_location,
                        api_url, platform, active_status, tenant,
                        device_type_str, dry_run
                    )
                    if result == "created":
                        created += 1
                    elif result == "updated":
                        updated += 1
                    elif result == "failed":
                        failed += 1

        self.logger.info("=" * 50)
        self.logger.info(f"Org: {org_name} | Total: {total} | Created: {created} | Updated: {updated} | Failed: {failed}")
        if dry_run:
            self.logger.info("DRY RUN — no changes made")
        self.logger.info("=" * 50)

    def _sync_device(self, mist_device, site_id, site_name, site_location,
                     api_url, platform, active_status, tenant,
                     device_type_str, dry_run):
        """Create or update a single device."""
        device_id = mist_device.get("id", "")
        name = mist_device.get("name") or mist_device.get("mac", "unknown")
        model = mist_device.get("model", "Unknown")
        serial = mist_device.get("serial", "")

        if dry_run:
            action = "UPDATE" if Device.objects.filter(name=name).exists() else "CREATE"
            self.logger.info(
                f"[DRY RUN] {action}: {name} | model={model} | "
                f"location={site_location.name} | mist_device_id={device_id}"
            )
            return "dry_run"

        try:
            manufacturer, _ = Manufacturer.objects.get_or_create(name="Juniper")
            device_type_obj, _ = DeviceType.objects.get_or_create(
                model=model,
                manufacturer=manufacturer,
                defaults={},
            )

            from django.contrib.contenttypes.models import ContentType
            from nautobot.dcim.models import Device as DeviceModel
            device_ct = ContentType.objects.get_for_model(DeviceModel)
            role, created = Role.objects.get_or_create(
                name=f"Juniper Mist {device_type_str.title()}",
                defaults={"color": "00bcd4"},
            )
            if device_ct not in role.content_types.all():
                role.content_types.add(device_ct)
            nb_device, created = Device.objects.get_or_create(
                name=name,
                defaults={
                    "status": active_status,
                    "platform": platform,
                    "location": site_location,
                    "device_type": device_type_obj,
                    "role": role,
                },
            )

            if not created:
                nb_device.platform = platform
                nb_device.location = site_location
                nb_device.device_type = device_type_obj
                nb_device.status = active_status

            nb_device.cf["mist_site_id"] = site_id
            nb_device.cf["mist_device_id"] = device_id
            nb_device.cf["mist_api_url"] = api_url

            if serial:
                nb_device.serial = serial
            if tenant:
                nb_device.tenant = tenant

            nb_device.validated_save()

            action = "Created" if created else "Updated"
            self.logger.info(f"✅ {action}: {name} | mist_device_id={device_id}")
            return "created" if created else "updated"

        except Exception as e:
            self.logger.error(f"❌ Failed to sync {name}: {e}")
            return "failed"

from nautobot.core.celery import register_jobs
register_jobs(MistSyncJob)
