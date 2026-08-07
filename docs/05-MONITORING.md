# Monitoring Guidelines

**Status note:** no monitoring stack ships in this repo today — the
single-server compose file has Docker-level `healthcheck` blocks (used
for compose's own `condition: service_healthy` startup ordering) but no
metrics exporter, log aggregation, or alerting. This doc is a
recommended setup, not a description of something already running.

The goal is to catch the failure modes this specific pipeline actually
has — a stuck fan-out, a site hitting its concurrency cap, a credential
that stopped working, a broker request with no allowlist — not generic
"is the box up" monitoring alone.

---

## 1. What to monitor, per component

### Nautobot web
- **Availability**: HTTP check against `/health/` (Nautobot's built-in
  health endpoint) or `nautobot-server health_check` (already used as the
  Docker healthcheck in `docker-compose.yml`).
- **App load**: alert if `nautobot_day2`'s three Jobs
  (`SyncNetworkData`, `SyncAllSites`, `MistSyncJob`) disappear from
  `extras/jobs` — that means the App failed to import on last restart.
  Can be scripted against the same query used in `INSTALL.md` Phase 8:
  ```
  Job.objects.filter(module_name__startswith='nautobot_day2').count()
  ```
  Expect `3`.
- **Request latency/error rate**: standard web-tier metrics (Django
  request duration, 5xx rate) — every onboarding script and the broker
  hit this API constantly, so Nautobot being slow cascades everywhere.

### Postgres / Redis
- Standard DB/cache monitoring (connections, replication lag if HA,
  memory, disk). Nothing `nautobot_day2`-specific here, but note that
  Redis is a **triple-duty** dependency in this design: Django cache,
  Celery broker/result backend, **and** the per-site concurrency
  counters. A Redis outage doesn't just slow things down — it also
  removes the concurrency cap's enforcement.

### Celery workers (`nautobot_day2_sync` queue)
This is the component most worth building real alerting on, since it's
where devices actually get touched:
- **Queue depth** for `nautobot_day2_sync` — a growing, never-draining
  queue means either no worker is listening on it (the #1 support issue
  in `01-ONBOARDING-GUIDE.md` §7) or workers are falling behind demand.
- **Worker liveness** — Celery's own `celery inspect ping` /
  `celery inspect active`, or a Flower dashboard if one is stood up.
- **Task success/failure rate** for `sync_device_task` — a spike in
  failures across *many devices at once* usually means a shared
  dependency (OpenBao, a site's WAN link) broke, not N unrelated device
  problems.
- **Retry rate** — `sync_device_task` retries on `TIMEOUT:`/`SSH_ERROR:`
  prefixes (backoff `15s * 2^retries`, max 3) and on `SiteAtCapacity`
  (fixed 15s, up to 20 retries). A site permanently sitting at its retry
  ceiling means `max_concurrent_per_site` is set too low for that site's
  actual device count, or a device is unreachable and blocking its slot
  for the retry window.
- **Job dispatch → summary latency** — time between a Job logging
  "Dispatched N device sync task(s)..." and `sync_summary_callback`'s
  summary line landing. A job that dispatches but never gets a summary
  means the chord's callback never fired — check for a crashed/OOM'd
  worker mid-batch.

### Per-site concurrency (`nautobot_day2.concurrency`)
- **Cache-key inspection**: `nautobot_day2:site_inflight:<site_key>` in
  Redis — should return to `0` between sync runs. A key permanently
  stuck near `max_concurrent_per_site` (even with no active sync) means
  a task crashed hard enough to skip its `finally` release; it'll
  self-heal after `SLOT_TTL_SECONDS` (600s), but repeated occurrences are
  worth investigating (worker OOM-kills, unhandled exceptions bypassing
  the `try/finally`).

### OpenBao
- **Seal status** — `GET /v1/sys/health` — OpenBao being sealed is a
  total outage for every sync and every broker call, silently, until
  someone tries a credential fetch and gets `OPENBAO_UNREACHABLE`.
- **AppRole auth failure rate** — a spike here across many
  tenants/devices at once usually means a role/secret ID rotated or
  expired, not a credentials-typo problem.
- **Storage/disk** — OpenBao's file storage backend (used in the
  single-server compose file) needs disk space and backup monitoring
  like any datastore holding the only copy of every customer's device
  credentials.
- **AppRole scope drift** (production): periodically audit that the
  broker's AppRole for tenant A genuinely cannot read tenant B's KV path
  — see `06-GAPS-AND-RECOMMENDATIONS.md` for why this matters before any
  multi-tenant broker deployment.

### Onboarding wizard (`upload_app.py`, :8081)
- **`/health`** endpoint (already exists) — wire into your uptime
  checker.
- **Error rate on `/api/deploy` and `/api/save-credentials`** — these
  are the two routes that write to Nautobot/OpenBao; failures here block
  an engineer mid-onboarding, worth a real-time alert during business
  hours rather than a nightly digest.

### Agent Broker (`api_server.py` :8082, `mcp_server.py` :8090)
- **`/health`** on the REST side.
- **Request volume and request *content*, logged** — today, neither
  interface logs what was requested (see gaps doc: no audit log exists
  yet). Until that's built, at minimum monitor raw request *count* and
  *source IP* per instance so an unexpected volume spike or an
  unexpected source is visible even without command-level detail.
- **Per-tenant instance health**, once broker instances are split
  per-customer (per `deploy/PRODUCTION_GUIDE.md`) — each is a separate
  monitoring target scoped to that customer's network boundary.

### Vendor devices / connectivity (indirect, via sync results)
- **Per-device last-successful-sync timestamp** — derivable from
  Nautobot's own data (whatever `write_facts()` last touched) or from
  parsing Job Result logs; alert on any device that hasn't synced
  successfully in longer than its expected schedule.
- **Cable/topology drift** — a sudden drop in `cables:N` in a
  sync-summary line for a site that previously had stable topology is
  worth investigating (LLDP neighbor data changed unexpectedly, or a
  device stopped reporting neighbors).

---

## 2. Suggested log aggregation

Nothing currently ships structured/centralized logging. At minimum,
ship these into one place (ELK/Loki/CloudWatch/whatever the org
standardizes on):

- Nautobot web + worker container/process logs (`docker compose logs
  nautobot`, `nautobot-worker`)
- Celery task logs (`get_task_logger(__name__)` output in `tasks.py`) —
  this is where `sync_device_task`'s retry/failure messages and
  `sync_summary_callback`'s "Batched cable creation failed" warnings
  land.
- The wizard's Flask process log (`upload_app.py`)
- The broker's two process logs (`api_server.py`, `mcp_server.py`) —
  **especially** important today since there's no allowlist/audit log
  yet; the raw process log is the only record of what commands were run
  against real devices until that's built.
- OpenBao's own audit log — OpenBao supports enabling an audit device;
  turn this on before any production use, independent of anything else
  in this doc.

## 3. Suggested alert priorities

| Priority | Condition |
|---|---|
| **P1 — page** | OpenBao sealed/unreachable; Nautobot web down; `nautobot_day2_sync` queue depth growing unbounded with zero active workers; Agent Broker reachable from outside its intended network boundary (if network-segmented per tenant) |
| **P2 — urgent, business hours** | Elevated `sync_device_task` failure rate across many devices at once; a tenant's credentials failing validation repeatedly; wizard `/api/deploy` or `/api/save-credentials` error rate spike |
| **P3 — investigate, non-urgent** | A single device stuck retrying; a site sitting at its concurrency cap; cable-creation warnings in the callback log; OpenBao AppRole auth failures for one specific identity |

## 4. What monitoring alone can't fix here

Monitoring surfaces *that* something is wrong; it does not close the
broker's current gap of accepting and executing any command with no
allowlist, restriction, or authentication. Watching request volume is a
stopgap, not a substitute for the allowlist/auth work described in
`06-GAPS-AND-RECOMMENDATIONS.md` — build that before treating broker
monitoring as a safety net.
