#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "Usage: $0 <last-good-commit-file> <backup-sql-file>"
  exit 1
fi

COMMIT_FILE="$1"
BACKUP_FILE="$2"
REPO_DIR="/home/day2work/nautobot-day2-v2-freshtest"
COMPOSE_FILE="deploy/single-server/docker-compose.yml"

if [ ! -f "$COMMIT_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
  echo "ERROR: One of the given files doesn't exist. Double-check the paths."
  exit 1
fi

cd "$REPO_DIR"
GOOD_COMMIT=$(cat "$COMMIT_FILE")

echo "==> Rolling code back to commit: $GOOD_COMMIT"
git checkout "$GOOD_COMMIT"

echo "==> Restoring database from: $BACKUP_FILE"
docker compose -f "$COMPOSE_FILE" up -d postgres
sleep 5
cat "$BACKUP_FILE" | docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U nautobot -d postgres

echo "==> Restarting the full stack on the rolled-back code..."
docker compose -f "$COMPOSE_FILE" up -d --build

echo ""
echo "==> Rollback complete. Verify with:"
echo "  docker compose -f $COMPOSE_FILE ps"
echo "  git log -1 --oneline"
