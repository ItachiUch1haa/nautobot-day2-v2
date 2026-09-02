# Known Issues & Risks

Non-security operational risks and open questions, condensed to table
form. Security-specific items (authentication, credential isolation,
TLS, OpenBao recovery) live in `05-SECURITY-AND-COMPLIANCE.md` instead —
this page is everything else. Full technical detail for every item:
`docs/06-GAPS-AND-RECOMMENDATIONS.md`.

## Open items

| # | Item | Impact | Notes |
|---|---|---|---|
| 1 | No automated tests exist in-repo | Regression risk as more engineers touch credential brokering, cable-creation race logic, concurrency, and platform-slug derivation in parallel | Flagged as a pre-production blocker in `deploy/PRODUCTION_GUIDE.md` §6. The shadow-IP math module is the one exception — it has pure-Python unit tests that run without a live Nautobot instance. |
| 2 | `onboard_cli.py` (terminal onboarding path) diverges from the wizard's sync behavior | Anyone using the CLI path beyond quick lab testing gets slower, non-parallel, no-retry sync — silently, with no runtime warning | Recommend reconciling it onto the same Job-trigger call the wizard/`onboarding-mcp` use, or adding an explicit warning at the point of use. |
| 3 | No cross-tenant concurrency protection — only per-site | A very large tenant's full-tenant sync can crowd out a smaller tenant's on-demand sync via plain queue ordering, no priority mechanism | Already flagged as "the next scaling point, not built yet" in the production guide. Worth planning for before it surfaces as a customer complaint. |
| 4 | Vendor coverage is intentionally partial | Onboarding a customer with Cisco wireless (DNAC/WLC-SSH) hits "not implemented" at the credential-test step, by design | Not a bug — `vendor_matrix.py` has these explicitly `enabled: False`. Worth surfacing in pre-sales/scoping conversations before a customer commits, so it isn't discovered mid-onboarding. |
| 5 | `SIMULATED` dry-run mode does not actually prevent live dispatch | Every currently-supported vendor/platform is hardcoded to live dispatch regardless of the global flag — confirmed live via a real SSH connection attempt against an unreachable test IP | Treat every sync run as live, always. If "safely fake this one vendor's sync without a deploy" ever becomes an operational need, this needs to become an environment-driven override rather than a module-level dict. |
| 6 | Async sync Jobs' `JobResult.status` never updates — always shows `Pending` | Anyone relying on the Job Results list (including the wizard's Deploy step, which links to it) to know whether a sync succeeded sees `Pending` forever, with no UI signal that anything happened | **Confirmed deployment-wide, not specific to this codebase's own jobs** — a plain synchronous Nautobot Job (`OnboardSite`) shows the identical symptom. `job_log_entries` *are* reliably persisted, though — both onboarding surfaces now poll those instead of `.status` for exactly this reason. Worth investigating at the Nautobot/Celery-result-backend level on this deployment. |
| 7 | VIP coverage reconciliation has open policy decisions its own spec left unresolved | `ValidateVIPCoverage` mismatches (likely meaning a customer is currently unreachable) only reach the job log today, not a ticket/alert channel; no retention policy for `Deprecated` shadow IPs (they accumulate indefinitely); no auto-promotion path for unrecognized MACs (by design, pending confirmation this stays policy) | See `docs/06-GAPS-AND-RECOMMENDATIONS.md` §14 for the full list of open questions — these are deliberate "ask before deciding" items, not oversights. |
| 8 | `ValidateVIPCoverage`/`ReconcileDeviceIPs` are untested against a real FortiGate NVA | Response-shape parsing (`get_vip()`'s `mappedip` field in particular) is implemented against the documented FortiOS API shape but not yet confirmed live | Needs a live FortiGate NVA validation pass before relying on these two jobs operationally. |
| 9 | This Nautobot version has no `object`/`multi-object` custom field type | All shadow-IP/VIP custom fields (`nat_shadow_prefix`, `mapped_shadow_ip`, `managing_controller`) store a target object's UUID as plain text instead of a native clickable reference | Functionally identical for every consumer in this codebase (all reads/writes go through the Django ORM directly, never the REST API's object-type serialization) — the only loss is Nautobot's built-in clickable-reference UI. Worth switching back if/when this environment upgrades past the Nautobot version that first ships these field types. |

## Recently found and fixed (worth knowing existed)

A cluster of real, live-verified bugs surfaced this cycle while bringing
`onboarding-mcp` to parity with the web wizard and validating the
shadow-IP feature end to end — all fixed and confirmed working, listed
here because the underlying Nautobot-version quirks they reveal are
worth knowing about for any future code touching this same surface area
(full technical detail: `docs/06-GAPS-AND-RECOMMENDATIONS.md` §16–17):

- Several Nautobot ORM quirks specific to this deployment's version:
  `PrefixQuerySet` has no `net_overlap` (use `net_contains_or_equals`/
  `net_contained_or_equal` instead); `Prefix`/`IPAddress.status` is a
  required field with no default; `custom_field_data` is a Python
  property, not a real queryable Django field (`_custom_field_data` is
  the real name for filtering; it can't be set via `get_or_create()`'s
  `defaults=` at all); a custom field's `key` (not `name`) is what
  actually determines its identifier.
- `JobHookReceiver.receive_job_hook()`'s real signature on this version
  differs from what an earlier spec assumed (`change`, not
  `change_context`; `snapshots` needs a default — Nautobot doesn't
  always pass it).
- `IPAddress` has no public `namespace` attribute on this Nautobot
  version at all — only reachable via `.parent.namespace`.
- A REST API response's nested `namespace` reference on a `Prefix` has
  no `name` field, only `{id, url, object_type}` — any code comparing by
  name instead of id silently gets `None`.
- A genuine race condition between a device's own onboarding code and
  the `CatalogShadowIP` Job Hook's independent, asynchronous attempt to
  link the same device's shadow IP — the hook fires the instant the real
  IP is created, before any of the onboarding code's own follow-up REST
  calls (interface link, `primary_ip4` patch) even start, so its device
  lookup finds nothing regardless of call order. **Found and fixed
  twice**: first in `onboarding-mcp`'s deploy path, then — live, on a
  real customer tenant deployed through the **web wizard**, not a
  synthetic test — found to affect the wizard's own device loop
  identically (an earlier single-device wizard test had simply happened
  not to lose the race; a 4-device batch lost it uniformly, every time).
  Both surfaces now share one fixed implementation
  (`nautobot_onboard_v2.link_shadow_ip_sync()`) instead of two separate
  copies that could drift. Closed by making the onboarding code
  idempotent against the hook's own still-running attempt rather than
  trying to force strict ordering, which isn't reliably controllable
  across an async Celery-dispatched Job Hook and a synchronous REST
  caller.

None of these are open risks today — they're listed so the pattern
("this Nautobot version's actual API surface differs from what a design
doc assumed — verify live, don't trust the spec") is visible to whoever
next extends this code, not just buried in commit messages.
