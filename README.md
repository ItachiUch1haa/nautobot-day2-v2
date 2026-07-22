# nautobot-day2

Customer/site onboarding pipeline and day-2 network data sync (SSH + vendor
cloud APIs) for multi-vendor networks, packaged as an installable Nautobot
App.

## Layout

```
nautobot_day2/            installable Python package (the Nautobot App)
├── __init__.py           NautobotAppConfig — this is what Nautobot loads
├── client.py             shared Nautobot REST client (auth, retry, pagination)
├── concurrency.py         per-site concurrency cap for fanned-out device tasks
├── tasks.py               Celery tasks — one device-sync task per device
├── jobs/                 Nautobot Jobs (dispatchers — run via Job scheduler/Celery)
│   ├── mist_sync.py
│   └── sync_network_data_job.py
├── chatops/               Slack/Teams commands (via nautobot-chatops)
│   └── worker.py          /nautobot onboard, /nautobot fill-creds
├── onboarding/            onboarding CLI pipeline + vendor sync engine
│   ├── onboard_cli.py     orchestrates the phases below
│   ├── create_tenant.py
│   ├── nautobot_prepare.py
│   ├── nautobot_onboard_v2.py
│   ├── sync_network_data.py
│   ├── vendor_matrix.py   single source of truth for vendor/device/access-method combos
│   └── ...
└── vendor_commands/
    └── vendor_commands.yaml   SSH commands / API endpoints per vendor
```

## Install

```bash
pip install .
```

Then register it with Nautobot in `nautobot_config.py`:

```python
PLUGINS = ["nautobot_day2"]
PLUGINS_CONFIG = {
    "nautobot_day2": {
        # Base dir for tenant credential .env files. Defaults to
        # /etc/nautobot/tenants — override per deployment.
        "tenants_dir": "/etc/nautobot/tenants",
        # Max concurrent device-sync tasks per site, regardless of how many
        # Celery workers are running.
        "max_concurrent_per_site": 5,
    }
}
```

Run `nautobot-server post_upgrade` (or restart Nautobot) so the Jobs in
`nautobot_day2.jobs` get registered and appear under **Jobs** in the UI.

### Celery worker — required for the sync Jobs to actually run devices

`SyncNetworkData`/`SyncAllSites` no longer loop over devices themselves —
they dispatch one Celery task per device to a queue named
`nautobot_day2_sync`, so devices sync in parallel instead of one at a time.
Nautobot's default worker only listens on the `default` queue, so it needs
to also consume this one:

```bash
nautobot-server celery worker -Q default,nautobot_day2_sync
```

For real horizontal scale, run a **separate** worker deployment dedicated
to `nautobot_day2_sync` (its own replica count, scaled independently of
Nautobot's other background work):

```bash
nautobot-server celery worker -Q nautobot_day2_sync --concurrency=10
```

Add more replicas of that command to sync more devices in parallel across
the fleet. `max_concurrent_per_site` above still caps how many of those
run against any single site at once, no matter how many replicas you add.

### ChatOps — onboard from Slack (Microsoft Teams later, same code)

1. Install with the `chatops` extra so `nautobot-chatops` comes along:
   ```bash
   pip install ".[chatops]"
   ```
2. Add `nautobot_chatops` to `PLUGINS` **before** `nautobot_day2` in
   `nautobot_config.py`, and follow nautobot-chatops' own Slack app setup
   (bot token + signing secret from your Slack workspace — see its docs).
   `nautobot_day2`'s commands register automatically via the
   `nautobot.workers` entry point declared in `pyproject.toml` — no extra
   config needed on this App's side.
3. Restart Nautobot's web and worker processes.

In Slack:
- `/nautobot onboard` — menu: onboard a new site for an existing tenant,
  check a tenant's credential status, or trigger a sync on demand.
- `/nautobot fill-creds <tenant-slug>` — fills in missing device credentials
  one at a time through a private prompt (never posted to the channel),
  writing straight into that tenant's `.env` file — replaces the old
  "SSH into the server and edit it by hand" step.

New-customer setup (choosing which vendors/device types a tenant uses)
still goes through `create_tenant.py`'s profile JSON or the CLI — that's
a multi-select choice better suited to a file/form than a chat wizard.
Once a tenant profile exists, the rest of the flow works from chat.

When you're ready for Microsoft Teams, it's the same `nautobot_day2`
code — just add the Teams adapter to `nautobot-chatops`' own config.

## Deploying this

`deploy/single-server/` has a ready-to-run Docker Compose stack (Postgres +
Redis + Nautobot + one Celery worker with `nautobot_day2` installed) for a
first test. `deploy/single-server/INSTALL.md` walks through it from a
completely clean Ubuntu server; `deploy/single-server/README.md` covers the
compose stack itself if Docker's already set up. That single-server layout
is also the starting point for the multi-server production shape — see
"Scaling to multiple servers" below.

### Scaling to multiple servers

`nautobot_day2` is one plugin (one Python package), not one-plugin-per-server
— the same install runs on every machine, and what changes between a
single-server test and a multi-server production cluster is only
*configuration*, not code:

- **Nautobot web + Postgres + Redis** stay on one machine (or their own
  machines, for HA) — this is the tier a normal load balancer (nginx/HAProxy)
  sits in front of, same as any web app.
- **Celery workers are the tier that scales horizontally for device count**:
  add more worker machines, all pointed at the same Redis, and Redis's queue
  naturally spreads work across however many are running — that *is* the
  load balancing for device sync, no separate load-balancer software needed
  for this tier. `max_concurrent_per_site` still caps how many run against
  any one site at once, regardless of total worker count.
- This is proven out structurally (see `deploy/single-server/`) but the
  multi-server split, Nornir-based orchestration, a real secrets broker
  (Vault/OpenBao), and split sync-vs-config-push worker pools for MSP scale
  are planned next, not built yet — see "Status" below.

### Status — what's actually been verified

- Every module here compiles and its internal wiring (imports, function
  signatures across files) has been checked.
- The Docker Compose stack's structure is validated (`docker compose config`),
  but has not yet been booted against a live Nautobot instance anywhere —
  first real boot happens on your own server.
- Not yet built: Nornir-based device orchestration, a real secrets broker,
  split worker pools for config-push automation, and the allowlist-gated
  ad-hoc command execution path for external agent troubleshooting.

## Running the onboarding pipeline

`onboard_cli.py` is the entry point for onboarding a new tenant/site — it
runs `create_tenant.py` → `nautobot_prepare.py` → `nautobot_onboard_v2.py` →
`sync_network_data.py` in sequence:

```bash
python3 nautobot_day2/onboarding/onboard_cli.py --profile profiles/acme-retail.json
```

(Each script in `onboarding/` still runs standalone the same way it always
has — no change to how you invoke it day to day.)

See each script's own `--help` for phase-by-phase usage.
