# Clean Ubuntu server → running the FULL nautobot_day2 stack, step by step

Start to finish, assuming the server has nothing on it yet. Minimum spec for
this single-server test tier: 4 vCPU / 8GB RAM / 50GB disk — comfortable
enough for Postgres + Redis + Nautobot + one worker at a few hundred to
~1,000 devices. Commands are copy-paste ready; run them as a regular user
with `sudo`, not as root directly.

**This is the single, complete install path** — it brings up and verifies
every one of the 9 services in `docker-compose.yml` (Postgres, Redis,
OpenBao, Nautobot web, Celery worker, the onboarding wizard, and both Agent
Broker interfaces), not just the database/Nautobot subset. Follow it in
order — later phases assume earlier ones passed their checks. A single
consolidated pass/fail checklist is in **Phase 17** if you want to jump
straight to "did my install actually work."

---

## Phase 0 — first login housekeeping

If you're logging in as `root` on a fresh VM, make a regular sudo user first
— running Docker and everything else as root directly works, but one bad
command has a much bigger blast radius:

```bash
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy   # carry over your SSH key
```

Log out and back in as `deploy` for everything below.

## Phase 1 — update the system, set the basics

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ca-certificates gnupg nano jq

sudo timedatectl set-ntp true
timedatectl status   # confirm "System clock synchronized: yes"
```

`jq` is used later to pull `role_id`/`secret_id` values out of OpenBao's
JSON responses — not strictly required, but saves a lot of manual copying.

Check available memory and swap; add a swap file if you're under ~8GB RAM
(cheap insurance — Postgres and Docker both appreciate not OOM-killing under
a load spike):

```bash
free -h
# if Swap shows 0B and RAM is small:
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## Phase 2 — basic firewall (SSH only for now)

```bash
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status
```

Every other port (Nautobot's 8080, the wizard's 8081, the Agent Broker's
8082/8090) gets opened deliberately in **Phase 13**, once each service is
actually running and you can make an informed call about which of them
should ever be reachable from outside this box. Opening them now, before
anything is running or verified, is how test ports end up exposed by
accident.

## Phase 3 — install Docker Engine + Compose (official repo, not the Ubuntu snap package)

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

sudo usermod -aG docker $USER
newgrp docker      # picks up the group change without logging out
```

Verify Docker actually works before going further:

```bash
docker run --rm hello-world
docker compose version
```

If `hello-world` doesn't print its success message, stop here and fix Docker
first — nothing past this point will work otherwise.

## Phase 4 — get the code

```bash
cd ~
git clone https://github.com/ItachiUch1haa/nautobot-day2-v2.git
cd nautobot-day2-v2/deploy/single-server
```

It's a private repo, so `git clone` will prompt for credentials — use a
GitHub personal access token as the password (GitHub no longer accepts
account passwords for this), or set up an SSH deploy key on this server and
clone via the `git@github.com:...` URL instead.

## Phase 5 — configure secrets (pass 1 — everything except OpenBao's App­Role secret IDs)

```bash
cp .env.example .env
nano .env
```

Replace every `change-me` value:
- `NAUTOBOT_SECRET_KEY` — any random 50-character string (`openssl rand -hex 25` works)
- `NAUTOBOT_DB_PASSWORD`, `NAUTOBOT_REDIS_PASSWORD` — real passwords
- `NAUTOBOT_SUPERUSER_PASSWORD` — your admin login password
- `NAUTOBOT_SUPERUSER_API_TOKEN` — a 40-char hex string (`openssl rand -hex 20`) — this is what the onboarding scripts, wizard, and broker will authenticate to Nautobot with

Leave `BAO_SECRET_ID`, `BAO_BROKER_SECRET_ID`, and `BAO_REFRESHER_SECRET_ID`
exactly as `.env.example` has them (blank/placeholder) for now — OpenBao
doesn't exist yet, so there's nothing real to put there. **Phase 8** below
generates the real values and comes back to this same file.

## Phase 6 — build the image

```bash
docker compose build
```

This step downloads the base Nautobot image and installs `nautobot_day2`
(including the `[broker]` extra — Nornir, Netmiko, MCP) on top — it can take
a few minutes on first run.

## Phase 7 — bring up the three foundation services: Postgres, Redis, OpenBao

These three have no dependency on Nautobot or each other beyond what Docker
Compose's healthchecks already encode, so bring them up together:

```bash
docker compose up -d postgres redis openbao
docker compose ps
```

Wait until **Postgres and Redis** show `healthy`. **OpenBao will correctly
stay `unhealthy` at this point — that's expected, not a bug.** Its
healthcheck deliberately fails while OpenBao is sealed (the default state
on every fresh volume and after every restart), specifically so that
`upload-app`, `agent-broker`, and `agent-broker-mcp` — which all depend on
OpenBao being genuinely usable, not just running — won't start against an
unusable OpenBao. It will flip to `healthy` automatically, with no restart
needed, the moment Phase 8 unseals it.

```bash
watch docker compose ps   # Ctrl-C once postgres/redis are healthy
```

If OpenBao's container isn't even *running* (not just unhealthy — actually
exited or restarting), that's the real problem to chase: check
`docker compose logs openbao` for a bind failure on `0.0.0.0:8200` or a
storage permission error (`mkdir /openbao/data/core: permission denied`
means the `volume-init` service didn't get a chance to `chown` the
`openbao_data` volume before OpenBao tried to write to it — confirm
`volume-init` shows `Exited (0)` in `docker compose ps -a`, not a
non-zero exit code).

## Phase 8 — initialize and unseal OpenBao, create its AppRoles, finish `.env`

**This step does not exist anywhere else in the repo's docs — do not skip
it.** OpenBao ships with nothing pre-configured: no KV mount, no AppRole
auth, no policies, no roles. Every one of the `BAO_*` values that
`docker-compose.yml` already hardcodes for `BAO_ROLE_ID` /
`BAO_REFRESHER_ROLE_ID` (and the corresponding `b46357d7-...` role ID used
for the wizard and both brokers) is a **specific Role ID this phase must
create with a matching custom ID** — that's what lets the rest of the
compose file work unmodified once you fill in the three secret IDs.

### 8.1 — Initialize (one-time, ever, per OpenBao data volume)

```bash
docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 bao operator init -key-shares=1 -key-threshold=1" \
  | tee ~/openbao-init-output.txt
```

This prints exactly one **Unseal Key** and one **Initial Root Token**.

> Single-key-share (`-key-shares=1 -key-threshold=1`) is a single-server
> test-tier simplification — production OpenBao should use Shamir's Secret
> Sharing with multiple key shares held by different people (e.g.
> `-key-shares=5 -key-threshold=3`), or an auto-unseal mechanism (cloud KMS)
> instead of a single key one person holds. Do not carry this test-tier
> choice into production.

**Save `~/openbao-init-output.txt` somewhere durable and off this box** (a
password manager, not a file left on the server) — the unseal key and root
token are not recoverable from OpenBao itself if lost, and you'll need the
unseal key again every time this container restarts (see the note at the
end of this phase, and the earlier discussion in this session about
stopping the VM overnight).

### 8.2 — Unseal

```bash
UNSEAL_KEY=$(grep 'Unseal Key 1' ~/openbao-init-output.txt | awk '{print $NF}')
ROOT_TOKEN=$(grep 'Initial Root Token' ~/openbao-init-output.txt | awk '{print $NF}')

docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 bao operator unseal '$UNSEAL_KEY'"
```

Confirm: `"Sealed" false` in the output.

### 8.3 — Enable the KV v2 secrets engine at `kv/`

This is the exact mount path `nautobot_day2/openbao_client.py` expects
(`kv/data/tenants/<slug>/<prefix>`):

```bash
docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao secrets enable -path=kv -version=2 kv"
```

### 8.4 — Enable AppRole auth

```bash
docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao auth enable approle"
```

### 8.5 — Write the three policies

Three distinct identities, three distinct policies — the sync
engine/broker identity is **read-only**; the refresher identity used for
credential rotation is the only one that can write, per the design
documented in `openbao_client.py`:

```bash
docker compose exec -T openbao sh -c "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao policy write day2-sync-engine-policy -" <<'EOF'
path "kv/data/tenants/*" {
  capabilities = ["read", "list"]
}
path "kv/metadata/tenants/*" {
  capabilities = ["list"]
}
EOF

docker compose exec -T openbao sh -c "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao policy write day2-broker-policy -" <<'EOF'
path "kv/data/tenants/*" {
  capabilities = ["read", "list"]
}
path "kv/metadata/tenants/*" {
  capabilities = ["list"]
}
EOF

docker compose exec -T openbao sh -c "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao policy write day2-credential-refresher-policy -" <<'EOF'
path "kv/data/tenants/*" {
  capabilities = ["read", "create", "update"]
}
path "kv/metadata/tenants/*" {
  capabilities = ["list"]
}
EOF
```

> On a real multi-tenant production deployment, replace the `tenants/*`
> wildcard above with a separate policy per tenant (`tenants/<slug>/*`) and
> a separate AppRole per tenant's Agent Broker — this single-server install
> uses one broad policy across all three identities for simplicity, which
> is acceptable for one-box testing but is exactly the isolation gap called
> out in `docs/06-GAPS-AND-RECOMMENDATIONS.md` §3. Don't skip narrowing
> this before any real customer's credentials go into this OpenBao.

### 8.6 — Create the three AppRoles, pin their Role IDs to match `docker-compose.yml`, generate Secret IDs

```bash
# day2-sync-engine — used by Nautobot web + worker (BAO_ROLE_ID / BAO_SECRET_ID)
docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao write auth/approle/role/day2-sync-engine \
   token_policies=day2-sync-engine-policy token_ttl=15m token_max_ttl=1h secret_id_ttl=0"
docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao write auth/approle/role/day2-sync-engine/role-id \
   role_id=b85e4c02-4b8d-48f4-2e63-4f3aaf2fb290"
SYNC_SECRET_ID=$(docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao write -f -format=json auth/approle/role/day2-sync-engine/secret-id" \
  | jq -r '.data.secret_id')

# day2-broker — used by the onboarding wizard AND both Agent Broker interfaces
# (BAO_ROLE_ID / BAO_BROKER_SECRET_ID on upload-app, agent-broker, agent-broker-mcp)
docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao write auth/approle/role/day2-broker \
   token_policies=day2-broker-policy token_ttl=15m token_max_ttl=1h secret_id_ttl=0"
docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao write auth/approle/role/day2-broker/role-id \
   role_id=b46357d7-d0e9-60b3-c73d-fe472233edd2"
BROKER_SECRET_ID=$(docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao write -f -format=json auth/approle/role/day2-broker/secret-id" \
  | jq -r '.data.secret_id')

# day2-credential-refresher — write-scoped, shared by Nautobot, the wizard, and both brokers
# (BAO_REFRESHER_ROLE_ID / BAO_REFRESHER_SECRET_ID)
docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao write auth/approle/role/day2-credential-refresher \
   token_policies=day2-credential-refresher-policy token_ttl=15m token_max_ttl=1h secret_id_ttl=0"
docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao write auth/approle/role/day2-credential-refresher/role-id \
   role_id=a73d98bb-6c1b-cf27-7309-b125bd5c9474"
REFRESHER_SECRET_ID=$(docker compose exec openbao sh -c \
  "BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=$ROOT_TOKEN bao write -f -format=json auth/approle/role/day2-credential-refresher/secret-id" \
  | jq -r '.data.secret_id')

echo "SYNC_SECRET_ID=$SYNC_SECRET_ID"
echo "BROKER_SECRET_ID=$BROKER_SECRET_ID"
echo "REFRESHER_SECRET_ID=$REFRESHER_SECRET_ID"
```

The Role IDs above (`b85e4c02-...`, `b46357d7-...`, `a73d98bb-...`) are
**not arbitrary** — they're the exact values already hardcoded into
`docker-compose.yml`'s `BAO_ROLE_ID`/`BAO_REFRESHER_ROLE_ID` environment
entries. Pinning them here means nothing in the compose file needs editing
— only the three Secret ID values below.

### 8.7 — Finish `.env` (pass 2)

```bash
nano .env
```

Set:
- `BAO_SECRET_ID` = the `SYNC_SECRET_ID` value printed above
- `BAO_BROKER_SECRET_ID` = the `BROKER_SECRET_ID` value printed above
- `BAO_REFRESHER_SECRET_ID` = the `REFRESHER_SECRET_ID` value printed above

## Phase 9 — create the database schema

```bash
docker compose run --rm nautobot nautobot-server migrate
```

One-time only: creates the database schema. Expect a wall of `Applying
<app>.<migration>... OK` lines — that's normal and means it's working.

## Phase 10 — bring up Nautobot web + worker

```bash
docker compose up -d nautobot nautobot-worker
docker compose logs -f nautobot   # watch for startup errors
```

Watch this until it settles on something like `Listening at: http://0.0.0.0:8080`
with no tracebacks above it, then `Ctrl-C` to stop following the log (the
containers keep running in the background).

**Check 10.1 — the App loaded and its Jobs are registered:**

```bash
docker compose exec nautobot nautobot-server shell -c \
  "from nautobot.extras.models import Job; print(list(Job.objects.filter(module_name__startswith='nautobot_day2').values_list('name', flat=True)))"
```

Should print exactly: `Sync Network Data`, `Sync All Sites for Tenant`,
`Juniper Mist: Sync Devices to Nautobot`. An empty or partial list means the
App failed to import — check `docker compose logs nautobot` for a
traceback, not a UI issue.

**Check 10.2 — the worker is listening on both queues:**

```bash
docker compose exec nautobot-worker nautobot-server celery inspect active_queues
```

Should list both `default` and `nautobot_day2_sync` for the worker. If
`nautobot_day2_sync` is missing, re-check the worker's `command:` in
`docker-compose.yml` — this is the single most common "sync Job dispatches
but nothing ever happens" cause.

**Check 10.3 — OpenBao's read-only (`day2-sync-engine`) identity actually
authenticates from inside the Nautobot containers:**

```bash
docker compose exec nautobot python3 -c "
from nautobot_day2.openbao_client import fetch_openbao_secret
print(fetch_openbao_secret('smoketest', 'ssh'))
"
```

Expect `{}` — an empty dict means OpenBao auth *succeeded* and it simply
found no secret at that made-up path yet, which is correct at this point.
An `OPENBAO_AUTH_FAILURE` or `OPENBAO_CONFIG_ERROR` here means Phase 8 was
missed or the wrong Secret ID landed in `.env` — fix that before continuing
to any other phase.

From the Nautobot UI: open `http://<server-ip>:8080` (not exposed through
the firewall yet — SSH-tunnel to it for now, e.g. `ssh -L
8080:localhost:8080 deploy@<server-ip>`), log in with the superuser
credentials from `.env`, and confirm **Jobs** shows the same three Jobs as
Check 10.1.

## Phase 11 — bring up the onboarding wizard

```bash
docker compose up -d upload-app
curl -sf http://127.0.0.1:8081/health && echo " — wizard OK"
```

**Check 11.1 — the wizard's own OpenBao identity (`day2-broker`) authenticates:**

```bash
docker compose exec upload-app python3 -c "
from nautobot_day2.openbao_client import fetch_openbao_secret
print(fetch_openbao_secret('smoketest', 'ssh'))
"
```

Same expectation as Check 10.3 — `{}`, not an auth error.

## Phase 12 — bring up the Agent Broker (REST + MCP)

```bash
docker compose up -d agent-broker agent-broker-mcp
curl -sf http://127.0.0.1:8082/health && echo " — broker REST OK"
docker compose logs agent-broker-mcp | grep -i "MCP Server" \
  && echo " — broker MCP process started"
```

The MCP interface (`:8090`) doesn't speak plain HTTP GET, so there's no
simple `/health` curl for it — confirming the process started and the
Docker healthcheck situation (`healthcheck: disable: true` in the compose
file, deliberately — see `docker-compose.yml`) is as far as automated
verification goes. A real MCP client handshake is the only full test.

**⚠️ Before you open any firewall port for these two services, re-read
`docs/06-GAPS-AND-RECOMMENDATIONS.md` §1.** As of this install, the Agent
Broker has **no command allowlist and no authentication on either
interface** — anything that can reach port 8082 or 8090 can run any
command, including destructive ones, against any device it can resolve.
Phase 13 below deliberately does **not** open these two ports to anything
but this box's own internal network.

## Phase 13 — firewall: open only what should actually be reachable

```bash
# Nautobot web — only if this box is meant to serve the UI directly.
# In a real deployment this sits behind a load balancer/TLS terminator
# instead of being opened directly; for a single-server test, opening it
# to your own IP range only (not 0.0.0.0/0) is the minimum reasonable step:
sudo ufw allow from <your-office-or-VPN-CIDR> to any port 8080 proto tcp

# The onboarding wizard (8081) and BOTH Agent Broker interfaces (8082,
# 8090) are internal/MSP-only tools per docs/02-COMPONENTS.md and
# docs/06-GAPS-AND-RECOMMENDATIONS.md §1 — do NOT open these broadly.
# Reach them via SSH tunnel or a VPN that already restricts to trusted
# operators, e.g.:
#   ssh -L 8081:localhost:8081 -L 8082:localhost:8082 deploy@<server-ip>

sudo ufw status
```

## Phase 14 — bootstrap Nautobot's base objects (Phase 1 of the onboarding pipeline)

```bash
docker compose exec nautobot python3 /source/nautobot-day2/nautobot_day2/onboarding/bootstrap_nautobot.py --dry-run
```

Once the dry run looks right, re-run it without `--dry-run` to actually
create manufacturers, platforms, device roles, location types, and service
tags. Device creation later will fail with "Platform/Role not found" if
this step is skipped.

## Phase 15 — full pipeline health check

```bash
docker compose exec nautobot python3 /source/nautobot-day2/nautobot_day2/onboarding/preflight_check.py
```

This is a read-only check across the onboarding pipeline's required
scripts/services — run it any time you're unsure whether the environment
is in a good state before onboarding a real customer.

**Reading the output on this Docker Compose deployment:** `preflight_check.py`
predates the Docker Compose stack and still checks for a bare-metal/systemd
deployment. On this install, expect these specific checks to show `❌` even
when everything is genuinely fine — they're structurally inapplicable to
containers, not real failures:

- `nautobot` / `nautobot-worker` / `nautobot-upload` / `nautobot-vendor-test`
  under "Systemd Services" — this stack has no `systemd`, so
  `systemctl is-active` always fails inside a container. Use
  `docker compose ps` instead to check these.
- `Upload app (8081)` / `Vendor test app (8082)` under "Web Apps" — these
  check `localhost:<port>` from *inside the `nautobot` container's own
  network namespace*, not the host, so they fail even when `upload-app`
  and `agent-broker` are healthy on the Docker network. (Also note: on
  this stack, port 8082 is the **Agent Broker**, not a "vendor test app"
  — that label is stale.) Use the Phase 11/12 health checks instead
  (`curl http://127.0.0.1:8081/health` / `:8082/health` from the host, or
  `docker compose ps`).
- `manifests dir exists` — a directory the script expects at
  `onboarding/manifests/`; harmless if missing, it gets created
  automatically the first time something writes a manifest there (as this
  same preflight run just did, if you check again).
- `At least one tenant .env` — expected to fail until you've onboarded at
  least one tenant (Phase 9 of the wizard flow); not a sign of anything
  broken on a fresh install.

The checks that **do** matter here and should be `✅`: API reachable, API
token valid, all Base Objects counts (Phase 14), all Required Scripts
present, and Vendor Commands YAML exists. If any of those show `❌`,
that is a real problem worth chasing.

## Phase 16 — first sync test, safely

Keep `SIMULATED = True` in `nautobot_day2/onboarding/sync_network_data.py`
for the very first sync run — it exercises dispatch → parallel Celery tasks
→ summary log entry without touching a real device. Trigger "Sync Network
Data" from the Jobs UI, watch its log for the "Dispatched N device sync
task(s)..." line followed by the summary a short while later, then only
flip `SIMULATED` off once that round-trip is confirmed working.

## Phase 17 — final install checklist (all 9 services)

| # | Check | Command / where to look | Expected |
|---|---|---|---|
| 1 | Postgres healthy | `docker compose ps postgres` | `healthy` |
| 2 | Redis healthy | `docker compose ps redis` | `healthy` |
| 3 | OpenBao unsealed | `docker compose exec openbao sh -c "BAO_ADDR=http://127.0.0.1:8200 bao status"` | `Sealed: false` |
| 4 | Nautobot web healthy | `docker compose ps nautobot` | `healthy` |
| 5 | `nautobot_day2` Jobs registered | Check 10.1 | 3 Job names |
| 6 | Worker on both queues | Check 10.2 | `default`, `nautobot_day2_sync` |
| 7 | Sync-engine OpenBao identity works | Check 10.3 | `{}`, no auth error |
| 8 | Onboarding wizard healthy | `curl 127.0.0.1:8081/health` | `{"status": "ok", ...}` |
| 9 | Wizard's OpenBao identity works | Check 11.1 | `{}`, no auth error |
| 10 | Agent Broker REST healthy | `curl 127.0.0.1:8082/health` | `{"status": "ok", ...}` |
| 11 | Agent Broker MCP process up | `docker compose logs agent-broker-mcp` | startup banner, no traceback |
| 12 | No `change-me` values left in `.env` | `grep change-me .env` | no output |
| 13 | Ports 8081/8082/8090 not reachable from outside this box's trusted network | `nc -zv <server-ip> 8082` from an *untrusted* network | connection refused/timeout |
| 14 | OpenBao unseal key + root token stored off-box | — | confirmed with whoever owns secrets storage |
| 15 | First simulated sync completed end to end | Phase 16 | summary log line with `✅ N ❌ 0` |

If every row above passes, the install is complete and matches the
documented architecture in `docs/03-ARCHITECTURE.md`. Rows 12–14 are
security posture checks, not functional ones — a "fail" there doesn't mean
the stack is broken, it means it isn't safe to point at a real customer's
network yet.

## Phase 18 (optional) — ChatOps

Not part of this compose stack today — `nautobot_config.py`'s `PLUGINS`
list here is `["nautobot_day2"]` only, and the image doesn't install the
`nautobot-chatops` package. Adding it means: rebuild the image with
`pip install ".[chatops]"` added to the `Dockerfile`, add
`nautobot_chatops` to `PLUGINS` **before** `nautobot_day2` in
`nautobot_config.py`, and complete `nautobot-chatops`' own Slack app setup
(bot token + signing secret) — none of which this single-server compose
file provides out of the box. See the main `README.md`'s ChatOps section
for the package-level steps once you're ready for that.

## If something breaks

```bash
docker compose ps                       # which container is unhealthy/restarting
docker compose logs nautobot            # web process errors
docker compose logs nautobot-worker     # Celery/Job errors
docker compose logs upload-app          # onboarding wizard errors
docker compose logs agent-broker        # broker REST errors
docker compose logs agent-broker-mcp    # broker MCP errors
docker compose logs openbao             # sealed/storage/listener errors
docker compose logs postgres redis      # DB/broker connectivity issues
```

Specific to this stack's OpenBao dependency: `OPENBAO_UNREACHABLE` in any
log means OpenBao is either sealed (re-run Phase 8.2 with your saved unseal
key — this happens on **every** container/VM restart, OpenBao does not
auto-unseal here) or the `BAO_ADDR` value doesn't resolve (it should always
be `http://openbao:8200` — the in-network service name, not `127.0.0.1`,
from every container except when you're `exec`-ing directly into the
`openbao` container itself). `OPENBAO_AUTH_FAILURE` means a Secret ID in
`.env` doesn't match what Phase 8 generated, or a Secret ID's TTL expired
(this install sets `secret_id_ttl=0`, i.e. no expiry, specifically to avoid
that on a test box — do not carry `secret_id_ttl=0` into a production
policy without deciding on a real rotation cadence).

Paste whichever log has the traceback back here and I'll help track it down.
