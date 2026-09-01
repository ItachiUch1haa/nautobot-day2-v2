# Roadmap

## Vision — where this platform is headed

Nautobot should be the **complete, single source of truth** for every
customer's network: not just inventory, but a queryable, reportable,
change-tracked, and (eventually) interactively editable record — reached
equally well through chat, an MCP-connected client, or a human in the
Nautobot UI. Five themes carry that vision forward, each building on
capability that already exists today rather than starting from zero:

| Theme | Extends | Status |
|---|---|---|
| A. Complete system of record | Core positioning (already true for what's onboarded today) | Ongoing — close remaining gaps |
| B. Command execution abstraction | Agent Broker (REST + MCP) | Exists — needs hardening before extending |
| C. Searchable inventory & customer reporting via chat/MCP | ChatOps worker; net-new MCP tool surface | Not started |
| D. Config change history & per-customer approval workflow | Day-2 sync engine's fact-writing | Not started |
| E. Interactive inventory management via MCP (add, edit, delete) | `onboarding-mcp` (today: add-only) | Not started |

**Sequencing note, not just a suggestion**: Themes C, D, and E all
increase this platform's blast radius — more surfaces that can be asked
questions about a customer's entire network, more surfaces that can
*change* it, and (for D) a workflow explicitly built around changes
happening. Every one of them depends on the Priority 1 items in
`05-SECURITY-AND-COMPLIANCE.md` (authentication + a command allowlist on
the Agent Broker and `onboarding-mcp`) being closed first, not developed
in parallel. Treat that as a hard gate on C/D/E, not a parallel
workstream — the fine-grained prioritization below assumes it.

---

## Theme A — Complete system of record

Nautobot is already the authoritative record for everything onboarded
through this platform today. This theme is about closing the gaps that
stop that claim being *unconditionally* true:

- Retire the remaining dual-write paths (tenant `.env` credential files
  written alongside OpenBao — `05-SECURITY-AND-COMPLIANCE.md`, "Priority
  3" table) so there's no shadow copy of anything, credentials included.
- Close the intentionally-deferred vendor coverage gaps (Cisco AP
  management via DNAC/WLC-SSH — `06-KNOWN-ISSUES-AND-RISKS.md` item 4)
  or explicitly declare them permanently out of scope, rather than
  leaving them an open question indefinitely.
- Confirm the shadow-IP/VIP-coverage jobs (`ValidateVIPCoverage`,
  `ReconcileDeviceIPs`) against a real FortiGate NVA (`06-KNOWN-ISSUES-AND-RISKS.md`
  item 8) — an inventory that's wrong about reachability isn't a
  complete source of truth.

## Theme B — Command execution abstraction

The Agent Broker already abstracts vendor-specific dispatch (SSH via
Netmiko, or a vendor cloud API call) behind one consistent interface —
`get_device_info`/`run_command` over MCP, or the equivalent REST calls.
Maturing it:

- **Gate**: authentication + a per-vendor, pattern-based command
  allowlist (`05-SECURITY-AND-COMPLIANCE.md` Priority 1) — today
  *any* command, including destructive ones, is accepted from anyone who
  can reach the broker.
- Once gated, extend the abstraction from raw command strings toward
  structured command **intents** (e.g. "show interface status" resolved
  correctly per vendor, rather than the caller needing to know each
  vendor's exact syntax) — this is also the groundwork Theme C's
  reporting needs, and any future write-capable action Theme D/E might
  eventually support.

## Theme C — Searchable inventory & customer reporting via chat/MCP

Net-new. Today: the Agent Broker answers questions about *one device at
a time*; `onboarding-mcp` only creates new inventory. Neither answers
"what does Customer X's whole network look like" or "give me a report on
Customer Y" from a chat or MCP client. This theme is that missing
surface:

- A new, **read-only** MCP tool set (e.g. `search_inventory`,
  `get_customer_report`, `list_devices_by_status`) and/or an extension
  to the existing ChatOps worker (`chatops/worker.py`) for the same
  queries from Slack/Teams.
- Answers come directly from Nautobot's data — no separate reporting
  database, no export-and-reprocess step.
- Depends on Theme A: a report is only as trustworthy as the inventory
  behind it being genuinely complete.

## Theme D — Config change history & per-customer approval workflow

Net-new. Today the day-2 sync engine writes each device's *current*
facts to Nautobot on every run — there's no record of what changed
between one sync and the next, and nothing gates a detected change
before it's considered accepted.

- **Change history**: store a diff (not just latest state) each time a
  device's synced facts change, per customer — the actual "what changed,
  when" record this theme's name implies.
- **Notification**: surface a detected change to whatever
  paging/ticketing/chat channel this team standardizes on. Likely the
  same mechanism `06-KNOWN-ISSUES-AND-RISKS.md` item 7 already flags as
  missing for `ValidateVIPCoverage` mismatches — worth building once,
  shared by both.
- **Approval gate**: a detected/pending change surfaces for explicit
  human sign-off before being treated as final — this becomes
  load-bearing, not optional, the moment this platform can *make*
  changes (Theme B's future write-capable actions, or Theme E), not just
  observe them.

## Theme E — Interactive inventory management via MCP (add, edit, delete)

Extends `onboarding-mcp` past its current add-only scope (tenant/site/
device onboarding) into full lifecycle management — editing and deleting
inventory conversationally, not just creating it.

- **Gate, explicitly**: edit/delete is a materially bigger blast radius
  than today's create-only surface, combined with `onboarding-mcp`
  already having *no* authentication at all
  (`05-SECURITY-AND-COMPLIANCE.md` Priority 1, called out there as
  higher-risk than the Agent Broker's equivalent gap). Closing that is a
  hard prerequisite for this theme, not a parallel workstream.
- Needs its own confirmation/undo model beyond "the API call succeeded"
  — an MCP client (human or AI agent) editing or deleting the wrong
  tenant/site/device needs a real safety net, likely tying into Theme
  D's approval-workflow infrastructure rather than reinventing one.

---

## How to use the rest of this page

1. Break each theme above into sized, sequenced items once scoped —
   the sections below are where that goes.
2. Link each item back to its source in `05-SECURITY-AND-COMPLIANCE.md`
   / `06-KNOWN-ISSUES-AND-RISKS.md` / `03-FEATURES.md` where one exists.
3. Update `03-FEATURES.md`'s status column as items ship — a feature
   moving from Beta to GA, or a gap closing, should show up in both
   places.

## Now — actively being worked or committed for the current cycle

_(TBD — what's in flight right now.)_

| Item | Owner | Target | Notes |
|---|---|---|---|
| _(TBD)_ | | | |

## Next — decided, not yet started

_(TBD. The Priority 1 security gate above — Agent Broker /
`onboarding-mcp` authentication and command allowlist — is the natural
first candidate for this horizon, since Themes C/D/E all depend on it.)_

| Item | Priority | Notes |
|---|---|---|
| _(TBD)_ | | |

## Later — under consideration, not yet committed

_(TBD — Themes C, D, and E above once the Priority 1 gate is closed,
plus everything else in `06-KNOWN-ISSUES-AND-RISKS.md` not already
pulled into Now/Next: automated test coverage, credential rotation
policy, cross-tenant sync fairness, expanded vendor coverage.)_

| Item | Notes |
|---|---|
| _(TBD)_ | |

## Explicitly out of scope

_(TBD — worth naming what this platform deliberately does **not**
intend to do, so it stops coming up in planning conversations. E.g.
today: Cisco AP management via DNAC/WLC-SSH is `enabled: False` by
design, not a roadmap gap — see `06-KNOWN-ISSUES-AND-RISKS.md` item 4,
unless Theme A above changes that.)_

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
