"""
Aruba Central controller adapter (architecture doc §5).

DEVIATION FROM THE ARCHITECTURE DOC, following the master doc's own
methodology (verify against the real, already-validated integration
rather than trust a spec doc): §5's table lists `client_id, client_secret,
customer_id` with a plain OAuth2 client-credentials exchange. The
existing, already-validated Aruba Central integration
(onboarding/sync_network_data.py::_aruba_central_get_token(), and
vendor_matrix.py's aruba-central env_vars list) uses a REFRESH-TOKEN
grant instead — `client_id`, `client_secret`, `refresh_token`, plus a
region base_url — with no customer_id field at all. This adapter follows
the real, validated pattern (ARUBA_CLIENT_ID / ARUBA_CLIENT_SECRET /
ARUBA_REFRESH_TOKEN / ARUBA_CENTRAL_BASE_URL naming, matching
vendor_matrix.py) so discovered/onboarded devices authenticate the exact
same way the existing sync engine already does for this vendor.
"""
import time

import requests

from controllers.base import APControllerClient, ControllerAuthError


class ArubaCentralClient(APControllerClient):
    """AP discovery against the Aruba Central API."""

    def __init__(self, base_url, client_id, client_secret, refresh_token):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.session = requests.Session()
        self._access_token = None
        self._expires_at = 0

    def required_credential_fields(self):
        """aruba_central collects base_url, client_id, client_secret, refresh_token (refresh-token grant, matching the existing sync engine's already-validated flow)."""
        return ["base_url", "client_id", "client_secret", "refresh_token"]

    def _get_token(self):
        if self._access_token and self._expires_at > time.time() + 60:
            return self._access_token
        try:
            resp = requests.post(
                f"{self.base_url}/oauth2/token",
                params={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as e:
            raise ControllerAuthError(f"Aruba Central token exchange failed: {e}")

        data = resp.json()
        self._access_token = data.get("access_token")
        self._expires_at = time.time() + data.get("expires_in", 1800)
        return self._access_token

    def test_connection(self):
        """Confirm the refresh token exchanges for a valid access token."""
        self._get_token()
        return True

    def discover_aps(self, filters):
        """GET /monitoring/v2/aps, optionally scoped to a site/group filter, mapped to the common candidate shape."""
        filters = filters or {}
        token = self._get_token()
        params = {}
        if filters.get("site_id"):
            params["site"] = filters["site_id"]

        try:
            resp = self.session.get(
                f"{self.base_url}/monitoring/v2/aps",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            raise ControllerAuthError(f"Aruba Central discover_aps failed: {e}")

        candidates = []
        for device in resp.json().get("aps", []):
            name = device.get("name") or device.get("macaddr", "unknown")
            if filters.get("name_contains") and filters["name_contains"].lower() not in name.lower():
                continue
            candidates.append({
                "name": name,
                "model": device.get("model"),
                "mac": device.get("macaddr"),
                "current_ip": device.get("ip_address"),
                "site_label": device.get("site"),
                "raw": device,
            })
        return candidates
