"""
Session state machine for onboarding-mcp -- architecture doc §3 (states)
and §4 (which tool call is legal from which state). One onboarding
session = one in-progress site/device batch, stored server-side so a chat
client can reconnect mid-flow without losing progress.

Redis-backed in production (session key onboarding_session:<uuid>, TTL 2h)
via connect()/redis_store() below -- but OnboardingSession itself only
depends on a tiny get/set store interface, so test_state_machine.py can
exercise every transition rule against an in-memory fake with no live
Redis server, per the brief's Phase 2 requirement.
"""
import json
import os
import time
import uuid

SESSION_TTL_SECONDS = 2 * 60 * 60  # 2 hours, per architecture doc §3
SESSION_KEY_PREFIX = "onboarding_session:"

INIT = "INIT"
TENANT_RESOLUTION = "TENANT_RESOLUTION"
SITE_RESOLUTION = "SITE_RESOLUTION"
DEVICE_INTAKE = "DEVICE_INTAKE"
CONTROLLER_SCAN = "CONTROLLER_SCAN"
REVIEW = "REVIEW"
DONE = "DONE"

# tool_name -> set of legal FROM states. A tool not in this table (e.g.
# get_session_status) is legal from any state and never transitions.
LEGAL_FROM_STATES = {
    "start_onboarding": {INIT},
    "set_tenant": {TENANT_RESOLUTION},
    "set_site": {SITE_RESOLUTION},
    "add_static_device": {DEVICE_INTAKE},
    "set_ap_controller": {DEVICE_INTAKE},
    "scan_ap_controller": {CONTROLLER_SCAN},
    "select_discovered_aps": {CONTROLLER_SCAN},
    "review_pending_batch": {DEVICE_INTAKE, CONTROLLER_SCAN, REVIEW},
    "remove_pending_device": {REVIEW},
    "deploy_site": {REVIEW},
}

# tool_name -> the state a successful call moves the session TO. Tools
# not listed here (scan_ap_controller — re-runnable per §4 —, and
# remove_pending_device) leave the session in its current state.
NEXT_STATE = {
    "start_onboarding": TENANT_RESOLUTION,
    "set_tenant": SITE_RESOLUTION,
    "set_site": DEVICE_INTAKE,
    "add_static_device": DEVICE_INTAKE,
    "set_ap_controller": CONTROLLER_SCAN,
    "select_discovered_aps": DEVICE_INTAKE,
    "review_pending_batch": REVIEW,
    "deploy_site": DONE,
}


class IllegalTransitionError(Exception):
    """Raised when a tool is called from a session state it isn't legal from."""


class SessionNotFoundError(Exception):
    """Raised when a session id doesn't exist (never created, or its TTL expired)."""


class OnboardingSession:
    """
    One in-progress onboarding session. `store` only needs `.get(key)` /
    `.set(key, value, ex=seconds)` -- a real redis.Redis client (via
    redis_store() below) in production, an in-memory fake in tests.
    """

    def __init__(self, store, session_id=None):
        self.store = store
        self.session_id = session_id or str(uuid.uuid4())

    @property
    def _key(self):
        return f"{SESSION_KEY_PREFIX}{self.session_id}"

    def _load(self):
        raw = self.store.get(self._key)
        if raw is None:
            raise SessionNotFoundError(self.session_id)
        return json.loads(raw)

    def _save(self, data):
        self.store.set(self._key, json.dumps(data), ex=SESSION_TTL_SECONDS)

    @classmethod
    def create(cls, store):
        """Create a brand new session in INIT state and persist it."""
        session = cls(store)
        session._save({
            "state": INIT,
            "tenant": None,
            "site": None,
            "pending_devices": [],
            "pending_controller": None,
            "created_at": time.time(),
        })
        return session

    def get_status(self):
        """Return the session's current state + full pending batch (get_session_status — no transition)."""
        return self._load()

    def transition(self, tool_name, mutate=None):
        """
        Validate that `tool_name` is legal from this session's current
        state, apply `mutate(data) -> data` if given, then move to
        NEXT_STATE[tool_name] if that tool has one.

        This is the single choke point every MCP tool handler calls
        through. DEVICE_INTAKE is only ever reached via set_site's
        NEXT_STATE entry — and tools_schema.py's set_site handler only
        calls transition("set_site", ...) after onboard_site() has
        actually returned a real Prefix with nat_shadow_prefix populated
        (new-site path) or the existing site's real prefix has been
        confirmed (existing-site path). So add_static_device /
        set_ap_controller — both legal only from DEVICE_INTAKE — can
        never succeed on a session that skipped set_site: this satisfies
        architecture doc §8's hard sequencing rule structurally, via the
        transition table itself, not by convention a handler could forget.
        """
        data = self._load()
        current_state = data["state"]
        legal_from = LEGAL_FROM_STATES.get(tool_name)
        if legal_from is not None and current_state not in legal_from:
            raise IllegalTransitionError(
                f"{tool_name} is not legal from state {current_state} "
                f"(legal from: {sorted(legal_from)})"
            )

        if mutate is not None:
            data = mutate(data)

        next_state = NEXT_STATE.get(tool_name)
        if next_state is not None:
            data["state"] = next_state

        self._save(data)
        return data


def redis_store(redis_client):
    """Adapt a real redis.Redis client to the tiny get/set store interface OnboardingSession expects."""

    class _RedisStore:
        def get(self, key):
            return redis_client.get(key)

        def set(self, key, value, ex=None):
            redis_client.set(key, value, ex=ex)

    return _RedisStore()


def connect():
    """
    Build a redis.Redis client from the same NAUTOBOT_REDIS_HOST/
    NAUTOBOT_REDIS_PASSWORD env vars nautobot/nautobot-worker already use
    (see docker-compose.yml) — reusing the existing redis service, not a
    new one. Uses a separate REDIS DB index (default 2) so session keys
    stay isolated from Django's own cache/Celery-broker use of DB 0/1.
    concurrency.py's `django.core.cache` isn't usable here — onboarding-mcp
    is a standalone script with no Django settings loaded, exactly like
    upload_app.py / broker/mcp_server.py — so this connects with a plain
    redis client instead.
    """
    import redis as redis_lib

    host = os.environ.get("NAUTOBOT_REDIS_HOST", "redis")
    port = int(os.environ.get("NAUTOBOT_REDIS_PORT", "6379"))
    password = os.environ.get("NAUTOBOT_REDIS_PASSWORD")
    db = int(os.environ.get("ONBOARDING_MCP_REDIS_DB", "2"))
    return redis_lib.Redis(host=host, port=port, password=password, db=db, decode_responses=True)
