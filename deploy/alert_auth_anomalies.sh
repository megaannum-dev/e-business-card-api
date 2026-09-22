#!/usr/bin/env bash
# Security anomaly detector for the e-business-card API — meant to be triggered
# every few minutes from the server's crontab. Each run reads only the nginx log
# lines written since the previous run and raises an alert when auth failures or
# rate-limit rejections exceed the configured thresholds.
#
# Alerts are written to a log file only; nothing is emailed or posted. Check it
# with:  tail -50 /var/log/ebc-alerts.log
# To add delivery later, extend notify() at the bottom — it is the single place
# an alert leaves this script.
#
# Controlled by .env (all optional, defaults shown):
#   ALERT_ENABLED             true/1 to run at all, anything else skips (default: false)
#   ALERT_LOG_FILE            where alerts are written (default: /var/log/ebc-alerts.log)
#   ALERT_STATE_DIR           read offsets + cooldown markers (default: /var/lib/ebc-alerts)
#   ALERT_ACCESS_LOG          nginx access log (default: /var/log/nginx/ebc-access.log)
#   ALERT_ERROR_LOG           nginx error log  (default: /var/log/nginx/ebc-error.log)
#   ALERT_AUTHFAIL_THRESHOLD  401+403 across all IPs in one window (default: 50)
#   ALERT_PER_IP_THRESHOLD    401+403 from a SINGLE IP in one window (default: 20)
#   ALERT_THROTTLE_THRESHOLD  429s in one window (default: 100)
#   ALERT_COOLDOWN_SECONDS    re-alert suppression per alert type (default: 1800)
#   ALERT_WEBHOOK_URL         reserved, currently unused -- alerts are log-only.
#                             To deliver them, extend notify() below; it is the
#                             single place an alert leaves this script.
#
# TUNING: a steady trickle of 401s is NORMAL here — the app forces a token
# refresh on every request and expired tokens on resume produce them. Alert on
# the spike above your baseline, not on any occurrence. Establish the baseline
# with:
#   awk '$9 ~ /^(401|403|429)$/ {print $9}' /var/log/nginx/ebc-access.log | sort | uniq -c
#
# The per-IP threshold is the one that catches credential stuffing: a single
# attacker stays well under a global count while standing out sharply per-IP.
#
# Must run as root — nginx logs are not world-readable.
#
# Usage: sudo bash deploy/alert_auth_anomalies.sh
# Cron:  */5 * * * * cd /opt/e-business-card-api && bash deploy/alert_auth_anomalies.sh >> /var/log/ebc-alerts-cron.log 2>&1

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  CLEAN_ENV_FILE="$(mktemp)"
  trap 'rm -f "$CLEAN_ENV_FILE"' EXIT
  sed 's/\r$//' .env > "$CLEAN_ENV_FILE"
  set -a
  # shellcheck disable=SC1091
  source "$CLEAN_ENV_FILE"
  set +a
fi

ALERT_ENABLED="${ALERT_ENABLED:-false}"
ALERT_LOG_FILE="${ALERT_LOG_FILE:-/var/log/ebc-alerts.log}"
ALERT_STATE_DIR="${ALERT_STATE_DIR:-/var/lib/ebc-alerts}"
ALERT_ACCESS_LOG="${ALERT_ACCESS_LOG:-/var/log/nginx/ebc-access.log}"
ALERT_ERROR_LOG="${ALERT_ERROR_LOG:-/var/log/nginx/ebc-error.log}"
ALERT_AUTHFAIL_THRESHOLD="${ALERT_AUTHFAIL_THRESHOLD:-50}"
ALERT_PER_IP_THRESHOLD="${ALERT_PER_IP_THRESHOLD:-20}"
ALERT_THROTTLE_THRESHOLD="${ALERT_THROTTLE_THRESHOLD:-100}"
ALERT_COOLDOWN_SECONDS="${ALERT_COOLDOWN_SECONDS:-1800}"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$ALERT_LOG_FILE"
}

shopt -s nocasematch
if [[ "$ALERT_ENABLED" != "true" && "$ALERT_ENABLED" != "1" ]]; then
  shopt -u nocasematch
  echo "$(date '+%Y-%m-%d %H:%M:%S') [DISABLED] ALERT_ENABLED=${ALERT_ENABLED}"
  exit 0
fi
shopt -u nocasematch

mkdir -p "$ALERT_STATE_DIR"

# Emit an alert unless the same type fired inside the cooldown window. Without
# this a sustained attack alerts on every cron tick and buries everything else.
notify() {
  local kind="$1" message="$2"
  local marker="${ALERT_STATE_DIR}/cooldown_${kind}"
  local now cutoff
  now="$(date +%s)"
  cutoff=$(( now - ALERT_COOLDOWN_SECONDS ))

  if [[ -f "$marker" ]] && [[ "$(cat "$marker" 2>/dev/null || echo 0)" -gt "$cutoff" ]]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [SUPPRESSED] ${kind} still in cooldown: ${message}"
    return 0
  fi

  echo "$now" > "$marker"
  log "[ALERT] ${message}"
}

# Print only the bytes appended since the previous run. Handles logrotate: when
# the file is shorter than our stored offset it has been rotated, so start over
# rather than reading garbage or skipping the whole new file.
read_new_lines() {
  local log_path="$1" state_name="$2" out_file="$3"
  local offset_file="${ALERT_STATE_DIR}/${state_name}.offset"
  local offset size

  : > "$out_file"

  if [[ ! -r "$log_path" ]]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN] Cannot read ${log_path} (run as root?)"
    return 0
  fi

  offset="$(cat "$offset_file" 2>/dev/null || echo 0)"
  size="$(stat -c %s "$log_path")"

  if [[ "$size" -lt "$offset" ]]; then
    offset=0
  fi

  if [[ "$size" -gt "$offset" ]]; then
    tail -c "+$((offset + 1))" "$log_path" > "$out_file"
  fi

  echo "$size" > "$offset_file"
}

ACCESS_CHUNK="$(mktemp)"
ERROR_CHUNK="$(mktemp)"
trap 'rm -f "${CLEAN_ENV_FILE:-}" "$ACCESS_CHUNK" "$ERROR_CHUNK"' EXIT

read_new_lines "$ALERT_ACCESS_LOG" "access" "$ACCESS_CHUNK"
read_new_lines "$ALERT_ERROR_LOG" "error" "$ERROR_CHUNK"

# Field 9 of the combined log format is the status code. The regex guard keeps a
# malformed request line (which shifts every field) from being counted.
AUTH_FAIL_TOTAL="$(awk '$9 ~ /^(401|403)$/' "$ACCESS_CHUNK" | wc -l | tr -d ' ')"
RATE_LIMIT_TOTAL="$(awk '$9 ~ /^429$/' "$ACCESS_CHUNK" | wc -l | tr -d ' ')"

TOP_LINE="$(awk '$9 ~ /^(401|403)$/ {print $1}' "$ACCESS_CHUNK" | sort | uniq -c | sort -rn | head -1 || true)"
TOP_COUNT="$(awk '{print $1+0}' <<<"$TOP_LINE")"
TOP_IP="$(awk '{print $2}' <<<"$TOP_LINE")"
TOP_COUNT="${TOP_COUNT:-0}"
TOP_IP="${TOP_IP:-none}"
# nginx writes: limiting requests, excess: 5.976 by zone "ebc_scan", client: ...
# Note the SPACE before the quote -- it is `zone "x"`, not `zone="x"`.

ZONE_BREAKDOWN="$(grep -o 'zone "[a-z_]*"' "$ERROR_CHUNK" 2>/dev/null \
  | sed 's/zone "//; s/"//' | sort | uniq -c | sort -rn \
  | awk '{printf "%s=%s ", $2, $1}' || true)"
ZONE_BREAKDOWN="${ZONE_BREAKDOWN:-none}"

if [[ "$AUTH_FAIL_TOTAL" -ge "$ALERT_AUTHFAIL_THRESHOLD" ]]; then
  notify "auth_total" \
    "Auth failure spike: ${AUTH_FAIL_TOTAL} x 401/403 since last check (threshold ${ALERT_AUTHFAIL_THRESHOLD}). Worst IP ${TOP_IP} with ${TOP_COUNT}. See RUNBOOK.md -> 401/403 spike."
fi

if [[ "$TOP_COUNT" -ge "$ALERT_PER_IP_THRESHOLD" ]]; then
  notify "auth_per_ip" \
    "Single IP ${TOP_IP} produced ${TOP_COUNT} x 401/403 since last check (threshold ${ALERT_PER_IP_THRESHOLD}). Possible credential stuffing. See RUNBOOK.md -> 401/403 spike."
fi

if [[ "$RATE_LIMIT_TOTAL" -ge "$ALERT_THROTTLE_THRESHOLD" ]]; then
  notify "rate_limit" \
    "Rate limiting is rejecting heavily: ${RATE_LIMIT_TOTAL} x 429 since last check (threshold ${ALERT_THROTTLE_THRESHOLD}). Zones: ${ZONE_BREAKDOWN}. See RUNBOOK.md -> 429 spike."
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') [OK] auth_fail=${AUTH_FAIL_TOTAL} (worst ip ${TOP_IP}=${TOP_COUNT}) rate_limited=${RATE_LIMIT_TOTAL} zones: ${ZONE_BREAKDOWN}"
