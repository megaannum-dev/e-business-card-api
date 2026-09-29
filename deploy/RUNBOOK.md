# Incident Runbook — E-Business Card API

Triage steps for the alerts raised by `deploy/alert_auth_anomalies.sh`, plus the
outages you are most likely to hit. Commands first: this is meant to be followed
at 3am, not read.

## Orientation

| | |
|---|---|
| Repo on server | `/opt/e-business-card-api` |
| Containers | `ebc-api`, `ebc-mongodb` |
| Dev URL | `https://focms.megaannum.ai:8001` → `127.0.0.1:8002` |
| Prod URL | `https://ebc.megaannum.ai` |
| nginx server block | `/etc/nginx/conf.d/ebc-api-8001.conf` |
| nginx rate zones | `/etc/nginx/conf.d/00-ebc-ratelimit.conf` |
| nginx proxy snippet | `/etc/nginx/snippets/ebc-proxy.conf` |
| Access log | `/var/log/nginx/ebc-access.log` |
| Error log (rate limits) | `/var/log/nginx/ebc-error.log` |
| Alert log | `/var/log/ebc-alerts.log` |
| Restart the stack | `bash deploy/start.sh --dev` (or `--prod`) |

**Baseline note:** a steady trickle of 401s is normal. The app forces a token
refresh on every request, so expired tokens on resume produce them routinely.
Alert on the spike, not on the occurrence.

---

## ALERT: Auth failure spike (401/403 across all IPs)

**Meaning:** many clients failing auth at once. Usually a broken release or an
expired credential, not an attack — an attack normally shows up on the per-IP
alert below instead.

Who is failing:

```bash
sudo awk '$9 ~ /^(401|403)$/ {print $1}' /var/log/nginx/ebc-access.log | sort | uniq -c | sort -rn | head -20
```

**Spread across many IPs** → suspect a client or server problem, not abuse.

Check the API is authenticating at all — if Firebase credentials are missing the
API returns 503, not 401:

```bash
sudo docker logs ebc-api --tail 100 | grep -i "firebase\|token"
```

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://focms.megaannum.ai:8001/health
```

Confirm the Firebase service account is still mounted and valid:

```bash
sudo docker exec ebc-api python -c "import firebase_admin; from firebase_admin import credentials; firebase_admin.initialize_app(credentials.Certificate('/app/secrets/firebase-service-account.json')); print('credentials OK')"
```

**Concentrated in a few IPs** → treat as the per-IP case below.

---

## ALERT: Single IP producing 401/403 (possible credential stuffing)

**Meaning:** one source is repeatedly failing auth. This is the alert that
matters most — a single attacker stays under the global threshold while standing
out sharply per-IP.

What it is actually hitting:

```bash
sudo grep " 401 \| 403 " /var/log/nginx/ebc-access.log | grep "^THE_IP" | awk '{print $6, $7}' | sort | uniq -c | sort -rn | head
```

Mostly `/api/v1/` with varied paths → scripted probing. A single repeated
endpoint → more likely a stuck client.

**Block the IP** at nginx if it is clearly hostile. Add inside the `server` block
in `/etc/nginx/conf.d/ebc-api-8001.conf`:

```
deny THE_IP;
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

**If a specific account may be compromised**, kill its sessions immediately.
This is what `get_current_user_id_strict` enforces on account deletion, card
deletion, and share-link create/revoke:

```bash
sudo docker exec ebc-api python -c "import firebase_admin; from firebase_admin import auth, credentials; firebase_admin.initialize_app(credentials.Certificate('/app/secrets/firebase-service-account.json')); u=auth.get_user_by_email('USER_EMAIL'); auth.revoke_refresh_tokens(u.uid); print('revoked', u.uid)"
```

The user is signed out on their next request and must sign in again. To lock the
account entirely, disable it in the Firebase Console — that surfaces as "This
account has been disabled."

Note the revocation only takes effect within ~1 hour on non-strict routes, which
keep the fast local check. The five strict routes reject immediately.

---

## ALERT: 429 spike (rate limiting rejecting heavily)

**First question: is this abuse, or a limit set too tight?** The alert names the
zone; that tells you which.

Which zones are firing and for whom:

```bash
sudo grep "limiting requests" /var/log/nginx/ebc-error.log | tail -50
```

```bash
sudo grep "limiting requests" /var/log/nginx/ebc-error.log | grep -o 'client: [0-9.]*' | sort | uniq -c | sort -rn | head
```

**Many distinct IPs → your limits are too tight.** Especially likely on
`ebc_image` (the wallet loads one scan image per card) and `ebc_health` (probed
before every scan). Remember mobile users share carrier NAT addresses, so one IP
can be several people. Widen the zone in
`/etc/nginx/conf.d/00-ebc-ratelimit.conf`, then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

**One or two IPs → abuse.** Leave the limits alone; they are working. Block the
IP as above if it persists.

**`ebc_scan` firing** is the expensive one — those endpoints call the LLM. The
per-user quota in `app/services/llm_rate_limiter.py` (10/hour, 20/day) is the
backstop, so cost is capped even if nginx lets traffic through.

---

## API down / health check failing

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8002/health
```

`200` here but failing publicly → the problem is nginx, not the app:

```bash
sudo nginx -t && sudo systemctl status nginx --no-pager
```

No response from 8002 → the container:

```bash
sudo docker ps -a | grep ebc
```

```bash
sudo docker logs ebc-api --tail 100
```

```bash
cd /opt/e-business-card-api && bash deploy/start.sh --dev
```

MongoDB unhealthy blocks the API from starting (`depends_on: service_healthy`):

```bash
sudo docker exec ebc-mongodb mongosh --eval "db.adminCommand('ping')"
```

Latest backup, if you need to restore:

```bash
ls -lht /opt/e-business-card-api/backups | head
```

---

## Checking what config is actually live

`conf.d/*.conf` is included alphabetically, and **two server blocks on the same
port and server_name will silently shadow each other** — nginx warns and uses the
first. This has bitten this deployment before.

```bash
sudo nginx -T 2>&1 | grep -i "conflicting server name"
```

```bash
sudo nginx -T | grep -A3 "listen 8001"
```

More than one `listen 8001` block means the duplicate is back. Move the extra
file out of `/etc/nginx/conf.d/` — anything not ending in `.conf` is ignored.

---

## Verifying the alerting itself

Run it by hand; it prints an `[OK]` summary every time:

```bash
sudo bash /opt/e-business-card-api/deploy/alert_auth_anomalies.sh
```

```bash
sudo tail -50 /var/log/ebc-alerts.log
```

Thresholds and the cooldown live in `.env` (see the header of
`deploy/alert_auth_anomalies.sh`). To silence a noisy alert while you tune it,
raise its threshold rather than disabling the whole script.

---

## Escalation

| What | Where |
|---|---|
| Firebase (users, disable accounts) | Firebase Console — dev project `mega-e-business-card-dev` |
| Server, firewall, ports | Alibaba Cloud console |
| TLS certificates | `sudo certbot certificates` on the server |
| Repo | `https://github.com/jfung2019/e-business-card-api` |
