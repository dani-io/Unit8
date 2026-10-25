# MT5 Bridge

Thin REST wrapper around the MetaTrader5 Python package.
Runs on a **Windows VM** next to the MT5 terminal, so Unit8 on Linux
can trade through MT5 over HTTPS — published via a Cloudflare Tunnel
behind Cloudflare Access (see [CLOUDFLARE.md](CLOUDFLARE.md)).

No business logic lives here — prices and volumes pass through as-is.

## Setup (on the Windows VM)

1. Install and log in to the MT5 terminal (enable *Algo Trading*).
2. Install Python 3.10+ and the dependencies:

   ```
   pip install -r requirements.txt
   ```

3. Create a `.env` file next to `server.py` (copy `.env.example`):

   ```
   MT5_ACCOUNT=12345678
   MT5_PASSWORD=your_password
   MT5_SERVER=YourBroker-ECN
   BRIDGE_TOKEN=<32+ char random token>
   ```

   Generate the token with:

   ```
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   The bridge refuses to start if `BRIDGE_TOKEN` is missing or shorter than 32 chars.

4. Run the bridge — **localhost only**; `cloudflared` forwards to it:

   ```
   uvicorn server:app --host 127.0.0.1 --port 8000
   ```

5. Set up the tunnel and Access application: [CLOUDFLARE.md](CLOUDFLARE.md).

6. Test from the Linux box:

   ```
   curl https://bridge.example.com/health \
     -H "Authorization: Bearer $BRIDGE_TOKEN" \
     -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
     -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
   # {"status": "ok", "mt5_connected": true}
   ```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Bridge + MT5 connection status |
| GET | `/account` | Balance, equity, margin |
| GET | `/tick/{symbol}` | Current bid/ask/last |
| GET | `/symbol/{symbol}` | Point, tick value/size, volume limits, spread |
| GET | `/ohlcv/{symbol}/{timeframe}?count=512` | OHLCV bars (M1, M5, M15, M30, H1, H4, D1) |
| GET | `/positions?symbol=` | Open positions (optional symbol filter) |
| POST | `/order` | `{symbol, side, volume, sl?, tp?}` → market order |
| POST | `/close` | `{ticket}` → close position |
| POST | `/modify` | `{ticket, sl?, tp?}` → modify SL/TP |

Returns **503** when MT5 is not connected (it retries the connection once per request).

All endpoints, including `/health`, require `Authorization: Bearer <BRIDGE_TOKEN>`
and return **401** otherwise. Interactive docs (`/docs`, `/openapi.json`) are disabled.

## Security

Two layers:

1. **Cloudflare Access** — a Service Auth policy only lets requests with a valid
   service token (`CF-Access-Client-Id` / `CF-Access-Client-Secret`) reach the tunnel.
2. **Bearer token** — the bridge itself checks `BRIDGE_TOKEN` (constant-time compare)
   and logs every failed attempt with the client IP.

The bridge binds to `127.0.0.1` only, so nothing on the network can reach it
except through `cloudflared`. Never bind it to `0.0.0.0` or open port 8000 in the firewall.
