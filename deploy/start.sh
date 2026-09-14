#!/usr/bin/env bash
# Build and start API + MongoDB on the server.
#
# Usage:
#   bash deploy/start.sh           # uses DEPLOY_ENV from .env (dev or prod)
#   bash deploy/start.sh --dev     # force dev compose (bridge networking)
#   bash deploy/start.sh --prod    # force prod compose (host networking)
#   sudo env DEPLOY_ENV=prod bash deploy/start.sh --dev
#     keep the dev compose file, but hide /docs as prod would

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

CLI_DEPLOY_ENV=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)
      CLI_DEPLOY_ENV=dev
      shift
      ;;
    --prod)
      CLI_DEPLOY_ENV=prod
      shift
      ;;
    -h|--help)
      echo "Usage: bash deploy/start.sh [--dev|--prod]"
      echo "  --dev / --prod selects the compose file (networking)."
      echo "  Prefix DEPLOY_ENV=prod to hide Swagger while still using --dev:"
      echo "    sudo env DEPLOY_ENV=prod bash deploy/start.sh --dev"
      exit 0
      ;;
    *)
      echo "Unknown option: $1 (try --dev, --prod, or --help)"
      exit 1
      ;;
  esac
done

if [[ ! -f .env ]]; then
  echo "Missing .env in repo root. Copy deploy/.env.dev.example or deploy/.env.production.example to .env and edit it."
  exit 1
fi

# Remember a caller override (sudo env DEPLOY_ENV=prod ...) before .env overwrites it.
CALLER_DEPLOY_ENV="${DEPLOY_ENV:-}"

set -a
# shellcheck disable=SC1091
# Strip Windows CRLF if .env was uploaded from Windows
source <(sed 's/\r$//' .env)
set +a

# --dev/--prod picks the compose file. App DEPLOY_ENV can differ for docs-gate tests.
COMPOSE_SELECTOR="${CLI_DEPLOY_ENV:-${DEPLOY_ENV:-prod}}"
case "$COMPOSE_SELECTOR" in
  dev)
    COMPOSE_FILE="deploy/docker-compose.dev.yml"
    PUBLIC_DOCS_URL="https://focms.megaannum.ai:8001/docs"
    ;;
  prod)
    COMPOSE_FILE="deploy/docker-compose.prod.yml"
    PUBLIC_DOCS_URL="https://ebc.megaannum.ai/docs"
    ;;
  *)
    echo "Invalid DEPLOY_ENV=$COMPOSE_SELECTOR (use dev or prod in .env, or pass --dev / --prod)"
    exit 1
    ;;
esac

if [[ -n "$CALLER_DEPLOY_ENV" ]]; then
  DEPLOY_ENV="$CALLER_DEPLOY_ENV"
elif [[ -n "$CLI_DEPLOY_ENV" ]]; then
  DEPLOY_ENV="$CLI_DEPLOY_ENV"
fi
DEPLOY_ENV="${DEPLOY_ENV:-$COMPOSE_SELECTOR}"

FIREBASE_CREDS="${FIREBASE_CREDENTIALS_PATH:-./firebase-service-account.json}"
# Compose resolves relative volume paths from deploy/, not repo root — use absolute path.
if [[ "$FIREBASE_CREDS" != /* ]]; then
  FIREBASE_CREDS="$ROOT_DIR/${FIREBASE_CREDS#./}"
fi
if [[ -d "$FIREBASE_CREDS" ]]; then
  echo "ERROR: $FIREBASE_CREDS is a directory (Docker often creates this when the JSON was missing)."
  echo "  rm -rf '$FIREBASE_CREDS' then re-upload the file via scp."
  exit 1
fi
if [[ ! -f "$FIREBASE_CREDS" ]]; then
  echo "Missing Firebase service account at $FIREBASE_CREDS (FIREBASE_CREDENTIALS_PATH in .env)."
  exit 1
fi

if [[ -z "${MONGO_ROOT_USERNAME:-}" || -z "${MONGO_ROOT_PASSWORD:-}" ]]; then
  echo "Missing MONGO_ROOT_USERNAME or MONGO_ROOT_PASSWORD in .env"
  exit 1
fi

SECRETS_DIR="$ROOT_DIR/secrets"
mkdir -p "$SECRETS_DIR"
cp "$FIREBASE_CREDS" "$SECRETS_DIR/firebase-service-account.json"

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  DOCKER=(sudo docker)
fi

# Docker Compose reads --env-file literally; strip CRLF and pass secrets dir (absolute path).
ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
sed 's/\r$//' .env | grep -v '^SECRETS_BIND_MOUNT=' | grep -v '^DEPLOY_ENV=' > "$ENV_FILE"
printf 'SECRETS_BIND_MOUNT=%s\n' "$SECRETS_DIR" >> "$ENV_FILE"
printf 'DEPLOY_ENV=%s\n' "$DEPLOY_ENV" >> "$ENV_FILE"

echo "Compose: $COMPOSE_SELECTOR → $COMPOSE_FILE"
echo "App DEPLOY_ENV=$DEPLOY_ENV (Swagger $([ "$DEPLOY_ENV" = prod ] && echo disabled || echo enabled))"
"${DOCKER[@]}" compose \
  --project-directory "$ROOT_DIR" \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  up -d --build --force-recreate

BACKUP_ENABLED="${BACKUP_ENABLED:-false}"
shopt -s nocasematch
if [[ "$BACKUP_ENABLED" == "true" || "$BACKUP_ENABLED" == "1" ]]; then
  shopt -u nocasematch
  BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
  mkdir -p "$BACKUP_DIR"

  CRON_LINE="*/15 * * * * cd $ROOT_DIR && bash deploy/backup_mongo.sh >> $BACKUP_DIR/cron.log 2>&1"
  if crontab -l 2>/dev/null | grep -Fq "deploy/backup_mongo.sh"; then
    echo "Crontab entry for scheduled backups already present, skipping."
  else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "Registered crontab entry for scheduled backups (every 15 min, backs up at BACKUP_HOUR=${BACKUP_HOUR:-3} into $BACKUP_DIR)."
  fi
else
  shopt -u nocasematch
  echo "BACKUP_ENABLED is not true — skipping backup directory setup and crontab registration."
fi

echo ""
echo "Repo: $ROOT_DIR"
echo "API listening on http://127.0.0.1:8002 (localhost only)."
echo "Health: curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:8002/health"
if [[ "$DEPLOY_ENV" != "prod" ]]; then
  echo "Docs:   curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:8002/docs"
  echo "Public docs URL: $PUBLIC_DOCS_URL"
else
  echo "Swagger docs are disabled on prod. Use /health for connectivity."
fi
