import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as ops
import production
from wsgi_support import WSGITestServer


class ProductionTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous_db, cls.previous_demo = ops.DB_PATH, ops.DEMO_MODE
        ops.DB_PATH = Path(cls.temp.name) / "ops.sqlite3"
        ops.DEMO_MODE = True
        ops.initialize()
        cls.httpd = WSGITestServer(("127.0.0.1", 0))
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=3)
        ops.DB_PATH, ops.DEMO_MODE = cls.previous_db, cls.previous_demo
        cls.temp.cleanup()

    def connection(self, port=None):
        conn = http.client.HTTPConnection("127.0.0.1", port or self.httpd.server_port, timeout=5)
        self.addCleanup(conn.close)
        return conn

    def test_login_cookies_and_persistent_connection_framing(self):
        conn = self.connection()
        with patch.dict(os.environ, {"COOKIE_SECURE": "1"}):
            conn.request("POST", "/api/login", json.dumps({"email": "admin@captureit.local", "password": "demo1234"}),
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            cookies = response.headers.get_all("Set-Cookie")
            self.assertEqual(len(cookies), 2)
            self.assertTrue(all("Secure" in cookie for cookie in cookies))
            body = response.read()
            self.assertEqual(int(response.getheader("Content-Length")), len(body))
        # A second request on the same HTTP/1.1 connection must complete.
        conn.request("GET", "/")
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b"html", response.read().lower())

    def test_head_dynamic_and_static_have_no_body(self):
        conn = self.connection()
        for path in ("/", "/push-config.js", "/favicon.ico", "/api/bootstrap"):
            conn.request("HEAD", path)
            response = conn.getresponse()
            self.assertIn(response.status, (200, 401))
            self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
            self.assertEqual(response.read(), b"")

    def test_method_and_request_size_boundaries(self):
        conn = self.connection()
        conn.request("PUT", "/api/profile", body=b"{}")
        response = conn.getresponse()
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "GET, HEAD, POST")
        response.read()
        conn.putrequest("POST", "/api/login")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(production.MAX_BODY_SIZE + 1))
        conn.endheaders()
        response = conn.getresponse()
        self.assertEqual(response.status, 413)
        response.read()

    def test_chunked_json_request_is_decoded_by_waitress(self):
        conn = self.connection()
        body = json.dumps({"email": "admin@captureit.local", "password": "demo1234"}).encode()
        conn.request("POST", "/api/login", body=iter([body[:10], body[10:]]),
                     headers={"Content-Type": "application/json"}, encode_chunked=True)
        response = conn.getresponse()
        self.assertEqual(response.status, 200, response.read())
        response.read()

    def test_static_path_boundaries_and_unicode_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "static"
            root.mkdir()
            (root / "foto-é.txt").write_bytes(b"photo")
            (root / "folder").mkdir()
            secret = Path(tmp) / "secret.txt"
            secret.write_bytes(b"secret")
            (root / "escape.txt").symlink_to(secret)
            (root / ".env").write_bytes(b"secret")
            with patch.object(ops, "STATIC", root):
                conn = self.connection()
                for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/escape.txt", "/.env", "/folder/"):
                    conn.request("GET", path)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 404, path)
                    self.assertNotIn(b"secret", response.read())
                conn.request("GET", "/foto-%C3%A9.txt")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"photo")

    def test_wsgi_response_contract(self):
        env = {}
        setup_testing_defaults(env)
        env["QUERY_STRING"] = ""
        env["wsgi.input"] = io.BytesIO()
        statuses = []
        body = validator(production.application)(env, lambda status, headers: statuses.append(status))
        try:
            self.assertTrue(b"".join(body))
        finally:
            body.close()
        self.assertEqual(statuses, ["200 OK"])

    def test_untrusted_forwarded_address_is_ignored(self):
        conn = self.connection()
        conn.request("POST", "/api/login", json.dumps({"email": "untrusted-proxy@example.test", "password": "bad"}),
                     {"Content-Type": "application/json", "X-Forwarded-For": "198.51.100.9"})
        response = conn.getresponse()
        self.assertEqual(response.status, 401)
        response.read()
        with ops.get_db() as db:
            ip = db.execute("SELECT ip FROM login_attempts WHERE email='untrusted-proxy@example.test'").fetchone()[0]
        self.assertEqual(ip, "127.0.0.1")

    def test_trusted_proxy_uses_only_the_last_forwarded_address(self):
        httpd = WSGITestServer(("127.0.0.1", 0), trusted_proxy="127.0.0.1",
                              trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"}, trusted_proxy_count=1)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            conn = self.connection(httpd.server_port)
            conn.request("POST", "/api/login", json.dumps({"email": "trusted-proxy@example.test", "password": "bad"}),
                         {"Content-Type": "application/json", "X-Forwarded-For": "198.51.100.9, 203.0.113.10"})
            response = conn.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            conn.close()
            with ops.get_db() as db:
                ip = db.execute("SELECT ip FROM login_attempts WHERE email='trusted-proxy@example.test'").fetchone()[0]
            self.assertEqual(ip, "203.0.113.10")
        finally:
            httpd.shutdown()
            thread.join(timeout=3)

    def test_concurrent_reads_complete(self):
        def read(_):
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
            try:
                conn.request("GET", "/push-config.js")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(b"CAPTUREIT_PUSH_CONFIG", response.read())
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(read, range(24)))


class ProductionLifecycleTests(unittest.TestCase):
    def test_background_tasks_start_once(self):
        with patch.object(ops, "BACKGROUND_TASKS_STARTED", False), patch.object(ops.threading, "Thread") as thread:
            ops.start_background_tasks()
            ops.start_background_tasks()
            self.assertEqual(thread.call_count, 2)

    def test_production_refuses_demo_mode_before_initializing(self):
        with patch.object(ops, "configure_runtime"), patch.object(ops, "DEMO_MODE", True), patch.object(ops, "initialize") as init:
            with self.assertRaisesRegex(SystemExit, "DEMO_MODE=false"):
                production.main()
            init.assert_not_called()
