# Deploy to Alibaba Cloud (CMS server, `focms.megaannum.ai:8001`)

Runs **API + MongoDB** in Docker alongside the existing CMS on the same server.

## Architecture

```
https://focms.megaannum.ai      (443) → CMS        → 127.0.0.1:8000
https://focms.megaannum.ai:8001 (8001) → EBC API  → 127.0.0.1:8002 (Docker)
```

Mobile app production URL: `https://focms.megaannum.ai:8001`

## Files in `deploy/`

| File | Purpose |
|------|---------|
| `docker-compose.dev.yml` | Dev server — bridge networking (`focms.megaannum.ai:8001`) |
| `docker-compose.prod.yml` | Prod server — host networking (`ebc.megaannum.ai`; fixes broken Docker DNS) |
| `start.sh` | Build and start containers (`DEPLOY_ENV` in `.env`, or `--dev` / `--prod`) |
| `nginx/focms-ebc-8001.conf` | nginx server block: SSL on 8001, per-IP rate limits, security headers |
| `nginx/conf.d/ebc-ratelimit.conf` | Rate limit zones (http-level -- must go in `/etc/nginx/conf.d/`) |
| `nginx/snippets/ebc-proxy.conf` | Shared `proxy_pass` settings included by every location |
| `alert_auth_anomalies.sh` | 401/403 and 429 alerting (cron, every 5 min) |
| `RUNBOOK.md` | Incident triage |
| `.env.dev.example` | Template for **dev** server `.env` (`DEPLOY_ENV=dev`) |
| `.env.production.example` | Template for **prod** server `.env` (`DEPLOY_ENV=prod`) |
| `setup-server.sh` | One-time Docker/nginx install (skip if CMS server already set up) |

**Not in git (upload separately):** `.env`, Firebase service account JSON in repo root.

### Dev vs prod

| | Dev (`DEPLOY_ENV=dev`) | Prod (`DEPLOY_ENV=prod`) |
|---|------------------------|--------------------------|
| Compose | `docker-compose.dev.yml` | `docker-compose.prod.yml` |
| API networking | Docker bridge | Host network (DNS workaround) |
| Public URL | `https://focms.megaannum.ai:8001` | `https://ebc.megaannum.ai` |
| Firebase JSON | `firebase-service-account-dev.json` | `firebase-service-account.json` |

Add `DEPLOY_ENV=dev` or `DEPLOY_ENV=prod` to each server's `.env`, or pass `bash deploy/start.sh --dev` / `--prod`.

---

## 1. Clone on server

```bash
cd /opt
git clone https://github.com/jfung2019/e-business-card-api.git
cd e-business-card-api
```

## 2. Upload secrets from your PC

```powershell
scp C:\Projects\E-business-card\e-business-card-api\.env root@8.217.183.31:/opt/e-business-card-api/
scp C:\Projects\E-business-card\e-business-card-api\firebase-service-account.json root@8.217.183.31:/opt/e-business-card-api/
```

Or on server: `cp deploy/.env.production.example .env` then `nano .env`.

## 3. Install Docker (if needed)

```bash
docker --version || curl -fsSL https://get.docker.com | sh
```

Or: `sudo bash deploy/setup-server.sh` for full one-time setup.

## 4. Start API + MongoDB

```bash
cd /opt/e-business-card-api
chmod +x deploy/*.sh
bash deploy/start.sh
```

Verify:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8002/docs
# → 200
docker ps   # ebc-api, ebc-mongodb
```

## 5. nginx (port 8001)

SSL for `focms.megaannum.ai` should already exist from CMS setup.

This server's nginx has **no `sites-available` / `sites-enabled`** (those are a
Debian convention; `nginx.conf` here includes `/etc/nginx/conf.d/*.conf`).
Install all three files -- the server block alone fails `nginx -t`, because the
rate limit zones are http-level and the proxy snippet is included by every
location:

```bash
sudo mkdir -p /etc/nginx/snippets
sudo cp deploy/nginx/snippets/ebc-proxy.conf      /etc/nginx/snippets/ebc-proxy.conf
sudo cp deploy/nginx/conf.d/ebc-ratelimit.conf    /etc/nginx/conf.d/00-ebc-ratelimit.conf
sudo cp deploy/nginx/focms-ebc-8001.conf          /etc/nginx/conf.d/ebc-api-8001.conf
sudo nginx -t && sudo systemctl reload nginx
```

`conf.d/*.conf` is included in alphabetical order, so the `00-` prefix keeps the
zones ahead of the server block that uses them.

> **Check for duplicates before you edit anything.** Two `server` blocks both on
> `listen 8001` with the same `server_name` silently resolve to whichever nginx
> parses first, and edits to the other file do nothing:
>
> ```bash
> sudo grep -rn "listen 8001" /etc/nginx/ && sudo nginx -t
> ```
>
> A `conflicting server name ... ignored` warning means exactly this. Move the
> loser out of `conf.d/` rather than deleting it.

If cert paths differ, check: `sudo ls /etc/letsencrypt/live/`

## 6. Alibaba firewall

Open TCP **8001** (80 and 443 should already be open for CMS).

## 7. Test

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://focms.megaannum.ai:8001/docs
```

On phone (any network): `https://focms.megaannum.ai:8001/docs`

---

## Updates

```bash
cd /opt/e-business-card-api
git pull
bash deploy/start.sh
```

## Useful commands

| Task | Command |
|------|---------|
| API logs | `docker logs -f ebc-api` |
| Restart | `bash deploy/start.sh` (uses `DEPLOY_ENV` from `.env`) |
| Stop | `docker compose -f deploy/docker-compose.dev.yml down` or `...prod.yml` |
| Renew SSL | `sudo certbot renew --dry-run` |

## Connect to MongoDB (MongoDB Compass, from your PC)

Production MongoDB is **not** on port 8001 (that is the API). Use an **SSH tunnel** to `127.0.0.1:27017` on the server.

**1. On the server** (after `git pull` — compose binds Mongo to localhost only):

```bash
cd /opt/e-business-card-api
git pull
bash deploy/start.sh
docker ps   # ebc-mongodb should show 127.0.0.1:27017->27017/tcp
```

**2. On your PC** — keep this terminal open:

```powershell
ssh -L 27017:127.0.0.1:27017 root@8.217.183.31
```

**3. MongoDB Compass** — connection string (use credentials from the **server** `.env`):

```
mongodb://MONGO_ROOT_USERNAME:MONGO_ROOT_PASSWORD@127.0.0.1:27017/?authSource=admin
```

Then open database **`e_business_card`**. Collections: `captured_cards`, `user_cards`, `share_links`, `fs.files`, `fs.chunks`.

**Compass SSH tunnel (alternative):** Advanced → SSH → host `8.217.183.31`, user `root` → MongoDB host `127.0.0.1`, port `27017`, auth user/password from server `.env`, auth source `admin`.

Do **not** open port 27017 in the Alibaba cloud firewall — SSH tunnel is enough.

---

| Problem | Fix |
|---------|-----|
| `:8001` timeout | Open port 8001 in Alibaba firewall |
| `502 Bad Gateway` | `bash deploy/start.sh`, check `docker logs ebc-api` |
| nginx cert error | Match paths in `/etc/letsencrypt/live/focms.megaannum.ai/` |
| Missing secrets | Ensure `.env` and `firebase-service-account.json` in repo root |
| **413** on card scan (front + back) | nginx default upload limit is 1 MB. After `git pull`, copy `deploy/nginx/focms-ebc-8001.conf` and `sudo nginx -t && sudo systemctl reload nginx` (`client_max_body_size 25m`) |
| App shows **Parsing service is busy** | Upload reached the API but OpenRouter parsing failed. Run `git pull && bash deploy/start.sh` (rebuilds the API container). Check `docker logs --tail 100 ebc-api` for `OpenRouter error` lines. |
| App **Invalid or expired token** (Firebase creds OK) | Container cannot reach `www.googleapis.com` (Docker bridge DNS broken on some Alibaba VPS). Prod compose uses **host networking** for the API so it shares the host resolver. Redeploy: `git pull && bash deploy/start.sh`. Verify: `docker exec ebc-api python -c "import socket; print(socket.getaddrinfo('www.googleapis.com',443)[0][4][0])"` |
