# nautobot_day2 — Documentation Index

Operational documentation for engineers working with the `nautobot_day2`
onboarding, day-2 sync, and Agent Broker pipeline. Start with the main
repo `README.md` for a general orientation; these docs go deeper on
specific concerns.

| Doc | Covers |
|---|---|
| [`01-ONBOARDING-GUIDE.md`](./01-ONBOARDING-GUIDE.md) | Step-by-step guide for engineers onboarding a new customer or site — web wizard, ChatOps, and CLI paths, plus common failure points. |
| [`02-COMPONENTS.md`](./02-COMPONENTS.md) | Every component grouped by function, and exactly what it's responsible for. |
| [`03-ARCHITECTURE.md`](./03-ARCHITECTURE.md) | Diagrams of the system layout and the three working flows: onboarding, day-2 sync, and Agent Broker troubleshooting. |
| [`04-COMPONENT-PATHS.md`](./04-COMPONENT-PATHS.md) | Exact file paths, ports, queues, and configuration/env vars for every component. |
| [`05-MONITORING.md`](./05-MONITORING.md) | What to monitor per component, suggested log aggregation, and alert priorities for keeping this pipeline healthy. |
| [`06-GAPS-AND-RECOMMENDATIONS.md`](./06-GAPS-AND-RECOMMENDATIONS.md) | Things not explicitly asked for but worth knowing — security gaps (notably the Agent Broker's current lack of an allowlist/auth), missing tests, and other operational risks. |

**Read `06-GAPS-AND-RECOMMENDATIONS.md` §1 before pointing any external
agent or untrusted network at the Agent Broker (ports 8082/8090)** — it
currently has no command allowlist and no authentication.
