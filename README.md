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
