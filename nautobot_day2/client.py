"""
nautobot_day2.client
Shared Nautobot REST client — one place for config, auth, retries, and
pagination.

Every onboarding script used to open its own requests.Session()-less
connection: load .env itself, build its own Authorization header, and
hand-roll a `while data.get('next')` pagination loop. That duplication is
exactly how the stale-lab-path and hollow-SecretsGroup bugs slipped in —
each copy could drift from the others. This module is the one place that
logic lives now; scripts import NautobotClient instead of re-implementing it.
"""

import logging
import os

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def get_logger(name):
    """Return a module logger with a consistent format, configured once."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(os.environ.get("NAUTOBOT_DAY2_LOG_LEVEL", "INFO"))
    return logger


class NautobotAPIError(Exception):
    """Raised when a Nautobot API call fails after retries are exhausted."""


class NautobotClient:
    """
    Thin wrapper around Nautobot's REST API: auth headers, retry/backoff on
    transient failures (connection errors, 5xx), and automatic pagination.

    One instance per script/process — cheap to create, reuses a single
    requests.Session (and its connection pool) for every call.

        client = NautobotClient(env_file=".env")
        devices = client.get_all("dcim/devices", params={"site": "hq"})
        group, created = client.get_or_create(
            "extras/secrets-groups", "aruba-ssh-acme", {"name": "aruba-ssh-acme"}
        )
    """

    def __init__(self, url=None, token=None, timeout=10, env_file=None):
        if env_file and os.path.exists(env_file):
            load_dotenv(env_file)

        self.url = (url or os.environ.get("NAUTOBOT_URL", "http://127.0.0.1:8080")).rstrip("/")
        self.token = token or os.environ.get("NAUTOBOT_TOKEN")
        self.timeout = timeout
        self.log = get_logger("nautobot_day2.client")

        if not self.token:
            self.log.warning("NAUTOBOT_TOKEN not set — requests will be unauthenticated")

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {self.token}" if self.token else "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PATCH", "DELETE"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ── low-level ──────────────────────────────────────────────────────

    def _endpoint_url(self, endpoint):
        return f"{self.url}/api/{endpoint.strip('/')}/"

    def get(self, endpoint, params=None):
        return self.session.get(self._endpoint_url(endpoint), params=params, timeout=self.timeout)

    def post(self, endpoint, data):
        return self.session.post(self._endpoint_url(endpoint), json=data, timeout=self.timeout)

    def patch(self, endpoint, data):
        return self.session.patch(self._endpoint_url(endpoint), json=data, timeout=self.timeout)

    def delete(self, endpoint):
        return self.session.delete(self._endpoint_url(endpoint), timeout=self.timeout)

    def get_absolute(self, url, params=None):
        """GET a full URL Nautobot handed back (e.g. a hyperlinked
        `primary_ip4.url`), reusing this client's auth/retry/session."""
        return self.session.get(url, params=params, timeout=self.timeout)

    # ── pagination ─────────────────────────────────────────────────────

    def get_all(self, endpoint, params=None):
        """Fetch every page of a Nautobot list endpoint; return the combined results."""
        results = []
        url = self._endpoint_url(endpoint)
        next_params = params

        while url:
            r = self.session.get(url, params=next_params, timeout=self.timeout)
            if not r.ok:
                raise NautobotAPIError(f"GET {url} -> {r.status_code}: {r.text[:200]}")
            data = r.json()
            results.extend(data.get("results", []))
            url = data.get("next")
            next_params = None  # 'next' is already a full URL with query params baked in

        return results

    def count(self, endpoint, params=None):
        """Return an endpoint's result count, or None if the request failed."""
        r = self.get(endpoint, params=params)
        return r.json().get("count", 0) if r.ok else None

    # ── find-or-create helpers ─────────────────────────────────────────

    def find_by_name(self, endpoint, name):
        """
        Look up an object by exact name. Tries the `?name=` filter first;
        endpoints that don't support it (400) fall back to scanning the
        first page of results.
        Returns (found: bool, obj: dict | None).
        """
        r = self.get(endpoint, params={"name": name, "limit": 10})
        if r.status_code == 400:
            r = self.get(endpoint, params={"limit": 200})
        if not r.ok:
            return False, None
        for obj in r.json().get("results", []):
            if obj.get("name") == name:
                return True, obj
        return False, None

    def get_id_by_name(self, endpoint, name):
        found, obj = self.find_by_name(endpoint, name)
        return obj["id"] if found else None

    def get_or_create(self, endpoint, name, payload):
        """
        Idempotent create: if an object named `name` already exists at
        `endpoint`, return it unchanged; otherwise POST `payload`.
        Returns (obj: dict, created: bool). Raises NautobotAPIError on a
        failed create — callers that want to keep going after a failure
        should catch it.
        """
        found, obj = self.find_by_name(endpoint, name)
        if found:
            return obj, False
        r = self.post(endpoint, payload)
        if r.status_code != 201:
            raise NautobotAPIError(f"POST {endpoint} -> {r.status_code}: {r.text[:200]}")
        return r.json(), True
