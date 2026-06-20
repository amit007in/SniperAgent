#!/bin/bash
# Safe backup of marketdata.db using SQLite's online backup API.
# Unlike `cp`, this is safe even while a fetch is running.
#
# Usage:
#   bash backup_marketdata.sh               # backup to Data.nosync/backups/
#   bash backup_marketdata.sh /my/path.db   # backup to custom path

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/Data.nosync/marketdata.db"
BACKUP_DIR="$REPO/Data.nosync/backups"
DEST="${1:-$BACKUP_DIR/marketdata_$(date +%Y%m%d_%H%M%S).db}"

mkdir -p "$(dirname "$DEST")"

if [ ! -f "$SRC" ]; then
  echo "ERROR: $SRC not found"
  exit 1
fi

echo "Backing up: $SRC"
echo "        to: $DEST"

# .backup uses SQLite's hot-backup API — consistent snapshot even mid-write
sqlite3 "$SRC" ".backup '$DEST'"

SIZE=$(du -sh "$DEST" | cut -f1)
echo "Done. Backup size: $SIZE"
