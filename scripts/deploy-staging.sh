#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/ubuntu/nautobot-day2-v2"
COMPOSE_FILE="deploy/single-server/docker-compose.yml"

cd "$REPO_DIR"

echo "==> Fetching latest staging branch..."
git fetch origin
git checkout staging
git pull origin staging

echo "==> Rebuilding and restarting the stack..."
docker compose -f "$COMPOSE_FILE" up -d --build

echo "==> Registering any new Jobs / running migrations..."
docker compose -f "$COMPOSE_FILE" exec -T nautobot nautobot-server post_upgrade

echo ""
echo "==> Staging is now on:"
git log -1 --oneline
echo ""
echo "Go test against your lab devices now."
echo "Checklist before you approve this for prod:"
echo "  [ ] New feature/vendor works as expected"
echo "  [ ] Existing vendors still onboard fine"
echo "  [ ] Existing sync jobs still run"
echo "  [ ] Broker still responds to a basic command"
