#!/usr/bin/env bash
# MongoDB backup script — meant to be triggered frequently via the server's
# crontab (e.g. every 15 minutes); the script itself decides whether it's
# actually time to back up, so the backup HOUR is controlled from .env
# instead of being baked into the crontab line.
#
# Controlled by .env:
#   BACKUP_ENABLED       true/1 to run at all, anything else skips (default: false)
#   BACKUP_HOUR          0-23, the local hour to back up in (default: 3)
#   BACKUP_DIR           where to write backups (default: ./backups)
#
# Backups are kept forever (no automatic deletion) — manage disk space
# manually if BACKUP_DIR starts filling up.
#
# A marker file (BACKUP_DIR/.last_backup_date) ensures at most one backup
# attempt per calendar day even though cron fires every 15 minutes during
# the matching hour.
#
# Usage: bash deploy/backup_mongo.sh
# Cron:  */15 * * * * cd /opt/e-business-card-api && bash deploy/backup_mongo.sh >> backups/cron.log 2>&1

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Missing .env in repo root, aborting backup." >&2
  exit 1
fi

CLEAN_ENV_FILE="$(mktemp)"
trap 'rm -f "$CLEAN_ENV_FILE"' EXIT
sed 's/\r$//' .env > "$CLEAN_ENV_FILE"
set -a
# shellcheck disable=SC1091
source "$CLEAN_ENV_FILE"
set +a

BACKUP_ENABLED="${BACKUP_ENABLED:-false}"
BACKUP_HOUR="${BACKUP_HOUR:-3}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
LOG_FILE="${BACKUP_LOG_FILE:-$BACKUP_DIR/backup.log}"
MONGO_CONTAINER="${BACKUP_MONGO_CONTAINER:-ebc-mongodb}"
MARKER_FILE="$BACKUP_DIR/.last_backup_date"

mkdir -p "$BACKUP_DIR"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

# Force base-10 so hours like 08/09 aren't misread as invalid octal literals.
to_int() {
  echo "$((10#$1))"
}

# "Next scheduled run" is the next occurrence of BACKUP_HOUR:00, accounting
# for whether today's window has already passed or already run.
next_run() {
  local now_hour today_marker
  now_hour="$(to_int "$(date +%H)")"
  today_marker="$(date +%Y-%m-%d)"
  if [[ "$now_hour" -lt "$(to_int "$BACKUP_HOUR")" ]] || { [[ "$now_hour" -eq "$(to_int "$BACKUP_HOUR")" ]] && [[ ! -f "$MARKER_FILE" || "$(cat "$MARKER_FILE" 2>/dev/null)" != "$today_marker" ]]; }; then
    date -d "today ${BACKUP_HOUR}:00" '+%Y-%m-%d %H:%M' 2>/dev/null || date -v"${BACKUP_HOUR}"H -v0M -v0S '+%Y-%m-%d %H:%M'
  else
    date -d "tomorrow ${BACKUP_HOUR}:00" '+%Y-%m-%d %H:%M' 2>/dev/null || date -v+1d -v"${BACKUP_HOUR}"H -v0M -v0S '+%Y-%m-%d %H:%M'
  fi
}

shopt -s nocasematch
if [[ "$BACKUP_ENABLED" != "true" && "$BACKUP_ENABLED" != "1" ]]; then
  shopt -u nocasematch
  log "[DISABLED] BACKUP_ENABLED=${BACKUP_ENABLED} — backup disabled. Would next run: $(next_run)"
  exit 0
fi
shopt -u nocasematch

CURRENT_HOUR="$(to_int "$(date +%H)")"
TARGET_HOUR="$(to_int "$BACKUP_HOUR")"
TODAY="$(date +%Y-%m-%d)"

if [[ "$CURRENT_HOUR" -ne "$TARGET_HOUR" ]]; then
  log "[WAITING] Not yet BACKUP_HOUR=${BACKUP_HOUR} (current hour ${CURRENT_HOUR}). Next run: $(next_run)"
  exit 0
fi

if [[ -f "$MARKER_FILE" && "$(cat "$MARKER_FILE")" == "$TODAY" ]]; then
  log "[SKIPPED] Already backed up today (${TODAY}) during the BACKUP_HOUR=${BACKUP_HOUR} window. Next run: $(next_run)"
  exit 0
fi

TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
DUMP_NAME="mongo_backup_${TIMESTAMP}"
CONTAINER_DUMP_PATH="/tmp/${DUMP_NAME}.archive.gz"
ARCHIVE_PATH="${BACKUP_DIR}/${DUMP_NAME}.archive.gz"
DB_NAME="${MONGO_DB_NAME:-e_business_card}"

log "[START] Starting MongoDB backup of '${DB_NAME}' from container '${MONGO_CONTAINER}' -> ${ARCHIVE_PATH}"

BACKUP_OK=true
if ! docker exec "$MONGO_CONTAINER" mongodump \
    --username "${MONGO_ROOT_USERNAME}" \
    --password "${MONGO_ROOT_PASSWORD}" \
    --authenticationDatabase admin \
    --db "${DB_NAME}" \
    --archive="${CONTAINER_DUMP_PATH}" \
    --gzip >>"$LOG_FILE" 2>&1; then
  BACKUP_OK=false
fi

if [[ "$BACKUP_OK" == "true" ]]; then
  if docker cp "${MONGO_CONTAINER}:${CONTAINER_DUMP_PATH}" "${ARCHIVE_PATH}" >>"$LOG_FILE" 2>&1; then
    docker exec "$MONGO_CONTAINER" rm -f "${CONTAINER_DUMP_PATH}" >>"$LOG_FILE" 2>&1 || true
    SIZE=$(du -h "$ARCHIVE_PATH" 2>/dev/null | cut -f1)
    log "[SUCCESS] Backup completed: ${ARCHIVE_PATH} (${SIZE:-unknown size})"
  else
    log "[FAILED] mongodump succeeded but docker cp out of the container failed. Backup NOT saved to ${BACKUP_DIR}."
    BACKUP_OK=false
  fi
else
  log "[FAILED] mongodump failed — see log lines above for the container's error output. Backup NOT completed."
fi

# Mark today as attempted regardless of outcome, so a failure doesn't retry
# every 15 minutes for the rest of the BACKUP_HOUR window — it'll try again
# at the next day's window instead.
echo "$TODAY" > "$MARKER_FILE"

log "[NEXT] Next scheduled run: $(next_run) (assuming the crontab entry and BACKUP_ENABLED stay set)"


if [[ "$BACKUP_OK" != "true" ]]; then
  exit 1
fi
