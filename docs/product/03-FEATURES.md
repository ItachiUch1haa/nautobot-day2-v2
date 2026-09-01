# Feature Catalog

Every capability, grouped by area. **Status** legend:

- **GA** — built, live-verified against a real Nautobot instance, in
  active use.
- **Beta** — built and functioning, but with a known gap that limits
  production readiness (noted inline; see `05-SECURITY-AND-COMPLIANCE.md`
  / `06-KNOWN-ISSUES-AND-RISKS.md` for detail).
- **Partial** — some of the capability exists; the rest is a known,
  tracked gap.

---

## 1. Onboarding

| Feature | Status | Notes |
|---|---|---|
| Web wizard — bulk tenant/site/device onboarding | **GA** | 6-step flow: tenant/site → device rows (table or CSV) → validate → credentials → live credential test → deploy. One site per run by design. |
| Conversational onboarding (`onboarding-mcp`) | **GA** | Same underlying pipeline as the wizard, exposed as MCP tools for an AI agent or an engineer working interactively, one device at a time. Brought to parity with the wizard this cycle — new-tenant and existing-tenant paths, new-site Location hierarchy building, and shadow-IP/VIP fields all live-verified. |
| ChatOps onboarding (Slack) | **GA** | `/nautobot onboard` (new site / check credentials / sync now), `/nautobot fill-creds <tenant>` for filling missing credentials via a private prompt. Calls the same onboarding functions directly — not a separate code path. |
| CSV-driven device intake with cross-row validation | **GA** | Duplicate-IP detection, stack/HA grouping, vendor/access-method matching, before anything gets written to Nautobot. |
| Live credential testing before deploy | **GA** | Real SSH login / API token check per vendor (Step 5 of the wizard) — not just "is the field non-empty." For shadow-IP-enabled sites, tests against the device's shadow IP (the only actually-reachable address), not its real IP. |
| Multi-vendor device onboarding | **GA** (partial vendor coverage) | Aruba (AOS-S/AOS-CX/AP/ClearPass), Juniper (Junos/SRX/Mist-managed AP), Cisco (IOS/IOS-XE/NX-OS/ASA/FTD), Fortinet (FortiOS/Switch/AP). See `06-KNOWN-ISSUES-AND-RISKS.md` for what's intentionally out of scope. |
| Controller-managed device onboarding (Meraki, Mist, Aruba Central) | **GA** | Devices discovered through the controller's own API rather than entered manually; `onboarding-mcp`'s controller adapters (`meraki_client.py`, `mist_client.py`, `aruba_central_client.py`) plus `DiscoverNewDevices`/`ReconcileDeviceIPs` for picking up devices/IPs that weren't known at onboarding time. |
| Idempotent re-runs | **GA** | Every onboarding step (`create_tenant.py`, `nautobot_onboard_v2.py`, `OnboardSite`) can be safely re-run against an already-onboarded tenant/site without duplicating objects. |

## 2. Day-2 Sync (keeping Nautobot accurate)

| Feature | Status | Notes |
|---|---|---|
| Scheduled/on-demand device fact sync | **GA** | Serial, firmware, interfaces, LLDP-derived neighbors pulled live from each device (SSH via Netmiko, or a vendor cloud API) and written back to Nautobot. |
| Per-site concurrency limiting | **GA** | Redis-backed distributed counter so a large sync never overwhelms one customer's network, regardless of total worker pool size. |
| Automatic LLDP-derived cabling | **GA** | Deferred to a batch callback after every device in a sync run finishes, specifically to avoid a race between two devices that are each other's neighbor. |
| Retry with backoff on transient failures | **GA** | Distinguishes transient (timeout, SSH error — retried) from terminal (auth failure — not retried) per device task. |
| Juniper Mist SSoT sync | **GA** | Independent path: pulls devices directly from a Mist org's cloud API, tags Mist's own site/device IDs as custom fields, one run per customer. |
| Dry-run / simulated sync mode | **Partial** | A `SIMULATED` flag exists, but as of this writing does **not** actually prevent live dispatch for any of the 15 currently-supported platforms — see `06-KNOWN-ISSUES-AND-RISKS.md`. Treat every sync run as live. |

## 3. Shadow IP / VIP Coverage (NAT-aware address tracking)

| Feature | Status | Notes |
|---|---|---|
| Real + shadow prefix pairing at site onboarding | **GA** | `OnboardSite` creates both prefixes together and links them via a custom field, at the same time the site itself is created — in either onboarding surface. |
| Automatic shadow-IP linking on device IP create/update | **GA** | A Job Hook (`CatalogShadowIP`) computes and assigns the shadow IP the moment a device's real IP is created or changed — no manual step, no separate sync run needed. Device `primary_ip4` always points at the reachable (shadow) address. |
| DHCP-lease drift correction | **GA** | `ReconcileDeviceIPs` catches a device's real IP changing (DHCP renewal, re-IP) and re-derives its shadow IP to match, on a schedule. |
| New-device discovery from live DHCP leases | **GA** | `DiscoverNewDevices` — for controller-managed devices (APs) that get an IP after onboarding, not at onboarding time. |
| Live FortiGate VIP object reconciliation | **GA** (needs live FortiGate reachability) | `ValidateVIPCoverage` compares Nautobot's shadow-prefix records against the FortiGate NVA's actual VIP configuration, flagging mismatches — requires `FORTIGATE_NVA_BASE_URL`/`FORTIGATE_NVA_API_TOKEN` to be configured; silently skips otherwise. |
| Offset-preserving shadow-IP math | **GA** | Pure arithmetic, independently unit-tested (`shadow_ip/test_shadow_math.py`, runs without a live Nautobot instance) — a real IP's offset from its prefix matches the shadow IP's offset from the shadow prefix, exactly mirroring how the NAT is actually configured on the firewall. |

## 4. Agent Broker (live, on-demand device access)

| Feature | Status | Notes |
|---|---|---|
| Device metadata lookup | **GA** | `GET /device/<name>` — Nautobot-sourced metadata, no credential fetch, no live device contact. |
| Live diagnostic command dispatch | **GA** | `POST /diagnose` (REST) / `run_command` (MCP) — resolves the device, fetches its credential from OpenBao, dispatches via Nornir, returns raw output. |
| MCP interface for AI agents | **GA** | Same underlying logic as the REST interface, exposed as `get_device_info`/`run_command` MCP tools — an AI agent gets identical capability to a human calling the REST API. |
| Command allowlist | **Not built** | No restriction on what command an agent or caller can request today — see `05-SECURITY-AND-COMPLIANCE.md` item 1 (highest-priority open item in this product). |
| Authentication on either interface | **Not built** | Neither the REST nor MCP interface authenticates the caller today — same priority-1 item. |
| Special-case handling for controller-only device types | **GA** | e.g. Fortinet APs have no independently reachable SSH server on real hardware — both the sync engine and the broker automatically redirect to the AP's controlling FortiGate. |

## 5. Credential Management

| Feature | Status | Notes |
|---|---|---|
| OpenBao (Vault-compatible, BSL-free) as the credential store | **GA** | KV v2, AppRole-authenticated, path convention `kv/data/tenants/<tenant-slug>/<secrets-group-prefix>`. |
| Read/write identity separation | **GA** | A read-only AppRole (`day2-sync-engine`) shared by the sync engine, broker, and wizard's live-test path; a separate write-scoped AppRole (`day2-credential-refresher`) used only for credential save/rotation — never the same identity for both operations. |
| Credential rotation | **Partial** | Per-field rotation exists and is used by the wizard's save/rotate flows; not yet a full automated per-secret rotation policy — see `06-KNOWN-ISSUES-AND-RISKS.md`. |
| Legacy `.env` fallback | **GA** (deprecated path) | Still written alongside OpenBao for compatibility; no live component reads from it as its authoritative source anymore. |
| Per-tenant credential isolation | **Beta** | Multi-server production topology scopes the Agent Broker per tenant; the single-server topology's OpenBao policies currently use one broad `tenants/*` wildcard across identities rather than a policy per tenant — a known, documented gap to close before real multi-tenant production use (`05-SECURITY-AND-COMPLIANCE.md` item 3). |

## 6. Platform / Operations

| Feature | Status | Notes |
|---|---|---|
| Single-server reference deployment (Docker Compose) | **GA** | All 9 services, one `docker compose up`, a fully documented 18-phase install walkthrough (`deploy/single-server/INSTALL.md`) — live-verified start to finish on a fresh Azure VM this cycle. |
| Multi-server production topology | **Documented** | `deploy/PRODUCTION_GUIDE.md` — per-tenant Agent Broker isolation, shared core infra. Not the topology currently running in this project's own staging/prod servers (both use the single-server stack today). |
| Staging → main → prod release workflow | **GA** | `docs/00-WORKFLOW.md` — CI on every push to `staging`, human validation gate against real lab devices, then merge to `main` (the production branch) and a scripted, backed-up prod deploy. |
| Health checks and monitoring guidance | **GA** | `docs/05-MONITORING.md` — what to watch per component, suggested log aggregation, alert priorities. |
| Automated tests | **Not built** | No automated test suite exists in-repo yet beyond the shadow-IP math's pure-Python unit tests — see `06-KNOWN-ISSUES-AND-RISKS.md`. |
