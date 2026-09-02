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

LIVE-FOUND BUG, this adapter never carried over a load-bearing part of
that "already-validated" reference implementation: Aruba Central's
refresh-token grant *rotates* the refresh token on every single exchange
-- the response's own `refresh_token` field is a new value, and the one
just used becomes invalid immediately (sync_network_data.py's own
_aruba_central_get_token() docstring already calls this out: "each
exchange also risks rotating the refresh_token again"). That reference
implementation handles it by detecting the change and persisting the new
value (OpenBao + env file + os.environ) every time. This adapter
originally just discarded the response's refresh_token entirely, and
tools_schema.py rebuilds a brand-new ArubaCentralClient from scratch on
every set_ap_controller/scan_ap_controller call (no persistent in-process
instance across MCP tool calls), so the very first scan_ap_controller()
after a successful set_ap_controller() would already be handed a refresh
token Aruba had already invalidated during set_ap_controller()'s own
test_connection() exchange -- not just an issue after some TTL, but
reliably on the second call, every time. Reported symptom matched
exactly: "aruba central refresh token flow is not working for api
calls."

Fixed by mirroring the reference implementation: detect the rotation in
_get_token() and persist it to OpenBao immediately via
update_rotated_credential() (best-effort -- an OpenBao write failure
here shouldn't block the API call that already has a valid access
token). Session-state-level persistence (so the *next* MCP tool call in
this same session rebuilds the controller with the fresh token instead
of a now-invalid one from Redis-cached fields) is the caller's
responsibility -- see tools_schema.py's set_ap_controller/
scan_ap_controller, which read this instance's (possibly-updated)
`.refresh_token` attribute back out after each call.
"""
import time

import requests

from controllers.base import APControllerClient, ControllerAuthError
from openbao_client import update_rotated_credential


class ArubaCentralClient(APControllerClient):
    """AP discovery against the Aruba Central API."""

    def __init__(self, base_url, client_id, client_secret, refresh_token, tenant_slug=None):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        # Not one of required_credential_fields() -- callers set this
        # post-construction (tools_schema.py does, once the session's
        # tenant is known) so the rotation below can be persisted to the
        # right OpenBao path. None is a valid, if degraded, state: the
        # rotated token still updates self.refresh_token in memory for
        # the caller to read back, it just can't be written to OpenBao
        # without knowing which tenant's secret to update.
        self.tenant_slug = tenant_slug
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

        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != self.refresh_token:
            self.refresh_token = new_refresh
            if self.tenant_slug:
                try:
                    update_rotated_credential(
                        self.tenant_slug, "aruba_central-controller", {"refresh_token": new_refresh}
                    )
                except Exception:
                    # Non-fatal, matching sync_network_data.py's own
                    # _aruba_central_get_token() -- the access_token we
                    # just got is still valid and usable for this call;
                    # a failed OpenBao write here shouldn't block it.
                    # Whoever reads self.refresh_token back out after
                    # this call still gets the correct, current value.
                    pass

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
