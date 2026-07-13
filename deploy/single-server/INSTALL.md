# Clean Ubuntu server → running nautobot_day2, step by step

Start to finish, assuming the server has nothing on it yet. Minimum spec for
this single-server test tier: 4 vCPU / 8GB RAM / 50GB disk — comfortable
enough for Postgres + Redis + Nautobot + one worker at a few hundred to
~1,000 devices. Commands are copy-paste ready; run them as a regular user
with `sudo`, not as root directly.

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
sudo apt install -y curl git ca-certificates gnupg nano

sudo timedatectl set-ntp true
timedatectl status   # confirm "System clock synchronized: yes"
```

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

## Phase 2 — basic firewall

```bash
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status
```

Nautobot's web port (8080) gets opened later, only once it's actually running.

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

## Phase 5 — configure secrets

```bash
cp .env.example .env
nano .env
```

Replace every `change-me` value:
- `NAUTOBOT_SECRET_KEY` — any random 50-character string (`openssl rand -hex 25` works)
- `NAUTOBOT_DB_PASSWORD`, `NAUTOBOT_REDIS_PASSWORD` — real passwords
- `NAUTOBOT_SUPERUSER_PASSWORD` — your admin login password
- `NAUTOBOT_SUPERUSER_API_TOKEN` — a 40-char hex string (`openssl rand -hex 20`) — this is what the onboarding scripts will authenticate with

## Phase 6 — build and bring it up, in order (don't skip the wait steps)

```bash
docker compose build
```

This step downloads the base Nautobot image and installs `nautobot_day2` on
top — it can take a few minutes on first run.

```bash
docker compose up -d postgres redis
docker compose ps
```

Wait until both show `healthy` in the STATUS column before continuing —
if you move on too early, the next step will fail to connect.

```bash
docker compose run --rm nautobot nautobot-server migrate
```

One-time only: creates the database schema. Expect a wall of `Applying
<app>.<migration>... OK` lines — that's normal and means it's working.

```bash
docker compose up -d nautobot nautobot-worker
docker compose logs -f nautobot
```

Watch this until it settles on something like `Listening at: http://0.0.0.0:8080`
with no tracebacks above it, then `Ctrl-C` to stop following the log (the
containers keep running in the background).

## Phase 7 — open it up and check it in a browser

```bash
sudo ufw allow 8080/tcp
```

From your own machine, browse to `http://<server-ip>:8080`, log in with the
superuser credentials from `.env`, and go to **Jobs**. You should see:
- Sync Network Data
- Sync All Sites for Tenant
- Juniper Mist: Sync Devices to Nautobot

If they're missing, re-check `docker compose logs nautobot` for an import
error — that's the App failing to load, not a UI issue.

## Phase 8 — confirm from the inside, not just the UI

```bash
docker compose exec nautobot nautobot-server shell -c \
  "from nautobot.extras.models import Job; print(list(Job.objects.filter(module_name__startswith='nautobot_day2').values_list('name', flat=True)))"
```

Should print the same three names. This is the one command that proves
`nautobot_day2` is genuinely registered, independent of anything the UI
might be caching.

## Phase 9 — run the onboarding scripts against this instance

The container already has the full repo and its dependencies installed —
easiest to just run scripts inside it rather than setting up Python
separately on the host:

```bash
docker compose exec nautobot python3 /source/nautobot-day2/nautobot_day2/onboarding/bootstrap_nautobot.py --dry-run
```

Once the dry run looks right, re-run it without `--dry-run` to actually
create the base objects. Then move on to `create_tenant.py`,
`nautobot_prepare.py`, `nautobot_onboard_v2.py` the same way, following the
sequence from the earlier walkthrough.

## Phase 10 — first sync test, safely

Keep `SIMULATED = True` in `nautobot_day2/onboarding/sync_network_data.py`
for the very first sync run — it exercises dispatch → parallel Celery tasks
→ summary log entry without touching a real device. Trigger "Sync Network
Data" from the Jobs UI, watch its log for the "Dispatched N device sync
task(s)..." line followed by the summary a short while later, then only
flip `SIMULATED` off once that round-trip is confirmed working.

## If something breaks

```bash
docker compose ps                    # which container is unhealthy/restarting
docker compose logs nautobot         # web process errors
docker compose logs nautobot-worker  # Celery/Job errors
docker compose logs postgres redis   # DB/broker connectivity issues
```

Paste whichever log has the traceback back here and I'll help track it down
— this stack has been validated for structure (compose file syntax, env var
names against Nautobot's documented conventions) but not booted end-to-end
anywhere yet, so first boot on your box is the real first test.
