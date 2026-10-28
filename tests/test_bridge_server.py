"""
bridge/server.py auth, with a fake MetaTrader5 module (MT5 is Windows-only).

Needs fastapi + httpx; skipped otherwise.

Run:
    python -m unittest tests.test_bridge_server -v
"""

import importlib
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

BRIDGE_DIR = Path(__file__).resolve().parent.parent / "bridge"
TOKEN = "s" * 48

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


def fake_mt5() -> types.ModuleType:
    mt5 = types.ModuleType("MetaTrader5")
    for name in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
        setattr(mt5, f"TIMEFRAME_{name}", name)
    mt5.initialize = lambda: True
    mt5.login = lambda *a: True
    mt5.shutdown = lambda: None
    mt5.last_error = lambda: (0, "")
    mt5.terminal_info = lambda: object()
    mt5.account_info = lambda: types.SimpleNamespace(name="test", balance=1000.0, equity=1000.0, margin=0.0)
    mt5.symbol_select = lambda *a: True

    def copy_rates_range(symbol, tf, start, end):
        import numpy as np
        t0, t1 = int(start.timestamp()), int(end.timestamp())
        times = range(t0 - t0 % 60, t1 + 1, 60)
        dt = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"), ("tick_volume", "i8")]
        return np.array([(t, 1.1, 1.2, 1.0, 1.15, 5) for t in times if t >= t0], dtype=dt)

    mt5.copy_rates_range = copy_rates_range
    return mt5


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class BridgeServerAuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules["MetaTrader5"] = fake_mt5()
        sys.path.insert(0, str(BRIDGE_DIR))
        with mock.patch.dict(os.environ, {"BRIDGE_TOKEN": TOKEN}), mock.patch("dotenv.load_dotenv"):
            sys.modules.pop("server", None)
            cls.server = importlib.import_module("server")
        cls.client = TestClient(cls.server.app)

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(BRIDGE_DIR))
        sys.modules.pop("server", None)
        sys.modules.pop("MetaTrader5", None)

    def test_health_with_token(self):
        r = self.client.get("/health", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_history_range(self):
        h = {"Authorization": f"Bearer {TOKEN}"}
        r = self.client.get("/history/EURUSD/M1", params={"start": 1_700_000_040, "end": 1_700_000_040 + 600}, headers=h)
        self.assertEqual(r.status_code, 200)
        bars = r.json()
        self.assertEqual(len(bars), 11)
        self.assertEqual(bars[0]["time"], 1_700_000_040)
        self.assertEqual(set(bars[0]), {"time", "open", "high", "low", "close", "volume"})
        bad = self.client.get("/history/EURUSD/M1", params={"start": 10, "end": 5}, headers=h)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get("/history/EURUSD/M1", params={"start": 1, "end": 2}).status_code, 401)

    def test_missing_token(self):
        with self.assertLogs("bridge", "WARNING") as logs:
            r = self.client.get("/health")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["www-authenticate"], "Bearer")
        self.assertIn("missing token", logs.output[0])

    def test_wrong_token(self):
        for header in (f"Bearer {TOKEN}x", "Bearer ", f"Basic {TOKEN}", TOKEN):
            with self.subTest(header=header), self.assertLogs("bridge", "WARNING"):
                r = self.client.get("/account", headers={"Authorization": header})
                self.assertEqual(r.status_code, 401)

    def test_post_requires_token(self):
        with self.assertLogs("bridge", "WARNING"):
            r = self.client.post("/order", json={"symbol": "EURUSD", "side": "buy", "volume": 0.01})
        self.assertEqual(r.status_code, 401)

    def test_logs_cloudflare_client_ip(self):
        with self.assertLogs("bridge", "WARNING") as logs:
            self.client.get("/health", headers={"CF-Connecting-IP": "203.0.113.7"})
        self.assertIn("203.0.113.7", logs.output[0])

    def test_docs_disabled(self):
        for path in ("/docs", "/openapi.json"):
            r = self.client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
            self.assertEqual(r.status_code, 404)

    def test_no_cors(self):
        r = self.client.options("/health", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "GET",
        })
        self.assertNotIn("access-control-allow-origin", r.headers)


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class BridgeServerStartupTest(unittest.TestCase):
    """The bridge must refuse to start without a strong token."""

    def start(self, token):
        # Fresh interpreter so the module-level check runs for real
        code = (
            "import sys, types; m = types.ModuleType('MetaTrader5');"
            "[setattr(m, 'TIMEFRAME_' + n, n) for n in ('M1','M5','M15','M30','H1','H4','D1')];"
            "sys.modules['MetaTrader5'] = m;"
            "import dotenv; dotenv.load_dotenv = lambda *a, **k: None;"
            f"sys.path.insert(0, {str(BRIDGE_DIR)!r}); import server"
        )
        env = {k: v for k, v in os.environ.items() if k != "BRIDGE_TOKEN"}
        if token is not None:
            env["BRIDGE_TOKEN"] = token
        return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)

    def test_missing_token_exits(self):
        r = self.start(None)
        self.assertEqual(r.returncode, 1)
        self.assertIn("refusing to start", r.stderr)

    def test_short_token_exits(self):
        self.assertEqual(self.start("x" * 31).returncode, 1)

    def test_valid_token_starts(self):
        r = self.start("x" * 32)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
