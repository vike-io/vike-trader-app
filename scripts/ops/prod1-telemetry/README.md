# prod1 telemetry receiver — ops kit

Deploys the vike-trader MCP usage-telemetry receiver on **prod1** (Ubuntu 24.04, nginx, no Docker).
No secrets live in these files (the shared token and certbot email come from the environment at
deploy time), so the kit is tracked in the repo under `scripts/ops/`.

## State (as of last run)
- **DNS:** `telemetry.vike.io → 157.90.129.164` (A, DNS-only) — created via Cloudflare API. ✓
- **prod1 staging:** `~/vike-telemetry/` has `app.py`, `deploy.sh` (+ venv) — receiver smoke-tested OK
  (`/health` 200, `POST /telemetry` 204). ✓
- **Remaining:** one root step — wire systemd + nginx + TLS. Needs `sudo` (password-protected,
  so it can't run over the non-interactive SSH session).

## Finish it — ONE command on prod1
```bash
ssh prod1
sudo CERTBOT_EMAIL=you@vike.io bash ~/vike-telemetry/deploy.sh
```
This creates a `vike-telemetry` systemd service (uvicorn on 127.0.0.1:8099), an **isolated** nginx
vhost for `telemetry.vike.io` (rate-limited, 8 KB body cap; the `vike`/`grafana` vhosts are untouched),
and a Let's Encrypt cert. Endpoint: `https://telemetry.vike.io/telemetry`.

Rollback:
```bash
sudo rm /etc/nginx/sites-enabled/telemetry && sudo systemctl reload nginx
sudo systemctl disable --now vike-telemetry
```

## After it's live
Set the app's endpoint so opted-in users report to it:
`connect.DEFAULT_TELEMETRY_URL = "https://telemetry.vike.io/telemetry"` (in the PR), then commit.

## Alternative: fully sudo-free via Cloudflare Tunnel
The current Cloudflare token has **DNS:Edit + Tunnel:Read** but **not Tunnel:Edit** (tunnel-create
returns CF error 10000). Add **Account → Cloudflare Tunnel → Edit** to the token, and the whole thing
can be done with no sudo and no nginx changes (origin IP hidden, automatic TLS): create a tunnel,
route `telemetry.vike.io` (CNAME) → it, run `cloudflared` on prod1 as the user. The current grey
A record would be replaced by the tunnel CNAME.

## Files
- `app.py` — hardened FastAPI receiver (token check, 8 KB cap, JSONL sink).
- `deploy.sh` — the sudo deploy (same copy staged on prod1).

## Client contract (also: running your own receiver)
`app.py` doubles as the reference for self-hosting (it replaced the old
`examples/telemetry_receiver.py` duplicate). The desktop app's LOCAL MCP server POSTs one JSON
event per tool call to `VIKE_TELEMETRY_URL` (see `ai/telemetry.py`) -- **only when the user
has opted in** via the Connect dialog. When `VIKE_TELEMETRY_TOKEN` is set on the receiver,
clients must send the matching `X-Vike-Token` header (the Connect flow injects it). Events are
anonymous/pseudonymous by construction: the client id is a random per-install UUID and strategy
source is never sent (only a sha + length).
