# Component Paths & Locations

Exact file paths (relative to repo root), process type, and network
port/queue for every component. Pair with `02-COMPONENTS.md` for what
each one does.

## Repo layout

```
nautobot_day2/                      installable Python package (the Nautobot App)
├── __init__.py                     NautobotAppConfig — App registration + PLUGINS_CONFIG defaults
├── client.py                       NautobotClient — shared REST client
├── concurrency.py                  site_slot() — per-site concurrency guard
├── openbao_client.py               OpenBao KV v2 client (fetch + rotate)
├── tasks.py                        Celery tasks: sync_device_task, sync_summary_callback
├── jobs/
│   ├── __init__.py
│   ├── sync_network_data_job.py    Jobs: SyncNetworkData, SyncAllSites
│   └── mist_sync.py                Job: MistSyncJob (Juniper Mist SSoT)
├── chatops/
│   ├── __init__.py
│   └── worker.py                   /nautobot onboard, /nautobot fill-creds
├── broker/
│   ├── core.py                     shared broker logic
│   ├── api_server.py                REST wrapper
│   └── mcp_server.py                MCP wrapper
├── onboarding_mcp/                  conversational onboarding — MCP server, :8091
│   ├── server.py                     MCP transport (streamable-http)
│   ├── tools_schema.py               the 11 MCP tools
│   ├── session/state_machine.py      Redis-backed session state machine
│   ├── controllers/                  local_ssh_master, meraki, mist, aruba_central, other
│   ├── intake/                       static_device / ap_discovery validation
│   └── deploy/                       credential_writer, nautobot_deployer
├── shadow_ip/                       real-to-shadow IP mapping (RFC 6598 NAT catalog)
│   ├── shadow_math.py                offset-preserving compute_shadow_ip/compute_real_ip
│   ├── site_onboarding.py            onboard_site() — real+shadow Prefix pair
│   ├── custom_fields.py              one-time bootstrap (Phase 14a)
│   ├── jobs/                         OnboardSite, CatalogShadowIP, ReconcileDeviceIPs
│   └── integrations/fortigate_client.py  FortiGate NVA REST client (pending live verification)
├── onboarding/
│   ├── upload_app.py                web wizard (Flask)
│   ├── templates/
│   │   ├── index.html               the 6-step wizard UI
│   │   └── vendor_test.html
│   ├── bootstrap_nautobot.py         Phase 1
│   ├── preflight_check.py            Phase 2
│   ├── create_tenant.py              Phase 3
│   ├── nautobot_prepare.py           Phase 4 (normalization helpers)
│   ├── nautobot_onboard_v2.py        Phase 5
│   ├── sync_network_data.py          Phase 6 (sync engine)
│   ├── credential_checker.py
│   ├── vendor_test.py / vendor_test_app.py
│   ├── onboard_cli.py                terminal orchestrator
│   ├── vendor_matrix.py              vendor/device/access-method matrix
│   ├── engineer_template.csv         reference CSV format
│   └── profiles/                     tenant profile JSONs (if not overridden by tenants_dir)
└── vendor_commands/
    └── vendor_commands.yaml          SSH commands / API endpoints per vendor

deploy/
├── PRODUCTION_GUIDE.md               multi-server production topology
└── single-server/
    ├── INSTALL.md                    clean-Ubuntu-to-running walkthrough
    ├── README.md
    ├── docker-compose.yml             all 10 services, single-server test stack
    ├── Dockerfile                     shared image for nautobot/worker/wizard/broker
    ├── nautobot_config.py             mounted into the Nautobot image
    └── .env.example                   secrets template

pyproject.toml                        package metadata, deps, entry points
README.md                             top-level orientation (start here)
PROJECT_CONTEXT.md
```

## Process / service reference

| Process | Entry point | Port | Runs as | Queue (if Celery) |
|---|---|---|---|---|
| Nautobot web | `nautobot-server runserver` (or WSGI/ASGI in real prod) | 8080 | Django/Nautobot process | — |
| Celery worker (single-server test) | `nautobot-server celery worker -Q default,nautobot_day2_sync` | — | Celery worker | `default`, `nautobot_day2_sync` |
| Celery worker (production, dedicated) | `nautobot-server celery worker -Q nautobot_day2_sync --concurrency=10` | — | Celery worker | `nautobot_day2_sync` only |
| Onboarding wizard | `python3 nautobot_day2/onboarding/upload_app.py --port 8081` | **8081** | standalone Flask process | — |
| Agent Broker — REST | `python3 nautobot_day2/broker/api_server.py --port 8082` | **8082** | standalone Flask process | — |
| Agent Broker — MCP | `python3 nautobot_day2/broker/mcp_server.py` | **8090** (path `/mcp`) | standalone MCP server (streamable-http) | — |
| onboarding-mcp | `python3 nautobot_day2/onboarding_mcp/server.py` | **8091** (path `/mcp`) | standalone MCP server (streamable-http) | — |
| OpenBao | `bao server` (or the `openbao/openbao` image) | **8200** | standalone server | — |
| Postgres | — | 5432 (default) | container/managed DB | — |
| Redis | — | 6379 (default) | container/managed cache | — |
| ChatOps worker | loaded in-process by Nautobot via `nautobot.workers` entry point | — | inside Nautobot web/worker process | — |

## Configuration surface

| Setting | Where it's set | Consumed by |
|---|---|---|
| `PLUGINS = ["nautobot_day2"]` | `nautobot_config.py` | Nautobot App loader |
| `PLUGINS_CONFIG["nautobot_day2"]["tenants_dir"]` | `nautobot_config.py` | `tasks.py` (`_load_tenant_env`), onboarding scripts, wizard |
| `PLUGINS_CONFIG["nautobot_day2"]["max_concurrent_per_site"]` | `nautobot_config.py` | `tasks.py` → `concurrency.site_slot()` |
| `NAUTOBOT_URL`, `NAUTOBOT_TOKEN` | environment / `.env` | `NautobotClient` (every script, wizard, broker) |
| `BAO_ADDR`, `BAO_ROLE_ID`, `BAO_SECRET_ID` | environment | `openbao_client.fetch_openbao_secret` (read-only identity — sync engine, broker, wizard's live cred test) |
| `BAO_REFRESHER_ROLE_ID`, `BAO_REFRESHER_SECRET_ID` | environment | `openbao_client.update_rotated_credential` (write-scoped identity — wizard credential save, token rotation) |
| `VENDOR_COMMANDS_PATH` | environment (optional override) | `sync_network_data.py`, `preflight_check.py` — defaults to `vendor_commands/vendor_commands.yaml` |
| `NAUTOBOT_DAY2_LOG_LEVEL` | environment (optional) | `client.get_logger()` |
| Tenant credential `.env` files | `<tenants_dir>/<tenant-slug>.env` | fallback/legacy — see gaps doc |
| Tenant profile JSON | `<tenants_dir>/<tenant-slug>.json` (or `onboarding/profiles/` if unset) | wizard, ChatOps, `create_tenant.py`, `credential_checker.py` |
| `NAUTOBOT_REDIS_HOST`, `NAUTOBOT_REDIS_PASSWORD`, `ONBOARDING_MCP_REDIS_DB` | environment | `onboarding_mcp/session/state_machine.py`'s plain redis client — reuses the same `redis` service as Celery/Django cache, but a separate DB index (default `2`) for isolation |
| `FORTIGATE_NVA_BASE_URL`, `FORTIGATE_NVA_API_TOKEN` | environment (optional) | `shadow_ip/integrations/fortigate_client.py` — `ReconcileDeviceIPs` skips a tenant if unset |

## OpenBao path convention

```
kv/data/tenants/<tenant-slug>/<secrets-group-prefix>
```

e.g. `kv/data/tenants/acme-retail-ltd/aruba-ssh`,
`kv/data/tenants/acme-retail-ltd/juniper-mist-api`. The prefix comes from
`vendor_matrix.py`'s `secrets_group_prefix` field per vendor/device-type/
access-method combo (e.g. `aruba-ssh`, `juniper-mist-api`,
`aruba-central-api`, `fortinet-manager-api`, `cisco-fmc-api`,
`aruba-clearpass-api`).

## Package distribution (`pyproject.toml`)

| Extra | Installs | Needed for |
|---|---|---|
| (base) `pip install .` | requests, python-dotenv, PyYAML, tabulate, netmiko, Flask | The Nautobot App itself + onboarding scripts |
| `.[chatops]` | nautobot-chatops | ChatOps commands |
| `.[broker]` | nornir, nornir-netmiko, nornir-nautobot, mcp, redis | Agent Broker (both REST and MCP) + onboarding-mcp (same shared image/extra — see Dockerfile) |
| `.[dev]` | pytest, pytest-django | (declared; no tests exist yet in-repo — see gaps doc) |
