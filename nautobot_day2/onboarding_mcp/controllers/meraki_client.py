"""
Meraki controller adapter (architecture doc §5). Uses `requests`,
matching the existing convention elsewhere in this repo (base dependency,
not a new httpx client) -- see openbao_client.py / client.py for the same
choice.

PENDING LIVE VERIFICATION: built against Meraki Dashboard API's
documented shape (api_key header auth, /organizations/{org_id}/devices)
but not yet run against a real Meraki org.
"""
import requests

from controllers.base import APControllerClient, ControllerAuthError

_BASE_URL = "https://api.meraki.com/api/v1"


class MerakiClient(APControllerClient):
    """AP discovery against the Meraki Dashboard API."""

    def __init__(self, api_key, org_id, network_id=None):
        self.api_key = api_key
        self.org_id = org_id
        self.network_id = network_id
        self.session = requests.Session()
        self.session.headers.update({
            "X-Cisco-Meraki-API-Key": self.api_key,
            "Content-Type": "application/json",
        })

    def required_credential_fields(self):
        """meraki collects api_key, org_id, network_id (architecture doc §5)."""
        return ["api_key", "org_id", "network_id"]

    def test_connection(self):
        """Confirm the API key authenticates by fetching the organization's own record."""
        try:
            resp = self.session.get(f"{_BASE_URL}/organizations/{self.org_id}", timeout=15)
            resp.raise_for_status()
            return True
        except Exception as e:
            raise ControllerAuthError(f"Meraki test_connection failed for org {self.org_id}: {e}")

    def discover_aps(self, filters):
        """GET /organizations/{org_id}/devices filtered to productType=wireless, mapped to the common candidate shape."""
        filters = filters or {}
        network_id = filters.get("network_id", self.network_id)
        params = {"productTypes[]": "wireless"}
        if network_id:
            params["networkIds[]"] = network_id

        try:
            resp = self.session.get(
                f"{_BASE_URL}/organizations/{self.org_id}/devices", params=params, timeout=30
            )
            resp.raise_for_status()
        except Exception as e:
            raise ControllerAuthError(f"Meraki discover_aps failed for org {self.org_id}: {e}")

        candidates = []
        for device in resp.json():
            name = device.get("name") or device.get("mac", "unknown")
            if filters.get("name_contains") and filters["name_contains"].lower() not in name.lower():
                continue
            candidates.append({
                "name": name,
                "model": device.get("model"),
                "mac": device.get("mac"),
                "current_ip": device.get("lanIp"),
                "site_label": device.get("networkId"),
                "raw": device,
            })
        return candidates
