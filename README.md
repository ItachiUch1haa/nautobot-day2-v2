# nautobot-day2

Customer/site onboarding pipeline and day-2 network data sync (SSH + vendor
cloud APIs) for multi-vendor networks, packaged as an installable Nautobot
App.

## Layout

```
nautobot_day2/            installable Python package (the Nautobot App)
├── __init__.py           NautobotAppConfig — this is what Nautobot loads
├── jobs/                 Nautobot Jobs (run via Nautobot's Job scheduler/Celery)
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
    }
}
```

Run `nautobot-server post_upgrade` (or restart Nautobot) so the Jobs in
`nautobot_day2.jobs` get registered and appear under **Jobs** in the UI.

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
