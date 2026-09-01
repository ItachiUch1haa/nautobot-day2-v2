# Roadmap

**This page is a template.** It is not populated with real priorities —
those are a product/business decision, not something to derive from the
codebase. Fill in each section below once priorities are decided; the
sections themselves reflect how this platform's own known-gaps list
(`06-KNOWN-ISSUES-AND-RISKS.md`, `05-SECURITY-AND-COMPLIANCE.md`) is
already organized, so candidate items are easy to pull from there when
scoping each horizon.

## How to use this page

1. Replace each `_(TBD — describe...)_` placeholder with real content.
2. Keep the horizon structure (Now / Next / Later) or swap in whatever
   planning cadence this team actually uses (quarters, sprints, themes)
   — the structure below is a reasonable Confluence-native default, not
   a requirement.
3. Link each roadmap item back to its source in `05-SECURITY-AND-COMPLIANCE.md`
   / `06-KNOWN-ISSUES-AND-RISKS.md` / `03-FEATURES.md` where one exists,
   so a reader can jump from "what" to "why this matters" without
   re-deriving context.
4. Update `03-FEATURES.md`'s status column as items here ship — a
   feature moving from Beta to GA, or a gap closing, should show up in
   both places.

---

## Now — actively being worked or committed for the current cycle

_(TBD — what's in flight right now.)_

| Item | Owner | Target | Notes |
|---|---|---|---|
| _(TBD)_ | | | |

## Next — decided, not yet started

_(TBD — what's committed but not yet in progress. Priority 1/2 items
from `05-SECURITY-AND-COMPLIANCE.md` — Agent Broker/`onboarding-mcp`
authentication, the OpenBao recovery-path finding, per-tenant credential
isolation enforcement — are natural candidates to consider for this
horizon given their stated urgency, but that's this team's call, not a
default.)_

| Item | Priority | Notes |
|---|---|---|
| _(TBD)_ | | |

## Later — under consideration, not yet committed

_(TBD — everything else worth doing eventually: automated test
coverage, credential rotation policy, `.env` dual-write retirement,
backup/DR procedures, cross-tenant sync fairness, expanded vendor
coverage, live FortiGate validation for the VIP-coverage jobs, and
anything from `06-KNOWN-ISSUES-AND-RISKS.md` not already pulled into
Now/Next.)_

| Item | Notes |
|---|---|
| _(TBD)_ | |

## Explicitly out of scope

_(TBD — worth naming what this platform deliberately does **not** intend
to do, so it stops coming up in planning conversations. E.g., today:
Cisco AP management via DNAC/WLC-SSH is `enabled: False` by design, not
a roadmap gap — see `06-KNOWN-ISSUES-AND-RISKS.md` item 4.)_

---

## Changelog of major shipped work (reverse chronological)

Kept here as roadmap *history* — what actually shipped, not what's
planned. Update on each major release.

| Date | Shipped | Summary |
|---|---|---|
| _(this cycle)_ | Shadow IP / VIP coverage tracking | `OnboardSite`, `CatalogShadowIP`, `ReconcileDeviceIPs`, `DiscoverNewDevices`, `ValidateVIPCoverage` — real-to-shadow IP mapping for FortiGate NVA static NAT, live-verified end to end. |
| _(this cycle)_ | `onboarding-mcp` brought to parity with the web wizard | Conversational onboarding surface now supports the same new-tenant/existing-tenant, new-site/existing-site paths and shadow-IP/VIP fields as the wizard, after several real, live-verified bugs found and fixed (see `06-KNOWN-ISSUES-AND-RISKS.md`). |
| _(earlier)_ | Agent Broker (REST + MCP) | On-demand, credential-brokered device access for human engineers and AI agents. |
| _(earlier)_ | Day-2 sync engine | Scheduled/on-demand fact sync, LLDP-derived cabling, per-site concurrency. |
| _(earlier)_ | Web onboarding wizard | Bulk, CSV-driven tenant/site/device onboarding. |
