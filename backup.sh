#!/bin/bash
# GoldPulse Alerts — Backup Script
# WAL-safe online backup via sqlite3 backup API (no cp of a live WAL DB).
# Keeps the 14 most recent backups, deletes older ones.
cd "$(dirname "$0")" || exit 1
mkdir -p backups

TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
BACKUP_FILE="backups/goldpulse_${TIMESTAMP}.db"

venv/bin/python3 - "$BACKUP_FILE" <<'PY'
import sqlite3
import sys

source = sqlite3.connect("data/goldpulse.db")
dest = sqlite3.connect(sys.argv[1])
with dest:
    source.backup(dest)
source.close()
dest.close()
PY

if [ $? -ne 0 ]; then
    echo "❌ Backup FAILED: $BACKUP_FILE"
    exit 1
fi

echo "✅ Backup created: $BACKUP_FILE"
echo "   Size: $(du -h "$BACKUP_FILE" | cut -f1)"

# Retention: keep the newest 14, drop the rest.
ls -t backups/goldpulse_*.db 2>/dev/null | tail -n +15 | xargs -r rm -f