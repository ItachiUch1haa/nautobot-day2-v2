# Components — What's Responsible for What

`nautobot_day2` is **one installable Nautobot App** plus several
standalone processes that share its codebase. This doc groups every
component by the function it performs, independent of file layout (see
`04-COMPONENT-PATHS.md` for exact paths).

---

## 1. Nautobot core (the system of record)

| Component | Responsibility |
|---|---|
| **Nautobot web** | Source of truth for every device, tenant, site/location, IP, platform, role, secrets group, external integration. Everything else reads from or writes to it via its REST API. |
| **Postgres** | Nautobot's database. |
| **Redis** | Nautobot's cache backend, Celery broker/result backend, and the backing store for the per-site concurrency counters (`nautobot_day2.concurrency`). |
| **`nautobot_day2` App config** (`__init__.py`) | Registers the App with Nautobot; declares plugin settings: `tenants_dir` (shared credential `.env` storage path) and `max_concurrent_per_site`. |

## 2. Shared library code (used by everything else)

| Component | Responsibility |
|---|---|
| **`client.py` — `NautobotClient`** | The one place that knows how to talk to Nautobot's REST API: auth headers, retry/backoff on 5xx and connection errors, automatic pagination, find-or-create helpers. Every onboarding script, the sync engine, and the broker all import this instead of hand-rolling their own HTTP calls. |
| **`openbao_client.py`** | The one place that knows how to talk to OpenBao: AppRole login (fresh every call, no token caching), KV v2 read (`fetch_openbao_secret`), and a separate write-scoped merge-update (`update_rotated_credential`) used only for credential rotation. Shared by the sync engine, the broker, and the wizard's credential-save/rotate paths. |
| **`vendor_matrix.py`** | Single source of truth for every supported vendor / device-type / access-method combination — platforms, NAPALM drivers, required env vars, secrets-group prefixes, which sync handler applies. Every other component (wizard dropdowns, tenant creation, credential derivation, sync dispatch) reads from here instead of hardcoding vendor logic. |
| **`vendor_commands/vendor_commands.yaml`** | The actual SSH command strings / API endpoint paths per vendor+platform, parsed by regex/JSON extractors, and consumed by `sync_network_data.py` and the broker's dispatch path. Adding a new vendor's commands means editing this file, not Python code. |
| **`concurrency.py`** | Per-site concurrency guard (`site_slot`) — a Redis-backed distributed counter so no more than `max_concurrent_per_site` device-sync tasks run against one site at once, regardless of total worker pool size. Includes a TTL safety valve in case a task dies without releasing its slot. |

## 3. Onboarding pipeline (bulk create + validate)

| Component | Responsibility |
|---|---|
| **`bootstrap_nautobot.py`** (Phase 1) | One-time, idempotent setup of Nautobot's base objects: manufacturers, platforms, device roles, location-type hierarchy, service tags, the `industry_vertical` custom field. Reads its list of manufacturers/platforms from `vendor_matrix.py`. |
| **`preflight_check.py`** (Phase 2) | Read-only health check — confirms required scripts exist, Nautobot is reachable, required services are up, before an engineer starts onboarding. |
| **`create_tenant.py`** (Phase 3) | Creates only what a specific tenant needs: the Tenant record, Namespace, Secrets Groups, External Integrations, and an empty `.env` file — driven by a **tenant profile JSON** (vendor/device-type/access-method selections). Idempotent. Exposes `run_create_tenant()` as a programmatic entry point (used by the wizard) as well as a CLI. |
| **`nautobot_prepare.py`** | Normalizes and enriches raw device-data input (vendor/role/platform normalization, IP validation, access-method validation, secrets-group derivation) into a "ready" CSV shape. The wizard's `_validate_rows()` reimplements/extends this logic for its interactive table + CSV upload paths. |
| **`nautobot_onboard_v2.py`** (Phase 5) | Creates the actual devices in Nautobot from a `nautobot_ready_<site>.csv`: full location hierarchy (Region→Country→State→City→Site), devices, IPs, VirtualChassis (stacked switches), DeviceRedundancyGroup (firewall HA pairs), and links controllers to their External Integrations. Idempotent. Exposes `process_csv()`, called directly (in-process) by the wizard's deploy step. |
| **`credential_checker.py`** | Verifies every credential env var a tenant's vendor selections require is present and non-empty in that tenant's `.env` file. Used by both the CLI (`--list-tenants`, exit-code driven) and ChatOps' `/nautobot onboard check`. |
| **`vendor_test.py` / `vendor_test_app.py`** | Live connectivity test functions (`test_ssh`, `test_mist`, `test_aruba_central`) — a real SSH login or API token check, not just "is the field non-empty." Reused by the wizard's Step 5 (`/api/validate-credentials`) and available standalone via `vendor_test_app.py`'s own small Flask app. |
| **`onboard_cli.py`** | Older, terminal-based orchestrator running the same phases sequentially from a shell. Its sync step calls `sync_network_data.py` directly rather than dispatching through the Job/Celery pipeline — see the gaps doc. |

## 4. Web onboarding wizard

| Component | Responsibility |
|---|---|
| **`onboarding/upload_app.py`** (Flask, port 8081) | The current, complete onboarding UI: 6-step wizard (tenant/site → device data → validate → credentials → test credentials → deploy). Orchestrates calls into `create_tenant.py`, `nautobot_prepare.py`'s normalization logic, `nautobot_onboard_v2.py`, `vendor_test_app.py`, and both clients (`NautobotClient`, `openbao_client`) — it does not duplicate their logic. |
| **`onboarding/templates/index.html`** | The wizard's single-page frontend — plain HTML/JS driving the `/api/*` routes above. |

## 5. Day-2 sync engine (scheduled/on-demand data pull)

| Component | Responsibility |
|---|---|
| **`onboarding/sync_network_data.py`** | The actual sync logic: resolves a device's vendor/platform/role/secrets-group to a `vendor_commands.yaml` block, dispatches SSH (Netmiko) or a vendor cloud API call, parses the output, and writes facts (serial, firmware, interfaces, LLDP-derived neighbor data) back to Nautobot. Has a `SIMULATED` flag for dry-testing the whole pipeline without touching real hardware. |
| **`jobs/sync_network_data_job.py`** — `SyncNetworkData`, `SyncAllSites` | Nautobot Jobs (UI/scheduler-triggered entry points). Resolve the device list for a site or a whole tenant, then **fan out**: dispatch one Celery task per device to the `nautobot_day2_sync` queue via a `chord`, and return immediately — they report "dispatched," not "complete." |
| **`tasks.py`** — `sync_device_task`, `sync_summary_callback` | The Celery fan-out unit. `sync_device_task` syncs exactly one device (inside a per-site concurrency slot), retrying transient SSH/API failures with backoff and giving up permanently on auth failures. `sync_summary_callback` is the chord callback: runs once every device task in a batch finishes, creates LLDP-derived cables (batched, so it never races a still-syncing neighbor), and appends one summary log entry to the dispatching Job's result. |
| **`jobs/mist_sync.py`** — `MistSyncJob` | A separate, independent sync path: pulls devices directly from a Juniper Mist org via its cloud API and creates/updates them in Nautobot (SSoT-style), tagging Mist's own site/device IDs as custom fields. Multi-tenant: one run per customer. |

## 6. Agent Broker (ad-hoc, on-demand device access)

| Component | Responsibility |
|---|---|
| **`broker/core.py`** | The shared implementation both broker interfaces call: look up a device in Nautobot (`get_device_context`) → resolve its secrets group to an OpenBao path → fetch the credential → resolve vendor/platform to a `vendor_commands.yaml` block → dispatch via Nornir (Netmiko for SSH, `requests` for API-managed vendors) → return raw output (`run_diagnostic_command`). This is the *only* code path that ever authenticates to a live device on behalf of an agent. |
| **`broker/api_server.py`** (Flask, port 8082) | REST wrapper over `core.py`: `GET /device/<name>` (metadata only, no credential fetch), `POST /diagnose` (full dispatch), `GET /health`. |
| **`broker/mcp_server.py`** (port 8090, streamable-http) | MCP wrapper over the same `core.py` functions, exposed as two MCP tools: `get_device_info`, `run_command`. Lets an MCP-speaking AI agent call the exact same logic the REST interface uses. |

**Explicit, documented gap**: neither interface currently enforces a
command allowlist or authentication — see `06-GAPS-AND-RECOMMENDATIONS.md`.

## 7. ChatOps (Slack today, Teams later)

| Component | Responsibility |
|---|---|
| **`chatops/worker.py`** | `/nautobot onboard` (menu: new site / check credentials / sync now) and `/nautobot fill-creds <tenant>` (fills missing credentials one at a time via a private, ephemeral prompt). Calls the same onboarding functions and Django ORM Nautobot itself uses — not a separate code path, not a shell-out to scripts. Registers automatically via the `nautobot.workers` entry point in `pyproject.toml`. |

## 8. Credential store

| Component | Responsibility |
|---|---|
| **OpenBao server** | The open-source (Linux Foundation governed) fork of HashiCorp Vault, used specifically to avoid Vault's BSL license. Stores every tenant's device credentials as KV v2 secrets under `tenants/<tenant-slug>/<secrets-group-prefix>`. Authenticated via AppRole — a read-only identity (sync engine, broker) and a separate write-scoped identity (`day2-credential-refresher`, used only for rotation/save flows) — never the same identity for both. |
| **Tenant `.env` files** (`tenants_dir`) | Legacy/fallback credential storage, still written alongside OpenBao for compatibility, but no longer the authoritative source — every live sync, broker call, and credential test reads from OpenBao. |
