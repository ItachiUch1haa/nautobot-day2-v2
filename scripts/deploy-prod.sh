#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/day2work/nautobot-day2-v2-freshtest"
BACKUP_DIR="/opt/backups/nautobot"
COMPOSE_FILE="deploy/single-server/docker-compose.yml"
DATE=$(date +%Y%m%d-%H%M%S)

cd "$REPO_DIR"
mkdir -p "$BACKUP_DIR"

echo "==> Currently running commit:"
git log -1 --oneline

echo ""
echo "==> Step 1: Backing up the database..."
docker compose -f "$COMPOSE_FILE" exec -T postgres pg_dumpall -U nautobot > "$BACKUP_DIR/nautobot-backup-$DATE.sql"
echo "Backup saved to: $BACKUP_DIR/nautobot-backup-$DATE.sql"

echo ""
echo "==> Step 2: Recording current commit as your rollback point..."
git rev-parse HEAD > "$BACKUP_DIR/last-good-commit-$DATE.txt"
echo "Rollback point saved to: $BACKUP_DIR/last-good-commit-$DATE.txt"

echo ""
echo "==> Step 3: Pulling latest main..."
git fetch origin
git checkout main
git pull origin main
NEW_COMMIT=$(git rev-parse --short HEAD)
echo "Now on commit: $NEW_COMMIT"

echo ""
echo "==> Step 4: Rebuilding and restarting the stack..."
docker compose -f "$COMPOSE_FILE" up -d --build

echo ""
echo "==> Step 5: Running post_upgrade (migrations, Job registration)..."
docker compose -f "$COMPOSE_FILE" exec -T nautobot nautobot-server post_upgrade

echo ""
echo "==> Step 6: Health check..."
sleep 10
if curl -sf http://localhost:8080/health/ > /dev/null; then
  echo "Nautobot is healthy."
else
  echo "WARNING: Health check failed. Consider rolling back:"
  echo "  ./scripts/rollback-prod.sh $BACKUP_DIR/last-good-commit-$DATE.txt $BACKUP_DIR/nautobot-backup-$DATE.sql"
fi

echo ""
echo "==> Deploy finished. Now running commit $NEW_COMMIT."
echo "If anything looks wrong later, roll back with:"
echo "  ./scripts/rollback-prod.sh $BACKUP_DIR/last-good-commit-$DATE.txt $BACKUP_DIR/nautobot-backup-$DATE.sql"
