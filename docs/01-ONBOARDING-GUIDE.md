# Onboarding Guide — For Engineers

This is the step-by-step guide for an engineer onboarding a new customer
(tenant) or a new site into Nautobot using `nautobot_day2`. It covers the
recommended path (the web wizard), the alternative paths (ChatOps, CLI),
and what happens after devices go live.

For *why* the pipeline is shaped this way, see `02-COMPONENTS.md`. For
*where* each file/script/service lives, see `04-COMPONENT-PATHS.md`.

---

## 0. Before you start — one-time environment setup

These are one-time, per-environment steps, normally already done by
whoever stood up the stack (see `deploy/single-server/INSTALL.md` /
`deploy/PRODUCTION_GUIDE.md`). Confirm they're in place before onboarding
your first customer:

1. **Nautobot is running** with `nautobot_day2` in `PLUGINS`, and the Jobs
   list (Nautobot UI → **Jobs**) shows:
   - `Sync Network Data`
   - `Sync All Sites for Tenant`
   - `Juniper Mist: Sync Devices to Nautobot`
2. **A Celery worker is listening on `nautobot_day2_sync`** (in addition
   to `default`) — without this, sync Jobs will dispatch but nothing
   ever picks the tasks up.
3. **Base objects exist in Nautobot** — manufacturers, platforms, device
   roles, location types, service tags. These come from running
   `bootstrap_nautobot.py` once against a fresh Nautobot instance (Phase
   1). If Jobs run but every device create fails with "Platform not
   found" / "Role not found", this step was skipped.
4. **OpenBao is reachable** and the sync-engine (`BAO_ROLE_ID` /
   `BAO_SECRET_ID`) and refresher (`BAO_REFRESHER_ROLE_ID` /
   `BAO_REFRESHER_SECRET_ID`) AppRoles are configured in the environment
   Nautobot's web, worker, and the onboarding wizard all run in.
5. **The onboarding wizard is running** (`upload_app.py`, port 8081) and
   can reach both Nautobot and OpenBao.

If any of these aren't true yet, hand this off to whoever owns the
deployment (see `deploy/PRODUCTION_GUIDE.md`) before proceeding.

---

## 1. Onboarding a brand-new customer (tenant) — the web wizard path

The web wizard (`onboarding/upload_app.py`, served at
`http://<host>:8081`) is the current, complete, and recommended path. It
is a 6-step UI:

### Step 1 — Tenant & Site

- Enter the customer's name, tenant group, and industry vertical.
- Pick which **vendors** this customer uses (Aruba, Juniper, Cisco,
  Fortinet — whichever combination applies). This drives everything
  downstream: which credential fields you'll be asked for, which Nautobot
  objects get created, which sync logic applies per device.
- Enter the site's location details (region/country/state/city, site
  name, site type).
- Submitting this step calls `create_tenant.py`'s logic
  (`run_create_tenant()`) under the hood: creates the Tenant, a Namespace,
  the required Secrets Groups, External Integrations (for API-managed
  vendors), and an empty tenant `.env` file — then saves a **tenant
  profile JSON** (`<tenant-slug>.json`) that every later step reads back.

### Step 2 — Device Data

- Enter devices either through the interactive table or by uploading a
  CSV (`engineer_template.csv` is the reference format: `device_name,
  role, vendor, model, ip, site, tenant, serial, managed_by, status,
  namespace, stack_group`).
- Group **stacked switches** with a shared `stack_group` value (only the
  stack's commander/master row needs a management IP; use `vc_position`
  to set order explicitly, or leave it to row order).
- Group **firewall HA pairs** with a shared `ha_group` value (unlike a
  stack, every unit in an HA pair keeps its own IP; use `ha_priority` to
  set order).
- A device is a stack member or an HA member, **never both**.

### Step 3 — Validate

- The wizard cross-checks every row: unknown vendor/role, invalid or
  duplicate IPs, missing required fields, vendor/access-method
  mismatches, mixed-vendor stacks/HA groups (rejected — not supported),
  and derives the secrets group each device will use.
- Fortinet APs managed "via FortiGate" automatically inherit their site's
  firewall credentials — the wizard resolves this from either the current
  batch or, if the firewall was onboarded in an earlier session, from
  Nautobot's live inventory.
- Fix anything flagged `error` before continuing; `warn` rows can usually
  proceed but are worth a second look.

### Step 4 — Credentials

- The wizard shows exactly the credential fields this tenant's vendor
  selections require (derived live from `vendor_matrix.py` — never a
  hardcoded form).
- Submitted values are written to the tenant's `.env` file **and** pushed
  into OpenBao (the authoritative store going forward) under
  `kv/data/tenants/<tenant-slug>/<secrets-group-prefix>`.
- If a credential is a controller/API base URL, it's also patched onto
  the matching External Integration object in Nautobot automatically.
- Credential values are never echoed back, logged, or included in any
  error message.

### Step 5 — Test Credentials

- Runs a **real** connectivity test per vendor/access-method combination:
  a live SSH login for switches/firewalls, or a live token/API check for
  Mist / Aruba Central. This reads the just-saved values back from
  OpenBao (not the `.env` file), so it verifies exactly what the sync
  engine and broker will actually use.
- Don't skip this — a typo'd password here becomes a mystery `AUTH
  FAILURE` in tonight's scheduled sync otherwise.

### Step 6 — Deploy

- Creates the tenant's devices, IPs, and controllers in Nautobot (reusing
  `nautobot_onboard_v2.py`'s device-creation logic in-process).
- Triggers **"Sync Network Data"** for this specific site over Nautobot's
  own REST API (`POST /api/extras/jobs/<id>/run/`) — the same Job/Celery
  fan-out described in `03-ARCHITECTURE.md`, not a separate code path.
  This is scoped to the one site you just deployed, not the whole tenant.
- Watch the Job's log in the Nautobot UI (**Jobs → Job Results**) for the
  dispatch confirmation, then the summary line once every device task
  finishes (`✅ N ❌ M of T devices | interfaces:X cables:Y`).

---

## 2. Adding a new site to an *existing* tenant

Same wizard, same 6 steps — Step 1 picks the existing tenant instead of
creating a new one. Credentials from Step 1's original onboarding are
reused unless the new site needs a vendor/access-method that tenant
hasn't used before, in which case Step 4 will ask only for the new
fields.

## 3. Onboarding via ChatOps (Slack)

Once a tenant profile already exists (created via the wizard or CLI —
ChatOps deliberately doesn't do the multi-select vendor picker), an
engineer can drive most of the same flow from Slack:

- `/nautobot onboard` → menu: **new site** / **check credentials** /
  **sync now**.
- `/nautobot onboard check <tenant>` — reports which credential
  variables are still missing/empty for that tenant.
- `/nautobot onboard site <tenant> <site-name>` — onboards devices from an
  already-validated `nautobot_ready_<site>.csv` (produced by the wizard
  or `nautobot_prepare.py`), then triggers a sync for that site.
- `/nautobot onboard sync <tenant> <site|ALL> <category>` — triggers
  `SyncNetworkData` or `SyncAllSites` directly.
- `/nautobot fill-creds <tenant>` — fills in missing credentials one
  variable at a time through a private (ephemeral) prompt — nothing is
  posted to the channel.

## 4. Onboarding via CLI (advanced / lab use only)

`onboard_cli.py` runs the same phases from a terminal instead of a
browser — useful for scripted/lab testing, **not** the recommended
production path:

```bash
python3 nautobot_day2/onboarding/onboard_cli.py --profile profiles/acme-retail.json
python3 nautobot_day2/onboarding/onboard_cli.py --tenant acme-retail-ltd --site Acme-BLR-03
python3 nautobot_day2/onboarding/onboard_cli.py --dry-run
```

**Known limitation:** `onboard_cli.py`'s sync step still calls
`sync_network_data.py` directly and sequentially — it has **not** been
updated to dispatch through the Job/Celery pipeline the way the wizard's
Step 6 does. Prefer the wizard for anything beyond quick CLI testing.

Individual phase scripts can also be run standalone (each has its own
`--help`):

```bash
python3 nautobot_day2/onboarding/bootstrap_nautobot.py --dry-run   # Phase 1 — base objects
python3 nautobot_day2/onboarding/preflight_check.py                # Phase 2 — health check
python3 nautobot_day2/onboarding/create_tenant.py --profile profiles/acme-retail.json --dry-run   # Phase 3
python3 nautobot_day2/onboarding/nautobot_onboard_v2.py --csv tests/test_site.csv --dry-run        # Phase 5
python3 nautobot_day2/onboarding/sync_network_data.py --site Acme-BLR-01 --tenant acme-retail-ltd  # Phase 6
```

## 5. First sync on a new environment — do this safely

`sync_network_data.py` has a global `SIMULATED` flag (and a
per-platform `SIMULATED_OVERRIDE` map). On a brand-new environment, leave
it `True` for the first run: it exercises the full dispatch → parallel
Celery tasks → summary-log round trip with fake command output, so you
can confirm the pipeline works before touching a real device. Flip it
off only after that dry round-trip is confirmed, and only per-vendor once
that vendor's real hardware is ready.

## 6. Day-2 operations — after a customer is live

- **Scheduled sync**: run `Sync All Sites for Tenant` on a schedule
  (Nautobot's own Job scheduler) for nightly refresh of serials,
  firmware, interfaces, and LLDP-derived cabling.
- **On-demand sync**: trigger `Sync Network Data` for one site any time
  from the Jobs UI, ChatOps, or the wizard's re-deploy path.
- **Credential rotation**: use the wizard's Step 4 (re-save) or
  `/nautobot fill-creds` — both write through to OpenBao. Never hand-edit
  the tenant `.env` file on a running worker unless you understand it's a
  fallback artifact, not the source of truth.
- **Troubleshooting a live device**: this goes through the **Agent
  Broker**, not the sync pipeline — see `03-ARCHITECTURE.md` §3 and the
  security note in `06-GAPS-AND-RECOMMENDATIONS.md` before pointing any
  external agent at it.

## 7. Common failure points and where to look

| Symptom | Likely cause | Where to check |
|---|---|---|
| Job doesn't appear in Jobs UI | App failed to load / not registered | `docker compose logs nautobot` (or web process log) for an import error |
| Device create fails: "Platform/Role not found" | `bootstrap_nautobot.py` (Phase 1) never ran | Re-run it against this environment |
| Sync Job says "Dispatched" but nothing happens | No worker listening on `nautobot_day2_sync` queue | `nautobot-server celery worker -Q default,nautobot_day2_sync` |
| `AUTH_FAILURE` on real sync after wizard said credentials were fine | Credential changed after Step 5's test, or wrong OpenBao path | Re-run Step 5, check `openbao_client.fetch_openbao_secret` path (`tenants/<slug>/<prefix>`) |
| One customer's sync starves another's | No priority queues yet (see gaps doc) | Check per-site concurrency (`max_concurrent_per_site`), consider a dedicated worker pool |
| A device never gets cabled to its neighbor | Cable creation happens once *per batch*, after every device task finishes | Check `sync_summary_callback` ran; a device synced outside its batch's chord won't get its cables until the next full-site sync |
