# nautobot-day2

Customer/site onboarding, day-2 network data sync (SSH + vendor cloud APIs),
a web onboarding wizard, and an Agent Broker for ad-hoc device troubleshooting
— for multi-vendor MSP networks, packaged as an installable Nautobot App.

## Layout

```
nautobot_day2/            installable Python package (the Nautobot App)
├── __init__.py           NautobotAppConfig — this is what Nautobot loads
├── client.py              shared Nautobot REST client (auth, retry, pagination)
├── concurrency.py         per-site concurrency cap for fanned-out device tasks
├── tasks.py               Celery tasks — one device-sync task per device
├── openbao_client.py      OpenBao (open-source Vault fork) credential fetch — AppRole auth
├── jobs/                  Nautobot Jobs (dispatchers — run via Job scheduler/Celery)
│   ├── mist_sync.py
│   └── sync_network_data_job.py
├── chatops/                Slack/Teams commands (via nautobot-chatops)
│   └── worker.py           /nautobot onboard, /nautobot fill-creds
├── broker/                 Agent Broker — ad-hoc command execution for external agents
│   ├── core.py             shared logic: Nautobot lookup -> OpenBao credential -> Nornir dispatch
│   ├── api_server.py       REST wrapper (port 8082)
│   └── mcp_server.py       MCP wrapper (port 8090) — same logic, different transport
├── onboarding/             onboarding pipeline + vendor sync engine
│   ├── upload_app.py       web onboarding wizard (Flask, port 8081) — see below
│   ├── templates/index.html   the 6-step wizard UI
│   ├── onboard_cli.py      older terminal-based onboarding orchestrator
│   ├── create_tenant.py
│   ├── nautobot_prepare.py
│   ├── nautobot_onboard_v2.py   device creation, incl. VirtualChassis/HA support
│   ├── sync_network_data.py
│   ├── vendor_matrix.py    single source of truth for vendor/device/access-method combos
│   └── ...
└── vendor_commands/
    └── vendor_commands.yaml   SSH commands / API endpoints per vendor
```

## Install — as a plugin, in production

The Nautobot-facing part of this is **one plugin, nothing more**: install the
package, add it to `PLUGINS`, restart Nautobot. Everything else in this
README (the web wizard, the Agent Broker) are separate standalone processes
that happen to share this same package — they are not required for
`nautobot_day2` to work as a Nautobot App.

```bash
pip install .                 # or: pip install ".[broker]" for the Agent Broker's deps too
```

```python
# nautobot_config.py
PLUGINS = ["nautobot_day2"]
PLUGINS_CONFIG = {
    "nautobot_day2": {
        # Base dir for tenant credential .env files — shared/mounted storage
        # across every worker machine, not local disk on just one box.
        "tenants_dir": "/opt/nautobot/nautobot_day2_tenants",
        # Max concurrent device-sync tasks per site, regardless of how many
        # Celery workers are running.
        "max_concurrent_per_site": 5,
    }
}
```

`nautobot-server post_upgrade` (or restart) registers the Jobs
(`SyncNetworkData`, `SyncAllSites`, `MistSyncJob` — all registered via
`register_jobs()`, Nautobot's standard convention) so they appear under
**Jobs** in the UI.

### Celery worker — required for the sync Jobs to actually run devices

`SyncNetworkData`/`SyncAllSites` dispatch one Celery task per device to the
`nautobot_day2_sync` queue instead of looping themselves — devices sync in
parallel. Nautobot's default worker only listens on `default`:

```bash
nautobot-server celery worker -Q default,nautobot_day2_sync
```

For real horizontal scale, run a **separate** worker deployment dedicated to
`nautobot_day2_sync`:

```bash
nautobot-server celery worker -Q nautobot_day2_sync --concurrency=10
```

Add more replicas to sync more devices in parallel across the fleet.
`max_concurrent_per_site` still caps how many run against any one site at
once, no matter how many replicas you add.

### ChatOps — onboard from Slack (Microsoft Teams later, same code)

1. `pip install ".[chatops]"`
2. Add `nautobot_chatops` to `PLUGINS` **before** `nautobot_day2`, follow
   nautobot-chatops' own Slack app setup (bot token + signing secret).
   `nautobot_day2`'s commands register automatically via the
   `nautobot.workers` entry point in `pyproject.toml`.
3. Restart Nautobot's web and worker processes.

`/nautobot onboard` (site onboarding / credential check / trigger a sync),
`/nautobot fill-creds <tenant-slug>` (fills missing credentials one at a
time through a private prompt, writing into that tenant's `.env`).

## Onboarding a customer — two ways

**The web wizard** (`onboarding/upload_app.py`, Flask, port 8081) is the
current, complete path: a 6-step UI —
tenant & site → device data (interactive table or CSV, with checkboxes for
stacked switches and firewall HA pairs) → validation → credentials →
live credential validation (real SSH/Mist/Aruba-Central connectivity
tests, not just "is the field filled in") → **deploy**. Deploy creates the
tenant/devices in Nautobot and triggers the real `SyncAllSites` Job over
Nautobot's REST API (`POST extras/jobs/<id>/run`) — the same Celery fan-out
described above, not a separate code path.

**`onboard_cli.py`** is the older terminal-based orchestrator (same phases,
run from a shell instead of a browser). It has not been updated to trigger
the Job the same way the web wizard does — its sync step still calls
`sync_network_data.py` directly, sequentially. Prefer the web wizard for
anything beyond quick CLI testing until this is reconciled.

Device onboarding supports **stacked switches** (VirtualChassis, grouped by
a `stack_group` CSV column) and **firewall HA pairs** (DeviceRedundancyGroup,
via `ha_group`) — a device can be one or the other, never both.

## Credentials — OpenBao

Tenant device credentials are stored in **OpenBao** (the open-source,
Linux-Foundation-governed fork of HashiCorp Vault — used specifically to
avoid Vault's BSL licensing terms), authenticated via AppRole
(`BAO_ADDR`/`BAO_ROLE_ID`/`BAO_SECRET_ID`), alongside the tenant `.env` file
mechanism. `nautobot_day2/openbao_client.py` is the single point of contact
for this — it fetches fresh on every call rather than caching a token.

## Agent Broker — ad-hoc command execution for external agents

`nautobot_day2/broker/` exposes two interfaces (REST on :8082, MCP on :8090,
sharing one implementation in `core.py`) that let an external agent look up
a device in Nautobot, fetch its credential from OpenBao, and run a command
against it over Nornir/Netmiko — for live troubleshooting, not scheduled
sync.

**⚠️ Known, deliberate gap — not yet safe for production or external
exposure:** there is currently **no command allowlist and no authentication**
on either interface (documented in the code itself: *"No command allowlist,
no restricted-account enforcement... any command string is accepted"*).
Anything that can reach port 8082 or 8090 can run **any** command — including
destructive ones (`reload`, `write erase`, config-mode changes) — against
any device it can resolve, using a real fetched credential, with no gate at
all. Before this is exposed beyond a trusted internal network:
- add a per-vendor, pattern-based read-only command allowlist (not an
  exact-command list — match on `show`/`display`/`get`/`ping` verbs per
  vendor grammar), with explicit exclusions for commands that leak secrets
  even though they're read-only (`show running-config`, etc.) and for
  expensive/disruptive read commands (`show tech-support`, etc.);
- add authentication to both the REST and MCP endpoints;
- add an audit log of every command attempted, allowed or not;
- add a human-approval escalation path for anything that doesn't match the
  safe pattern, rather than a silent dead end.

Treat this section as the top priority before any MSP-production or
external-agent use of the broker.

## Deploying this

`deploy/single-server/` has a Docker Compose stack for a first test:
Postgres, Redis, Nautobot, a Celery worker, the onboarding web wizard, the
Agent Broker (REST + MCP), and OpenBao — nine services in total, all wired
together with health checks. `deploy/single-server/INSTALL.md` walks through
it from a completely clean Ubuntu server.

### Scaling to multiple servers

`nautobot_day2` is one plugin, not one-per-server — the same install runs on
every machine; what changes between single-server and a multi-server
production cluster is configuration, not code:

- **Nautobot web + Postgres + Redis** stay on one machine (or their own, for
  HA) — a normal load balancer (nginx/HAProxy) sits in front of the web tier.
- **Celery workers scale horizontally for device count**: add more worker
  machines pointed at the same Redis; Redis's queue naturally spreads work
  across however many are running. `max_concurrent_per_site` still caps
  per-site load regardless of total worker count.
- **OpenBao and the Agent Broker** need their own hardening pass (see above)
  before they're part of a multi-server production picture — don't just
  lift-and-shift the single-server compose file's broker services as-is.

### Status — what's actually been verified

- Bugs have been found and fixed via **live testing against real hardware**
  (a platform-slug collision that mis-assigned Juniper switches to the wrong
  platform, among others) — this is no longer purely lab-simulated.
- The onboarding wizard has been verified end-to-end in a real browser.
- The Agent Broker's core lookup → credential → Nornir dispatch pipeline
  works, but ships with no safety gate — see the warning above.
- Not yet built: the command allowlist/auth for the broker, split worker
  pools for config-push automation, Golden Config integration, and
  reconciling `onboard_cli.py`'s sync step onto the Celery pipeline.

## Running the onboarding scripts directly

```bash
python3 nautobot_day2/onboarding/onboard_cli.py --profile profiles/acme-retail.json
```

Each script in `onboarding/` still runs standalone the same way it always
has. See each script's own `--help` for phase-by-phase usage.
