# Cloudflare Tunnel + Access for the MT5 Bridge

The bridge listens on `127.0.0.1:8000` on the Windows VM. `cloudflared` opens an
outbound tunnel to Cloudflare and publishes it as `https://bridge.<domain>`.
Cloudflare Access sits in front of that hostname and only lets through requests
carrying a valid **service token**. The bridge then checks its own bearer token.

```
Unit8 (Linux) ──HTTPS──▶ Cloudflare Access ──tunnel──▶ cloudflared (VM) ──▶ 127.0.0.1:8000 (bridge)
                 CF-Access-Client-Id/Secret                                  Authorization: Bearer
```

No inbound ports are opened on the VM.

Prerequisites: a domain on Cloudflare (DNS managed by Cloudflare) and a
Zero Trust organization (free plan is fine).

---

## 1. Install cloudflared on Windows

In an **Administrator** PowerShell:

```powershell
winget install --id Cloudflare.cloudflared
cloudflared --version
```

(Or download the MSI from <https://github.com/cloudflare/cloudflared/releases>.)

## 2. Create the tunnel and run it as a service

1. Zero Trust dashboard → **Networks → Tunnels → Create a tunnel**.
2. Connector type: **Cloudflared**. Name it e.g. `mt5-bridge`.
3. Choose **Windows** — the dashboard shows an install command with the tunnel token:

   ```powershell
   cloudflared.exe service install <TUNNEL_TOKEN>
   ```

   Run it in the Administrator PowerShell. This registers a `cloudflared` Windows
   service that starts on boot. Check it under `services.msc` or:

   ```powershell
   Get-Service cloudflared
   ```

   The tunnel should show as **Healthy** in the dashboard.

## 3. Route bridge.<domain> to the bridge

In the tunnel's **Public Hostname** tab (called *Published application routes*
in newer dashboards) → **Add**:

| Field | Value |
|-------|-------|
| Subdomain | `bridge` |
| Domain | `<domain>` |
| Service type | `HTTP` |
| URL | `127.0.0.1:8000` |

Cloudflare creates the `bridge.<domain>` DNS record automatically. Use `HTTP`
(not HTTPS) — the hop from `cloudflared` to the bridge never leaves the VM.

> **Do this before step 4 only if you are fine with the hostname being briefly
> public.** The bridge token still protects it, but the safest order is to
> create the Access application (step 4) first, then add the route.

## 4. Create a service token

Zero Trust → **Access → Service Auth → Service Tokens → Create Service Token**.

- Name: `unit8`
- Duration: pick an expiry and put a reminder in your calendar to rotate it.

Copy both values — **the Client Secret is shown only once**:

```
CF_ACCESS_CLIENT_ID=xxxxxxxx.access
CF_ACCESS_CLIENT_SECRET=xxxxxxxxxxxxxxxx
```

## 5. Create the Access application

Zero Trust → **Access → Applications → Add an application → Self-hosted**.

- Application name: `MT5 Bridge`
- Application domain: subdomain `bridge`, domain `<domain>` (no path)
- Session duration: default is fine (service tokens don't use sessions)

Add a policy:

| Field | Value |
|-------|-------|
| Policy name | `unit8 service token` |
| Action | **Service Auth** |
| Include | **Service Token** → `unit8` |

Don't add any *Allow* policy — with only a Service Auth policy, browsers and
anything without the token are blocked at Cloudflare's edge.

## 6. Configure both sides

On the **VM**, `bridge/.env`:

```
BRIDGE_TOKEN=<token>
```

On the **Linux box**, Unit8's `.env`:

```
BRIDGE_URL=https://bridge.<domain>
BRIDGE_TOKEN=<same token>
CF_ACCESS_CLIENT_ID=<client id>
CF_ACCESS_CLIENT_SECRET=<client secret>
```

Generate the bridge token with:

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Start the bridge on the VM (localhost only):

```
uvicorn server:app --host 127.0.0.1 --port 8000
```

## 7. Verify

```bash
# Full credentials → 200 JSON
curl -i https://bridge.<domain>/health \
  -H "Authorization: Bearer $BRIDGE_TOKEN" \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"

# No CF headers → blocked by Access (302 to …cloudflareaccess.com, or 403)
curl -i https://bridge.<domain>/health -H "Authorization: Bearer $BRIDGE_TOKEN"

# CF headers but wrong bearer token → 401 from the bridge
curl -i https://bridge.<domain>/health \
  -H "Authorization: Bearer wrong" \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
```

When Access blocks a request, `BridgeBroker` logs
`Cloudflare Access rejected the request` — check the service token values and
that the policy action is **Service Auth**.

## Troubleshooting

- **502 / "Bad gateway" from Cloudflare** — the tunnel is up but the bridge isn't
  listening. Start uvicorn, and confirm the route URL is `127.0.0.1:8000`.
- **Tunnel not healthy** — `Get-Service cloudflared`; restart with
  `Restart-Service cloudflared`. Logs are in the Windows Event Viewer under
  *Application*.
- **Bridge log shows auth failures from unknown IPs** — someone got past Access
  (or the route was public before step 5). Rotate `BRIDGE_TOKEN` and the service token.
- **Rotating the service token** — create a new token, add it to the policy,
  update Unit8's `.env`, then delete the old token.
