"""
Shared OpenBao KV v2 client, used by both sync_network_data.py (the
scheduled/Slack-triggered sync engine) and the agent broker (per-request
diagnostic access). Kept as a single shared function so a fix or change
in one place propagates to both consumers rather than drifting apart.
"""
import os
import time
import requests

# Shared connection pool for all OpenBao calls (login + KV reads/writes) --
# previously every call used bare requests.put/get/post with no session, a
# fresh TCP connection each time.
_session = requests.Session()

_TOKEN_CACHE = {}
# In-process cache: (bao_addr, role_id) -> {"client_token":, "expires_at":}.
# Mirrors the same caching pattern already used for Aruba Central OAuth
# tokens in sync_network_data.py's _ARUBA_TOKEN_CACHE -- per-process only,
# not shared across agent-broker / agent-broker-mcp / nautobot-worker
# containers, but it eliminates the common case (a fresh AppRole login on
# every single secret fetch) within one running process/worker.


def _openbao_login(bao_addr, role_id, secret_id):
    cache_key = (bao_addr, role_id)
    cached = _TOKEN_CACHE.get(cache_key)
    if cached and cached["expires_at"] > time.time() + 30:
        return cached["client_token"]

    login_resp = _session.put(
        f"{bao_addr}/v1/auth/approle/login",
        json={"role_id": role_id, "secret_id": secret_id},
        timeout=10,
    )
    login_resp.raise_for_status()
    auth = login_resp.json()["auth"]
    client_token = auth["client_token"]
    lease_duration = auth.get("lease_duration", 0)
    if lease_duration:
        _TOKEN_CACHE[cache_key] = {
            "client_token": client_token,
            "expires_at": time.time() + lease_duration,
        }
    else:
        _TOKEN_CACHE.pop(cache_key, None)
    return client_token


def update_rotated_credential(tenant_slug, path_suffix, updates):
    """
    Merge-update specific fields in an existing OpenBao secret — used
    for credentials that rotate on use (e.g. OAuth2 refresh tokens).
    Reads the current secret, overlays `updates` on top (never a blind
    overwrite, so unrelated fields already in the secret are preserved),
    writes the merged result back.

    Uses a SEPARATE, write-scoped AppRole (day2-credential-refresher)
    from fetch_openbao_secret()'s read-only one — the read-only
    day2-sync-engine/day2-agent-broker identities are never broadened
    with write access. This function should only ever be called for
    secrets groups explicitly flagged credential_rotates: true in
    vendor_commands.yaml, and only from internal rotation logic, never
    from an agent-facing or request-driven path.

    Deliberately does NOT raise on failure by default (see swallow_errors)
    — a failed save-back should not prevent returning an access token
    that was already successfully obtained in the same call. Callers
    that need to know about a save failure can pass swallow_errors=False.
    """
    bao_addr = os.environ.get('BAO_ADDR')
    role_id = os.environ.get('BAO_REFRESHER_ROLE_ID')
    secret_id = os.environ.get('BAO_REFRESHER_SECRET_ID')
    if not all([bao_addr, role_id, secret_id]):
        raise Exception(
            "OPENBAO_CONFIG_ERROR: BAO_ADDR, BAO_REFRESHER_ROLE_ID, or "
            "BAO_REFRESHER_SECRET_ID not set in environment — cannot "
            "rotate credential."
        )

    try:
        login_resp = _session.put(
            f"{bao_addr}/v1/auth/approle/login",
            json={"role_id": role_id, "secret_id": secret_id},
            timeout=10,
        )
        login_resp.raise_for_status()
        client_token = login_resp.json()["auth"]["client_token"]
    except Exception as e:
        raise Exception(f"OPENBAO_AUTH_FAILURE: could not authenticate refresher identity — {e}")

    kv_path = f"kv/data/tenants/{tenant_slug}/{path_suffix}"
    try:
        current_resp = _session.get(
            f"{bao_addr}/v1/{kv_path}",
            headers={"X-Vault-Token": client_token},
            timeout=10,
        )
        current_data = {}
        if current_resp.status_code == 200:
            current_data = current_resp.json()["data"]["data"]
        elif current_resp.status_code != 404:
            current_resp.raise_for_status()

        merged = {**current_data, **updates}

        write_resp = _session.post(
            f"{bao_addr}/v1/{kv_path}",
            headers={"X-Vault-Token": client_token},
            json={"data": merged},
            timeout=10,
        )
        write_resp.raise_for_status()
        return True
    except Exception as e:
        raise Exception(f"OPENBAO_ROTATE_FAILURE: could not update {kv_path} — {e}")


def fetch_openbao_secret(tenant_slug, path_suffix):
    """
    Fetch a secret from OpenBao KV v2 using AppRole auth.
    The AppRole login is cached in-process for the lifetime of its lease
    (see _openbao_login) instead of re-authenticating on every call --
    at MSP scale (many devices/tenants, jobs firing across a large fleet)
    a fresh AppRole login per secret fetch meant every sync run paid a
    full OpenBao auth round-trip per device. Still raises on ANY failure
    (unreachable, sealed, auth failure) — no silent fallback to os.environ.
    """
    bao_addr = os.environ.get('BAO_ADDR')
    role_id = os.environ.get('BAO_ROLE_ID')
    secret_id = os.environ.get('BAO_SECRET_ID')
    if not all([bao_addr, role_id, secret_id]):
        raise Exception(
            "OPENBAO_CONFIG_ERROR: BAO_ADDR, BAO_ROLE_ID, or BAO_SECRET_ID "
            "not set in environment — cannot resolve credentials."
        )
    try:
        client_token = _openbao_login(bao_addr, role_id, secret_id)
    except Exception as e:
        raise Exception(f"OPENBAO_AUTH_FAILURE: could not authenticate to OpenBao — {e}")
    kv_path = f"kv/data/tenants/{tenant_slug}/{path_suffix}"
    try:
        secret_resp = _session.get(
            f"{bao_addr}/v1/{kv_path}",
            headers={"X-Vault-Token": client_token},
            timeout=10,
        )
        if secret_resp.status_code == 403:
            # Cached token may have been revoked/expired server-side --
            # drop it and retry once with a fresh login before giving up.
            _TOKEN_CACHE.pop((bao_addr, role_id), None)
            client_token = _openbao_login(bao_addr, role_id, secret_id)
            secret_resp = _session.get(
                f"{bao_addr}/v1/{kv_path}",
                headers={"X-Vault-Token": client_token},
                timeout=10,
            )
        if secret_resp.status_code == 404:
            return {}
        secret_resp.raise_for_status()
        return secret_resp.json()["data"]["data"]
    except requests.exceptions.HTTPError as e:
        raise Exception(f"OPENBAO_FETCH_FAILURE: could not read {kv_path} — {e}")
    except Exception as e:
        raise Exception(f"OPENBAO_UNREACHABLE: could not reach OpenBao at {bao_addr} — {e}")
