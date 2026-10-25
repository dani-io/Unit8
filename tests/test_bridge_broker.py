"""
BridgeBroker against a local stub bridge.

The stub mimics bridge/server.py's auth (bearer token → 401 otherwise) and
can impersonate Cloudflare Access, so these run without MT5 or Cloudflare.

Run:
    python -m unittest tests.test_bridge_broker -v
"""

import json
import logging
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from unittest import mock

from broker.bridge_broker import BridgeBroker

TOKEN = "t" * 48
CF_ID = "client-id.access"
CF_SECRET = "client-secret"


class StubBridge(BaseHTTPRequestHandler):
    # Set per test: None → behave like the bridge; "redirect" / "block" → like Access
    cf_mode = None
    requests_seen = []

    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type="application/json", headers=None):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        StubBridge.requests_seen.append(dict(self.headers))

        if self.cf_mode == "redirect":
            return self._send(302, "", "text/html", {
                "Location": "https://myteam.cloudflareaccess.com/cdn-cgi/access/login/bridge.example.com",
            })
        if self.cf_mode == "block":
            return self._send(403, "<html><title>Forbidden</title>Cloudflare Access</html>", "text/html")

        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"detail": "Unauthorized"})

        if self.path == "/health":
            return self._send(200, {"status": "ok", "mt5_connected": True})
        if self.path == "/account":
            return self._send(200, {"balance": 1000.0, "equity": 1001.0, "margin": 0.0})
        if self.path == "/order":
            return self._send(200, {"ticket": 42, "price": 1.1})
        return self._send(404, {"detail": "Not found"})

    do_GET = _handle
    do_POST = _handle


class BridgeBrokerAuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubBridge)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        StubBridge.cf_mode = None
        StubBridge.requests_seen = []
        # Keep a developer's real credentials out of the tests
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in ("BRIDGE_URL", "BRIDGE_TOKEN", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"):
            os.environ.pop(key, None)

    def broker(self, **config):
        return BridgeBroker({"url": self.url, **config})

    def test_correct_token_succeeds(self):
        b = self.broker(token=TOKEN)
        self.assertTrue(b.connect())
        self.assertEqual(b.get_account_info().balance, 1000.0)
        self.assertEqual(b.place_order("EURUSD", "buy", 0.01), 42)
        for headers in StubBridge.requests_seen:
            self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")

    def test_wrong_token_rejected(self):
        b = self.broker(token="wrong" * 10)
        with self.assertLogs("unit8.broker.bridge", logging.ERROR) as logs:
            self.assertFalse(b.connect())
            self.assertIsNone(b.place_order("EURUSD", "buy", 0.01))
        self.assertIn("Bridge rejected the token", "\n".join(logs.output))

    def test_missing_token_rejected(self):
        with self.assertLogs("unit8.broker.bridge", logging.WARNING) as logs:
            b = self.broker()
            self.assertFalse(b.connect())
        out = "\n".join(logs.output)
        self.assertIn("BRIDGE_TOKEN not set", out)
        self.assertIn("Bridge rejected the token", out)
        self.assertNotIn("Authorization", StubBridge.requests_seen[0])

    def test_cf_headers_sent_when_configured(self):
        b = self.broker(token=TOKEN, cf_access_client_id=CF_ID, cf_access_client_secret=CF_SECRET)
        self.assertTrue(b.connect())
        b.place_order("EURUSD", "buy", 0.01)
        self.assertEqual(len(StubBridge.requests_seen), 2)
        for headers in StubBridge.requests_seen:
            self.assertEqual(headers["CF-Access-Client-Id"], CF_ID)
            self.assertEqual(headers["CF-Access-Client-Secret"], CF_SECRET)

    def test_cf_headers_absent_when_not_configured(self):
        self.assertTrue(self.broker(token=TOKEN).connect())
        self.assertNotIn("CF-Access-Client-Id", StubBridge.requests_seen[0])
        self.assertNotIn("CF-Access-Client-Secret", StubBridge.requests_seen[0])

    def test_cf_access_redirect_detected(self):
        StubBridge.cf_mode = "redirect"
        b = self.broker(token=TOKEN)
        with self.assertLogs("unit8.broker.bridge", logging.ERROR) as logs:
            self.assertFalse(b.connect())
        self.assertIn("Cloudflare Access rejected the request", "\n".join(logs.output))
        # Redirect must not be followed
        self.assertEqual(len(StubBridge.requests_seen), 1)

    def test_cf_access_block_page_detected(self):
        StubBridge.cf_mode = "block"
        b = self.broker(token=TOKEN)
        with self.assertLogs("unit8.broker.bridge", logging.ERROR) as logs:
            self.assertIsNone(b.get_account_info())
        self.assertIn("Cloudflare Access rejected the request", "\n".join(logs.output))

    def test_env_fallback(self):
        env = {"BRIDGE_URL": self.url, "BRIDGE_TOKEN": TOKEN,
               "CF_ACCESS_CLIENT_ID": CF_ID, "CF_ACCESS_CLIENT_SECRET": CF_SECRET}
        with mock.patch.dict(os.environ, env):
            self.assertTrue(BridgeBroker().connect())
        headers = StubBridge.requests_seen[0]
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["CF-Access-Client-Id"], CF_ID)


if __name__ == "__main__":
    unittest.main()
