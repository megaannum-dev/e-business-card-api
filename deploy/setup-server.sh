#!/usr/bin/env bash
# One-time setup on Ubuntu 22.04+ (Alibaba Simple Application Server).
# Run as root or with sudo: bash deploy/setup-server.sh

set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/setup-server.sh"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg nginx certbot python3-certbot-nginx git ufw

# Docker Engine + Compose plugin (official convenience script)
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

# Firewall: SSH + HTTP + HTTPS only
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 8001/tcp
ufw --force enable

echo ""
echo "Done. Next steps:"
echo "  1. Clone the repo to /opt/e-business-card-api (or upload files)"
echo "  2. Copy .env and firebase-service-account.json into the repo root"
echo "  3. bash deploy/start.sh"
echo "  4. nginx config: see the nginx section of deploy/DEPLOY.md (conf.d layout, three files)"
echo "  5. Open Alibaba firewall port 8001"
echo "  6. Test https://focms.megaannum.ai:8001/docs"
