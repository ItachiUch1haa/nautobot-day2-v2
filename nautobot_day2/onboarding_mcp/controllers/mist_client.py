"""
Mist controller adapter (architecture doc §5). Bearer-token auth, region-
specific base_url (api.mist.com, api.eu.mist.com, etc.) -- mirrors the
same token-header pattern already used for Mist in
jobs/mist_sync.py::MistSyncJob (Authorization: Token <api_token>), so this
adapter and the existing Mist sync job authenticate the same way.

PENDING LIVE VERIFICATION: built against Mist's documented REST API
shape (/api/v1/sites/{site_id}/devices?type=ap) but not yet run against a
real Mist org/site.
"""
import requests

from controllers.base import APControllerClient, ControllerAuthError


class MistClient(APControllerClient):
    """AP discovery against the Juniper Mist API."""

    def __init__(self, base_url, api_token, org_id, site_id):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.org_id = org_id
        self.site_id = site_id
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {self.api_token}",
            "Content-Type": "application/json",
        })

    def required_credential_fields(self):
        """mist collects base_url, api_token, org_id, site_id (architecture doc §5)."""
        return ["base_url", "api_token", "org_id", "site_id"]

    def test_connection(self):
        """Confirm the token authenticates by fetching the site's own record."""
        try:
            resp = self.session.get(f"{self.base_url}/api/v1/sites/{self.site_id}", timeout=15)
            resp.raise_for_status()
            return True
        except Exception as e:
            raise ControllerAuthError(f"Mist test_connection failed for site {self.site_id}: {e}")

    def discover_aps(self, filters):
        """GET /api/v1/sites/{site_id}/devices?type=ap, mapped to the common candidate shape."""
        filters = filters or {}
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v1/sites/{self.site_id}/devices",
                params={"type": "ap"},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            raise ControllerAuthError(f"Mist discover_aps failed for site {self.site_id}: {e}")

        candidates = []
        for device in resp.json():
            name = device.get("name") or device.get("mac", "unknown")
            if filters.get("name_contains") and filters["name_contains"].lower() not in name.lower():
                continue
            candidates.append({
                "name": name,
                "model": device.get("model"),
                "mac": device.get("mac"),
                "current_ip": device.get("ip"),
                "site_label": self.site_id,
                "raw": device,
            })
        return candidates
