"""
Shared OpenBao KV v2 client, used by both sync_network_data.py (the
scheduled/Slack-triggered sync engine) and the agent broker (per-request
diagnostic access). Kept as a single shared function so a fix or change
in one place propagates to both consumers rather than drifting apart.
"""
import os
import requests


def fetch_openbao_secret(tenant_slug, path_suffix):
    """
    Fetch a secret from OpenBao KV v2 using AppRole auth.
    Logs in fresh every call (no token caching) per design decision.
    Raises on ANY failure (unreachable, sealed, auth failure)
    — no silent fallback to os.environ.
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
        login_resp = requests.put(
            f"{bao_addr}/v1/auth/approle/login",
            json={"role_id": role_id, "secret_id": secret_id},
            timeout=10,
        )
        login_resp.raise_for_status()
        client_token = login_resp.json()["auth"]["client_token"]
    except Exception as e:
        raise Exception(f"OPENBAO_AUTH_FAILURE: could not authenticate to OpenBao — {e}")
    kv_path = f"kv/data/tenants/{tenant_slug}/{path_suffix}"
    try:
        secret_resp = requests.get(
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
