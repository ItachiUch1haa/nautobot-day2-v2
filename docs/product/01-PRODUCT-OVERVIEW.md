# Product Overview

## What is Nautobot Day2?

Nautobot Day2 is an operations platform for MSPs (Managed Service
Providers) and network teams who run networks on behalf of multiple
customers. It is built as an installable **App** on top of
[Nautobot](https://networktocode.com/nautobot/) (the open-source Network
Source of Truth), plus a set of standalone services that share its data
model and codebase.

It answers three questions that come up constantly in MSP network
operations:

1. **"How do I bring a new customer or a new customer site online in
   Nautobot, correctly, without a spreadsheet and a prayer?"**
   → the onboarding pipeline (web wizard + conversational MCP interface)
2. **"How do I know my source-of-truth data actually matches what's
   running on the real devices, on an ongoing basis?"**
   → the day-2 sync engine
3. **"How does an engineer — or an AI agent acting on their behalf — get
   read access to a live device to troubleshoot something, without
   handing out raw SSH credentials?"**
   → the Agent Broker

A fourth, newer capability answers a question specific to MSPs that front
customer networks behind a shared NAT appliance:

4. **"Our customers' real device IPs aren't directly reachable — every
   customer sits behind a FortiGate doing static NAT to a shared address
   space. How do we track and reconcile the real-IP ↔ shadow-IP mapping
   in Nautobot itself, instead of it living only in the firewall's
   config?"**
   → shadow IP / VIP coverage tracking

## Who it's for

| Persona | What they use |
|---|---|
| **Onboarding engineer** | Web wizard (bulk, CSV-driven) or conversational MCP onboarding (interactive, one device/site at a time) to bring a new tenant or site online |
| **NOC / support engineer** | Agent Broker (REST or MCP) for on-demand, read-oriented device troubleshooting; the day-2 sync engine's synced facts as their source of truth |
| **AI agent (Claude, or any MCP-speaking assistant)** | Agent Broker's MCP interface for troubleshooting; `onboarding-mcp`'s MCP interface for conversational onboarding — both let an agent do the same thing a human engineer would, through the same code paths |
| **Platform/ops engineer** | Deploys and maintains the stack itself (`deploy/single-server/` or a production multi-server topology), owns OpenBao, monitors sync health |
| **Security/compliance reviewer** | Cares about credential handling (OpenBao), per-tenant isolation, and the Agent Broker's current lack of an authentication/allowlist layer — see `05-SECURITY-AND-COMPLIANCE.md` |

## The core problem this solves

An MSP running dozens or hundreds of customer networks accumulates the
same operational debt every network team eventually hits:

- Onboarding a new customer/site is manual, error-prone, and doesn't
  scale past a handful of engineers doing it by hand.
- The "source of truth" (Nautobot, or whatever came before it) silently
  drifts from what's actually configured on real hardware, because
  nothing keeps it in sync automatically.
- Troubleshooting requires either handing engineers raw device
  credentials (a real security exposure) or building yet another
  bespoke jump-host/bastion system per customer.
- Credentials themselves end up scattered across `.env` files, wikis,
  and tribal knowledge instead of one auditable, access-controlled
  store.
- For MSPs using shared-NAT architectures (one FortiGate NVA doing
  static NAT for many customers behind a private/RFC 6598 shadow
  address space), the real-IP ↔ shadow-IP mapping that makes every
  device reachable exists only in the firewall — not in the tool
  everyone actually looks at.

Nautobot Day2 addresses all five by making Nautobot itself the
authoritative record for tenant/site/device/credential-reference data,
and building every operational workflow (onboarding, sync, troubleshoot,
NAT tracking) as a thin, auditable layer on top of that one source of
truth — never a second, competing system.

## How it's packaged

Nautobot Day2 ships as:

- **A Nautobot App** (`nautobot_day2`, installed via `PLUGINS` in
  `nautobot_config.py`) — this is where the shared library code, Nautobot
  Jobs, and the shadow-IP/VIP data model live. It runs *inside* Nautobot's
  own web and Celery-worker processes.
- **Several standalone processes** that share the same installed package
  and Python environment, but run independently: the onboarding wizard
  (Flask, port 8081), the Agent Broker (REST on 8082, MCP on 8090),
  `onboarding-mcp` (MCP on 8091), and (optionally) ChatOps (loaded
  in-process by Nautobot via a `nautobot.workers` entry point).
- **Two reference deployment topologies**: a single-server Docker Compose
  stack (`deploy/single-server/`, the one used for testing and for this
  project's staging/prod servers today) and a documented multi-server
  production topology (`deploy/PRODUCTION_GUIDE.md`) where the Agent
  Broker is deployed per-tenant for network and credential isolation,
  while everything else is shared MSP-wide infrastructure.
- **OpenBao** (the Linux Foundation–governed open-source fork of
  HashiCorp Vault, used specifically to avoid Vault's BSL license) as the
  credential store every live component reads from.

## What "done" looks like today

As of this writing, every core flow has been built and **live-verified
end to end** against a real Nautobot instance (not just unit-tested in
isolation):

- Web wizard: full 6-step tenant/site/device onboarding, including the
  optional shadow-IP/VIP fields, verified working on a fresh install.
- `onboarding-mcp`: the same new-tenant/new-site/device flow, driven
  conversationally instead of via CSV upload — brought to parity with
  the web wizard this cycle, after several real bugs (see
  `06-KNOWN-ISSUES-AND-RISKS.md` for what was found and fixed, and
  `docs/06-GAPS-AND-RECOMMENDATIONS.md` for the full technical detail).
- Day-2 sync engine and Agent Broker: pre-existing, documented in the
  engineering docs' architecture diagrams.
- Shadow-IP/VIP tracking: `OnboardSite`, `CatalogShadowIP`,
  `ReconcileDeviceIPs`, `DiscoverNewDevices`, and `ValidateVIPCoverage`
  Jobs all live-verified functioning against a real Nautobot + OpenBao
  stack, including the Job Hook wiring that makes shadow-IP linking
  automatic on every real device IP create/update.

What's explicitly **not** done yet — authentication on the Agent
Broker/`onboarding-mcp`, automated tests, full per-tenant credential
isolation, and a handful of smaller items — is tracked candidly in
`05-SECURITY-AND-COMPLIANCE.md` and `06-KNOWN-ISSUES-AND-RISKS.md` rather
than left implicit.
