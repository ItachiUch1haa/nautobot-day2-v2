# Single-server test deployment

**Starting from a completely clean Ubuntu server?** Use [INSTALL.md](INSTALL.md)
instead — it covers everything from a bare box (Docker install, firewall,
swap) through a verified running instance. This file assumes Docker's
already set up and covers the compose stack itself.

Brings up Postgres + Redis + Nautobot + one Celery worker on one box, with
`nautobot_day2` installed, for testing against real devices before splitting
onto multiple machines. Matches the single-server starting point discussed —
one combined worker service here covers both the `default` and
`nautobot_day2_sync` queues; split those into two services (same image,
different `command:`) once you move to dedicated worker machines.

## Prerequisites

- Docker + Docker Compose on the server.
- Outbound access to pull `postgres:15`, `redis:7`, and
  `ghcr.io/nautobot/nautobot:2.3-py3.11` — some sandboxed/CI environments
  block container registry pulls by policy; a normal server shouldn't.

## First-time setup

```bash
cd deploy/single-server
cp .env.example .env        # edit passwords/secret key before going further
docker compose build
docker compose up -d postgres redis
# wait for both to report healthy: docker compose ps

# one-time: create the database schema
docker compose run --rm nautobot nautobot-server migrate

docker compose up -d nautobot nautobot-worker
docker compose logs -f nautobot   # watch for startup errors
```

Then open `http://<server-ip>:8080`, log in with the superuser credentials
from `.env`, and check **Jobs** in the UI — you should see "Sync Network
Data", "Sync All Sites for Tenant", and "Juniper Mist: Sync Devices to
Nautobot" listed. If they're not there, check `docker compose logs nautobot`
for an import error from `nautobot_day2`.

## Verifying nautobot_day2 actually loaded

```bash
docker compose exec nautobot nautobot-server shell -c \
  "from nautobot.extras.models import Job; print(list(Job.objects.filter(module_name__startswith='nautobot_day2').values_list('name', flat=True)))"
```

Should print the three Job names above. An empty list means the App didn't
register — recheck `PLUGINS` in `nautobot_config.py` and the worker/web logs.

## Running the onboarding scripts against this instance

The onboarding CLI scripts run outside Nautobot's process (same as always) —
point them at this instance from wherever you run them:

```bash
export NAUTOBOT_URL=http://<server-ip>:8080
export NAUTOBOT_TOKEN=<the NAUTOBOT_SUPERUSER_API_TOKEN from .env>
python3 nautobot_day2/onboarding/bootstrap_nautobot.py --dry-run
```

## Testing the Celery fan-out safely

Keep `SIMULATED = True` in `onboarding/sync_network_data.py` for the first
run — it exercises the whole dispatch → parallel tasks → summary-log path
without touching real hardware. Only flip it off once that's confirmed
working end to end.

## What I could and couldn't verify

I built and reviewed this against Nautobot's documented Docker environment
variables and config conventions, but this session's own sandbox blocks
outbound access to container registries (Docker Hub's and GHCR's blob
storage were both denied by egress policy) — so this compose stack has
**not** actually been run and booted successfully anywhere yet. Treat first
boot on your server as the real first test, and expect to iterate on
`nautobot_config.py` a little if your Nautobot version's exact env var
names differ from what's assumed here.
