#!/usr/bin/env bash
# vike-trader telemetry receiver — deploy on prod1. Run with sudo. Idempotent + reversible.
# Does NOT touch the existing `vike` / `grafana` nginx vhosts. Optional: export CERTBOT_EMAIL=you@vike.io
# (Staged on prod1 at ~/vike-telemetry/deploy.sh alongside app.py — run there.)
set -euo pipefail
DOMAIN="telemetry.vike.io"; PORT="8099"; SVC_USER="vike-telemetry"
APP_DIR="/opt/vike-telemetry"; SINK_DIR="/var/lib/vike-telemetry"
EMAIL="${CERTBOT_EMAIL:-}"
SRC_APP="$(cd "$(dirname "$0")" && pwd)/app.py"
[ "$(id -u)" -eq 0 ] || { echo "run with sudo: sudo bash $0"; exit 1; }
[ -f "$SRC_APP" ] || { echo "app.py not found beside deploy.sh"; exit 1; }
echo ">> DNS check"; getent hosts "$DOMAIN" >/dev/null || { echo "!! $DOMAIN does not resolve"; exit 1; }
echo ">> user + dirs"
id "$SVC_USER" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$APP_DIR" "$SINK_DIR"; install -m 0644 "$SRC_APP" "$APP_DIR/app.py"
echo ">> venv + deps"
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q fastapi "uvicorn[standard]" pydantic
chown -R "$SVC_USER":"$SVC_USER" "$APP_DIR" "$SINK_DIR"
echo ">> systemd"
cat > /etc/systemd/system/vike-telemetry.service <<UNIT
[Unit]
Description=vike-trader telemetry receiver
After=network.target
[Service]
User=$SVC_USER
WorkingDirectory=$APP_DIR
Environment=VIKE_TELEMETRY_SINK=$SINK_DIR/events.jsonl
ExecStart=$APP_DIR/venv/bin/uvicorn app:app --host 127.0.0.1 --port $PORT
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$SINK_DIR
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload; systemctl enable --now vike-telemetry; sleep 1
curl -fsS "http://127.0.0.1:$PORT/health" && echo " <- service up"
echo ">> nginx vhost (new isolated file)"
cat > /etc/nginx/sites-available/telemetry <<NGINX
limit_req_zone \$binary_remote_addr zone=vike_tel:10m rate=10r/s;
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 8k;
    location / {
        limit_req zone=vike_tel burst=20 nodelay;
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/telemetry /etc/nginx/sites-enabled/telemetry
nginx -t && systemctl reload nginx
echo ">> TLS"
command -v certbot >/dev/null || apt-get install -y certbot python3-certbot-nginx
if [ -n "$EMAIL" ]; then
  certbot --nginx -d "$DOMAIN" --redirect --non-interactive --agree-tos -m "$EMAIL"
else
  certbot --nginx -d "$DOMAIN" --redirect --non-interactive --agree-tos --register-unsafely-without-email
fi
echo ">> verify"; curl -fsS "https://$DOMAIN/health"; echo
echo "DONE -> https://$DOMAIN/telemetry"
echo "rollback: rm /etc/nginx/sites-enabled/telemetry && systemctl reload nginx && systemctl disable --now vike-telemetry"
