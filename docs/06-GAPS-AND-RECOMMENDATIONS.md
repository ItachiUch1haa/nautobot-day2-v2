# Gaps & Additional Recommendations — Not Explicitly Asked For, But Worth Knowing

This doc covers what wasn't in the original documentation request but is
directly relevant to running this pipeline safely and reliably. Most of
this is already flagged in the codebase itself (README, `core.py`
docstrings, `deploy/PRODUCTION_GUIDE.md`) — this consolidates it in one
place, at the front of an engineer's attention rather than buried in
individual files.

---

## 1. Security — the Agent Broker has no gate at all (highest priority)

Confirmed directly in `broker/core.py`'s own docstring: **"No command
allowlist, no restricted-account enforcement... any command string is
accepted."** Neither `api_server.py` (port 8082) nor `mcp_server.py`
(port 8090) has any authentication. Anything that can reach either port
can run **any** command — including destructive ones (`reload`, `write
erase`, config-mode changes) — against any device it can resolve, using
a real fetched credential.

**Before this is exposed beyond a trusted internal network:**
- Add a per-vendor, pattern-based **read-only** command allowlist (match
  verbs like `show`/`display`/`get`/`ping` per vendor grammar, not an
  exact-string list), with explicit exclusions for read-only commands
  that still leak secrets (`show running-config`) or are
  expensive/disruptive (`show tech-support`).
- Add authentication to both interfaces.
- Add an audit log of every command attempted, allowed or not (today,
  nothing distinguishes "an agent ran `show version`" from "an agent ran
  `reload`" in any persisted record).
- Add a human-approval escalation path for anything that doesn't match
  the safe pattern, instead of a silent dead end.

This is called out as the top production blocker in
`deploy/PRODUCTION_GUIDE.md` §6 and in the main README — treat it as
such; the rest of this list matters less if this one item isn't done
before any external-agent or multi-tenant exposure.

## 2. Security — TLS is disabled by default

The single-server `docker-compose.yml` sets `tls_disable = true` for
OpenBao and runs Nautobot with `--insecure`. This is explicitly
documented as test-only, but it's an easy thing to accidentally
lift-and-shift into a real deployment. Confirm TLS is enabled end-to-end
(Nautobot, OpenBao, and ideally the wizard/broker too) before anything
touches a real customer network.

## 3. Security — per-tenant credential isolation is a design intent, not yet an enforced control

The production guide describes per-tenant OpenBao AppRole scoping
(`tenants/<slug>/*` read-only, no cross-tenant wildcard) as required
setup — but this is a manual runbook step per new customer (§4.2 in
`PRODUCTION_GUIDE.md`), not something the code enforces or validates.
Nothing currently checks that a given broker instance's AppRole is
correctly scoped to only its intended tenant. Worth a periodic automated
audit (e.g. attempt a read against a *different* tenant's KV path with
each broker's credentials and alert if it ever succeeds) rather than
trusting the runbook was followed correctly every time.

## 4. No automated tests exist anywhere in this repo

`pyproject.toml` declares a `dev` extra (`pytest`, `pytest-django`) but
there are no test files in the repository. Given the amount of
non-trivial logic already here — credential brokering, cable-creation
race avoidance, stack/HA grouping validation, per-site concurrency,
platform-slug collision detection — this is real regression risk as more
engineers touch the code in parallel. This is explicitly flagged in
`PRODUCTION_GUIDE.md` §6 as a pre-production blocker, not just a nice-to-have.
Recommended minimum before scaling the team touching this code:
- Unit tests for `vendor_matrix.py`'s derivation functions (secrets-group
  prefix, env var lists, platform-slug uniqueness) — these silently
  corrupted onboarding for months once already (see the
  `validate_platform_slug_uniqueness()` comment in `vendor_matrix.py`).
- Unit tests for the wizard's `_validate_rows()` stack/HA
  grouping logic.
- Integration test for the full dispatch chain in `SIMULATED` mode
  (already built for manual testing — worth wiring into CI).

## 5. `onboard_cli.py` is on a different code path than the wizard

`onboard_cli.py`'s sync step calls `sync_network_data.py` directly and
sequentially — it was never updated to dispatch through the Job/Celery
fan-out the wizard's Step 6 uses. Anyone using the CLI path for anything
beyond quick lab testing will get different (slower, non-parallel,
no-retry) sync behavior than what the wizard produces, without any
warning at the point of use beyond the README's note. Recommend either
reconciling it onto the same Job-trigger call the wizard makes, or adding
a runtime warning in the script itself.

## 6. No noisy-neighbor protection across tenants — only within a site

`max_concurrent_per_site` caps concurrent syncs *per site*, but there's
no equivalent cap or priority mechanism *across tenants* sharing the same
worker pool. A very large tenant's `SyncAllSites` run can crowd out a
smaller tenant's on-demand sync simply by queue ordering, with no
priority queue to prevent it. `PRODUCTION_GUIDE.md` §5.4 already flags
this as the next scaling point ("that's the point to introduce priority
queues, not built yet") — worth planning for before it becomes a live
customer complaint rather than after.

## 7. Credential rotation is per-field, not per-secret, and partially manual

`update_rotated_credential()` does a read-merge-write so unrelated fields
in the same OpenBao secret survive a rotation — good. But there's no
automatic rotation *schedule* anywhere in the codebase; rotation only
happens when an engineer re-saves a value through the wizard, or when a
vendor's own OAuth2 refresh-token flow calls it internally (Aruba
Central). Long-lived static credentials (most SSH username/password
pairs) have no rotation path at all beyond a manual wizard re-save.
Worth deciding, per compliance requirements, whether SSH credentials need
a forced rotation cadence and if so building the automation for it.

## 8. Tenant `.env` files are a lingering dual-write, not fully deprecated

The README and code comments describe OpenBao as "authoritative," but
the `.env` file mechanism is still actively written to (not just left in
place for backward reads) on every credential save. This dual-write is a
drift risk — two independent writes to two stores means it's possible
for the `.env` file and OpenBao to disagree if one write succeeds and the
other doesn't (the wizard *does* surface OpenBao write failures to the
caller, which helps, but doesn't prevent the `.env` file being on-disk
proof of a credential value even in a supposedly OpenBao-only world).
Worth a deliberate decision: either fully retire the `.env` write path,
or explicitly document it as a permanent dual-write with a defined
reconciliation process.

## 9. Backup/DR is out of scope of everything reviewed here

None of the reviewed docs (README, `PRODUCTION_GUIDE.md`, `INSTALL.md`)
mention backup strategy for Postgres (Nautobot's entire data model),
OpenBao's storage backend (every customer's live credentials), or the
tenant profile JSON files (which aren't reproducible from anywhere else
if lost — they're the only record of a tenant's original vendor
selections). Recommend explicit backup + restore-drill procedures for
all three before production use, especially OpenBao, where data loss
means re-collecting live credentials from every customer.

## 10. Multi-vendor coverage is intentionally partial — know the boundaries

`vendor_matrix.py` has `enabled: False` entries for Cisco AP management
(DNAC and WLC-SSH) — deferred, not broken. Anyone onboarding a customer
with Cisco wireless will hit "not implemented" at the credential-test
step, by design, not as a bug. Worth surfacing this in whatever
pre-sales/scoping conversation happens before a customer is committed to
onboarding, so it isn't discovered mid-onboarding.

## 11. `SIMULATED_OVERRIDE` is a live production lever, not just test config

`sync_network_data.py`'s per-platform `SIMULATED_OVERRIDE` map controls
whether that vendor/platform actually touches real hardware. As of this
review every entry is set to `False` (i.e., not simulated — real
dispatch), but this is a plain Python module-level dict, not an
environment-driven config — flipping it back to simulated for one vendor
during an incident requires a code change and redeploy, not a config
toggle. Consider promoting this to an environment-variable-driven
override if "temporarily fake this one vendor's sync without a
deploy" ever becomes an operational need.

## 12. Async sync Jobs never update their own JobResult status — always shows PENDING

`SyncNetworkData` and `SyncAllSites`'s `run()` methods return immediately
after dispatching device sync tasks to Celery (deliberately — they don't
block waiting on potentially hundreds of devices). The real per-device
work happens in `sync_device_task`, and `sync_summary_callback` (a Celery
chord callback) writes a summary line into the JobResult's *log* once
every device task in the batch finishes.

**What's missing: nothing ever updates the JobResult's `status` field
itself.** Verified directly — a job whose devices had all long since
finished (confirmed via `sync_summary_callback` succeeding in the worker
logs, with a summary log entry present in the JobResult's own log) still
showed `Pending` in the Job Results list days later, with no indication
in the UI that anything had actually happened. `sync_summary_callback`
already receives `job_result_id` and could set
`job_result.status = <success|failure>` (plus `date_done`) once it has
the final results, but currently doesn't.

Practically: nothing is stuck or leaking resources (Celery's own `inspect
active` correctly shows empty once work is done), so this isn't a
performance or latency problem. It's an observability gap — anyone
relying on the Job Results list (or the wizard's Deploy step, which links
to this same page) to know whether a sync succeeded will see `Pending`
forever and have to cross-reference the Job's own log entries or the
Celery worker's logs directly to find out. Worth fixing by having
`sync_summary_callback` explicitly close out the JobResult's status once
the chord completes, rather than leaving it to whatever Nautobot's
own job-runner set at dispatch time.

## 13. `onboarding-mcp` has no authentication yet — higher exposure than the broker

Same category of gap as item 1 above, carried forward explicitly rather
than left as just a comment in a spec doc (per that spec's own
architecture doc §11): `onboarding_mcp/server.py` (port 8091) has no
authentication on any of its 11 MCP tools. This is a **strictly bigger**
exposure than the Agent Broker's missing auth — the broker only reads
device state and runs diagnostic commands with an existing credential;
onboarding-mcp **writes new credentials to OpenBao and creates real
Nautobot objects** (tenants, sites, devices, IP addresses) on behalf of
whoever can reach it. Do not expose port 8091 beyond this box's own
trusted network (INSTALL.md Phase 12a/13 do not open it), and treat
adding real authentication here as a precondition for any exposure
beyond that, not a follow-up.

## 14. VIP coverage reconciliation (`ValidateVIPCoverage`, `DiscoverNewDevices`) has open decisions its own spec calls out — not resolved in code

Added per `NautobotVIPManagementArchitecture.md` §6.3/§6.5, extending the
existing `shadow_ip/` package rather than a parallel implementation (its
own instruction #2). Both jobs are **untested against a real FortiGate**
(same `PENDING LIVE VERIFICATION` status as the rest of `shadow_ip/` —
`get_vip()`'s `mappedip` response-shape parsing in particular needs
confirming against the live NVA, per that doc's own §6.6 caveat). Beyond
live verification, that doc's §8 leaves several decisions explicitly open
and deliberately unresolved here rather than picked unilaterally:
- **§8.1** — `ValidateVIPCoverage` mismatches (which likely mean a
  customer is currently unreachable) only go to the job log today, not a
  ticket/alert channel. Worth wiring to whatever paging/ticketing this
  platform already uses before relying on it operationally.
- **§8.2** — no retention/hard-delete policy exists yet for `Deprecated`
  shadow IPs produced by `ReconcileDeviceIPs`/`CatalogShadowIP`'s update
  path; they accumulate indefinitely as-is.
- **§8.6** — `DiscoverNewDevices` only ever logs a warning for an
  unrecognized MAC; no auto-promotion to a full Device record, by design,
  for the reasons in the job's own docstring. Confirm this stays the
  policy before anyone builds an auto-promotion path on top of it.
- **§8.5** — FortiOS API token scope (per-VDOM vs. global-admin) and where
  it should be vaulted (OpenBao, following this repo's existing credential
  pattern, is the natural fit but isn't wired up) is still unconfirmed;
  `FORTIGATE_NVA_API_TOKEN` is a single shared env var today, not
  per-tenant-scoped like the broker's OpenBao AppRoles (see item 3 above —
  same category of risk).

Also worth flagging: this codebase's actual customer-namespace naming
convention is the tenant **slug** (`create_tenant.py::create_namespace()`),
not the `Customer-<Letter>` example naming in that architecture doc's §1/§3.1
allocation table — code here filters by `.exclude(namespace__name="Global")`
rather than the doc's literal `namespace__name__startswith="Customer"`,
which would silently match nothing against real tenant namespaces.

## 15. Shadow IP/VIP onboarding is now wired into both onboarding surfaces, with one real bug fixed and one still open

`shadow_ip/site_onboarding.py::onboard_site()` is now called from both
`onboarding_mcp/tools_schema.py::set_site()` (conversational) and
`onboarding/upload_app.py`'s `/api/deploy` (web wizard) — both now accept
the optional `fortigate_vdom`/`fortigate_vip_name`/`fortigate_tunnel_name`
fields added in the prior VIP Management pass.

**Fixed while wiring this in**: `onboard_site()`'s Location lookup
(`Location.objects.get(name=site_name, parent__name=customer_ns_name)`)
could never match anything this codebase actually creates — this
codebase's real Location model is a 5-level Region → Country → State →
City → Site chain (`bootstrap_nautobot.py`'s `LOCATION_TYPES`,
`nautobot_onboard_v2.build_location_hierarchy()`), with tenancy expressed
via each Location's own `tenant` field, not via a tenant-named parent as
the architecture doc's §3.2 assumed. Now looks up by name only, matching
the name-only Location lookup convention already used elsewhere in this
codebase (`upload_app.py::_find_live_firewall_sg()`,
onboarding_mcp's existing-site `set_site` path). This was a real,
pre-existing bug — `set_site(mode="new")` in onboarding_mcp could never
have succeeded against this codebase's actual Location model before this
fix, regardless of the VIP fields.

**Still open, not resolved unilaterally**: `onboarding_mcp`'s 11-tool
schema (architecture doc §4) never collects Region/Country/State/City, so
it has no way to create a new site's Location itself the way the web
wizard's `build_location_hierarchy()` does (now called explicitly by
`/api/deploy` before triggering `OnboardSite`, so the web wizard's path
works end to end). `set_site(mode="new")` in onboarding_mcp will still
fail — cleanly, with a clear error surfaced through `ToolError`, not
silently — for a site whose Location doesn't already exist. Two ways to
close this, needing a product decision rather than a guess:
(a) extend onboarding_mcp's schema to also collect the geo hierarchy, or
(b) treat onboarding_mcp's "new site" as "new to shadow-IP tracking" only,
requiring the site's Location to already exist (e.g. created via the web
wizard first) before a conversational session can onboard its shadow IP.
