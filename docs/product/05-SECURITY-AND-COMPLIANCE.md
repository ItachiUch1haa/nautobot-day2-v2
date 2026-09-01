# Security & Compliance

A stakeholder-level summary of this platform's current security posture —
what's enforced today, what's a known, documented open gap, and how
urgent each is. Full technical detail for every item lives in
`docs/06-GAPS-AND-RECOMMENDATIONS.md`; this page exists so a security
reviewer or leadership doesn't have to read all 18 items to understand
where the real risk is.

**Nothing on this page is hidden or newly discovered by an outside
reviewer** — every item here was identified and documented by the team
building this platform, as it was found, specifically so it's addressed
deliberately rather than discovered the hard way in production.

## Priority 1 — block before any exposure beyond a trusted internal network

### The Agent Broker and `onboarding-mcp` have no authentication or command gating

Confirmed directly in the code's own docstrings: **any caller that can
reach the Agent Broker (ports 8082/8090) can run any command — including
destructive ones (`reload`, `write erase`, config-mode changes) — against
any device it can resolve, using a real, live-fetched credential.**
There is no authentication on either interface, and no allowlist
restricting which commands are permitted.

`onboarding-mcp` (port 8091) carries the same gap, and is arguably
**higher risk**: the broker only reads device state and runs commands
with an existing credential, while `onboarding-mcp` **writes new
credentials into OpenBao and creates real Nautobot objects** (tenants,
sites, devices) on behalf of whoever can reach it.

**Current mitigation**: neither port is opened beyond the deployment
box's own trusted internal network in the documented install process
(`deploy/single-server/INSTALL.md` explicitly does not open 8081, 8082,
8090, or 8091 to any external network — only 8080, scoped to a
known CIDR). This is a network-boundary control, not an application-level
one — it depends entirely on that boundary being correctly maintained.

**What closing this requires**: authentication on both interfaces, plus
a per-vendor read-only command allowlist (verb-pattern matched, not an
exact-string list — with explicit exclusions for commands that are
technically read-only but still leak secrets, like `show running-config`)
for the broker, and an audit log of every command attempted, allowed or
not, on both. Full detail: `docs/06-GAPS-AND-RECOMMENDATIONS.md` §1 and
§13.

## Priority 2 — required before real customer credentials are stored long-term

### Per-tenant credential isolation is a documented design intent, not an enforced control

The production deployment guide describes scoping each tenant's Agent
Broker to its own OpenBao AppRole (read-only, restricted to that
tenant's own KV path) as required setup — but this is a **manual runbook
step per new customer**, not something the code validates or enforces.
Nothing today would catch a misconfigured AppRole that can read a
*different* tenant's credentials. Recommended control: a periodic
automated audit that attempts a cross-tenant read with each broker's own
credentials and alerts if it ever succeeds, rather than trusting the
runbook was followed correctly every time. (`06-GAPS...md` §3)

### TLS is disabled by default in the reference deployment

The single-server Docker Compose stack runs OpenBao and Nautobot without
TLS, explicitly marked test-only in the compose file — but it's an easy
thing to accidentally carry into a real deployment unchanged. Confirm
TLS end-to-end (Nautobot, OpenBao, ideally the wizard/broker interfaces
too) before any real customer traffic touches this stack. (`06-GAPS...md` §2)

### OpenBao's documented "break glass" recovery path does not reliably work on the build/version in use

**Live-verified this cycle**: after revoking the initial root token
(standard practice once per-service AppRoles are configured, since
nothing routine needs the root token afterward), the standard
Vault/OpenBao recovery ceremony (`generate-root`, designed to let anyone
holding the unseal key regain admin access with *no* token at all)
returned `permission denied` on every attempt. The only way back in, once
hit for real, was wiping OpenBao's entire data volume and starting over —
which only cost nothing because no real tenant secrets existed in it yet
at the time. **This means: losing the current root token, once real
credentials exist in this OpenBao instance, may mean losing access to
every credential it holds, with no documented recovery path shown to
work on this build.** Until this is independently reproduced/confirmed
and (if it reproduces) reported upstream, the operational mitigation is
simple but must be followed without exception: **always keep at least
one valid privileged OpenBao token stored securely off-box**, for the
life of every OpenBao instance running this platform. (`06-GAPS...md` §18)

## Priority 3 — process and hygiene gaps

| Item | Risk | Status |
|---|---|---|
| **No automated tests** | Regression risk as more engineers touch credential-brokering, cable-creation race-avoidance, and concurrency logic in parallel, with nothing to catch a break before production. | Open — flagged as a pre-production blocker in `deploy/PRODUCTION_GUIDE.md` §6. |
| **Credential rotation is manual, per-field** | No automatic rotation schedule for long-lived static credentials (most SSH username/password pairs) — rotation only happens on a manual wizard re-save, or via a vendor's own OAuth2 refresh flow (Aruba Central only). | Open — a compliance-driven decision on required rotation cadence hasn't been made yet. |
| **Legacy `.env` credential files are still actively dual-written** | Not just a backward-compat read path — every credential save still writes both OpenBao *and* a plaintext-on-disk `.env` file, creating drift risk between the two and leaving credential values on disk even in an otherwise "OpenBao-only" model. | Open — needs a deliberate decision: fully retire the `.env` write path, or explicitly document and manage it as a permanent, reconciled dual-write. |
| **No documented backup/DR strategy** | Postgres (Nautobot's entire data model), OpenBao's storage (every live customer credential), and tenant profile JSONs (not reproducible from anywhere else if lost) have no documented backup/restore-drill procedure. | Open — highest-consequence gap in this category, given OpenBao data loss means re-collecting live credentials from every customer. |

## What's already handled well

Worth stating plainly, not just the gaps:

- **Read/write identity separation in OpenBao is real, not just
  documented** — a dedicated read-only AppRole for the sync engine,
  broker, and credential-test paths; a completely separate,
  write-scoped AppRole used only for save/rotation, never shared.
- **Credentials are never hardcoded or committed** — every script,
  service, and onboarding surface reads from OpenBao (or, for legacy
  compatibility, a per-tenant `.env` file that is itself gitignored),
  never from source.
- **Live credential testing before deploy** genuinely authenticates
  against the real device/API before an onboarding is considered
  successful — not a syntactic check.
- **The production topology's per-tenant Agent Broker isolation**, once
  the manual scoping step above is actually enforced/audited, gives each
  customer's live device access its own network and credential
  boundary, rather than one shared broker reaching every customer.

## Recommended sequencing

1. Close Priority 1 (broker/`onboarding-mcp` auth + allowlist) before any
   exposure beyond the current trusted-network boundary.
2. Close the OpenBao recovery-path finding and the per-tenant isolation
   audit before onboarding a first real paying customer's credentials.
3. Make explicit, documented decisions on the Priority 3 items (test
   coverage, rotation policy, `.env` retirement, backup/DR) on whatever
   cadence this team's compliance process requires — none of them block
   correctness today, but all four compound in risk the longer real
   customer data sits on this platform without them.
