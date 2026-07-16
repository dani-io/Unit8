# MT5 Bridge

Thin REST wrapper around the MetaTrader5 Python package.
Runs on a **Windows VM** next to the MT5 terminal, so Unit8 on Linux
can trade through MT5 over plain HTTP.

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
   ```

4. Run the bridge:

   ```
   uvicorn server:app --host 0.0.0.0 --port 8000
   ```

5. Test from the Linux box:

   ```
   curl http://<vm-ip>:8000/health
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

## Security

There is **no authentication** — run this only on a private/internal network
(e.g., host-only VM network). Do not expose port 8000 to the internet.
