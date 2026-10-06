import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from test_workflows import Client, ops
import backup
import notification_center as notices
from staffing import WIB


def start_server(name):
    temp = tempfile.TemporaryDirectory()
    ops.DB_PATH = Path(temp.name) / name
    ops.DEMO_MODE = True
    ops.initialize(seed=True)
    httpd = ops.ThreadingHTTPServer(("127.0.0.1", 0), ops.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return temp, httpd, thread, f"http://127.0.0.1:{httpd.server_port}"


class LoginThrottleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp, cls.httpd, cls.thread, cls.base = start_server("login.sqlite3")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(); cls.temp.cleanup()

    def setUp(self):
        with ops.get_db() as conn:
            conn.execute("DELETE FROM login_attempts")
        os.environ.pop("LOGIN_MAX_FAILURES", None); os.environ.pop("LOGIN_IP_MAX_FAILURES", None)

    def attempt(self, email, password):
        return Client(self.base).request("/api/login", "POST", {"email": email, "password": password})

    def test_locks_after_repeated_failures_even_for_the_right_password(self):
        for _ in range(5):
            self.assertEqual(self.attempt("fikri@captureit.local", "salah")[0], 401)
        status, headers, body = self.attempt("fikri@captureit.local", "demo1234")   # correct password, but locked
        self.assertEqual(status, 429)
        self.assertGreater(int(headers["Retry-After"]), 0)
        self.assertIn("menit", json.loads(body)["error"])
        self.assertEqual(self.attempt("naya@captureit.local", "demo1234")[0], 200)  # other accounts are unaffected

    def test_unknown_email_is_throttled_the_same_way(self):
        for _ in range(5):
            self.assertEqual(self.attempt("tidak.ada@captureit.local", "x")[0], 401)
        self.assertEqual(self.attempt("tidak.ada@captureit.local", "x")[0], 429)

    def test_success_clears_the_counter(self):
        for _ in range(4):
            self.attempt("fikri@captureit.local", "salah")
        self.assertEqual(self.attempt("fikri@captureit.local", "demo1234")[0], 200)
        for _ in range(4):
            self.assertEqual(self.attempt("fikri@captureit.local", "salah")[0], 401)   # fresh budget, not locked yet

    def test_lock_expires(self):
        for _ in range(5):
            self.attempt("fikri@captureit.local", "salah")
        with ops.get_db() as conn:
            conn.execute("UPDATE login_attempts SET attempted_at=attempted_at-1000")   # 16m40s ago, window is 15m
        self.assertEqual(self.attempt("fikri@captureit.local", "demo1234")[0], 200)

    def test_one_ip_guessing_many_accounts_is_locked(self):
        os.environ["LOGIN_IP_MAX_FAILURES"] = "4"
        for index in range(4):
            self.assertEqual(self.attempt(f"orang{index}@captureit.local", "x")[0], 401)
        self.assertEqual(self.attempt("naya@captureit.local", "demo1234")[0], 429)

    def test_lock_is_audited_once(self):
        with ops.get_db() as conn:
            conn.execute("DELETE FROM audit_logs WHERE action='login_locked'")
        for _ in range(7):
            self.attempt("fikri@captureit.local", "salah")
        with ops.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='login_locked'").fetchone()[0], 1)


class SyncNoticeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp, cls.httpd, cls.thread, cls.base = start_server("sync.sqlite3")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(); cls.temp.cleanup()

    def setUp(self):
        with ops.get_db() as conn:
            conn.execute("DELETE FROM notifications")
            conn.execute("UPDATE google_calendar_sync_state SET status='success' WHERE id=1")

    def run_sync(self, ok):
        original = ops.google_sync
        ops.google_sync = lambda conn: {"ok": ok, "status": 200 if ok else 502,
                                        "message": "Sinkron OK." if ok else "Google Calendar belum dapat disinkronkan (HTTPError). HTTP 400 invalid_grant."}
        try:
            return ops.run_google_sync("manual")
        finally:
            ops.google_sync = original

    def inbox(self, email):
        client = Client(self.base); client.login(email)
        status, _, body = client.request("/api/notifications")
        self.assertEqual(status, 200, body)
        return json.loads(body)["items"]

    def test_failure_notifies_administrators_only_once_a_day(self):
        self.run_sync(False); self.run_sync(False); self.run_sync(False)
        admin = [i for i in self.inbox("admin@captureit.local") if "Google Calendar" in i["title"]]
        self.assertEqual(len(admin), 1)
        self.assertIn("invalid_grant", admin[0]["body"]); self.assertEqual(admin[0]["tone"], "red")
        self.assertEqual([i for i in self.inbox("coordinator@captureit.local") if "Google Calendar" in i["title"]], [])
        self.assertEqual([i for i in self.inbox("crew@captureit.local") if "Google Calendar" in i["title"]], [])

    def test_recovery_is_announced_once_and_only_after_a_failure(self):
        self.run_sync(True)   # success after success: silent
        self.assertEqual([i for i in self.inbox("admin@captureit.local") if "pulih" in i["title"]], [])
        self.run_sync(False)
        self.run_sync(True)
        self.run_sync(True)
        self.assertEqual(len([i for i in self.inbox("admin@captureit.local") if "pulih" in i["title"]]), 1)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "data" / "ops.sqlite3"
        self.db.parent.mkdir()
        conn = sqlite3.connect(self.db)
        conn.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO users(name) VALUES('a'),('b');""")
        conn.commit(); conn.close()
        self.photos = self.db.parent / "attendance-photos"
        self.photos.mkdir()
        (self.photos / "one.jpg").write_bytes(b"photo-one")
        self._env = {k: os.environ.get(k) for k in ("OPS_DB_PATH", "BACKUP_DIR", "BACKUP_COPY_DIR", "BACKUP_KEEP_DAYS", "ATTENDANCE_STORAGE_DIR")}
        os.environ["OPS_DB_PATH"] = str(self.db)
        os.environ["BACKUP_DIR"] = str(root / "backups")
        for key in ("BACKUP_COPY_DIR", "BACKUP_KEEP_DAYS", "ATTENDANCE_STORAGE_DIR"):
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._env.items():
            os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)
        self.temp.cleanup()

    def test_backup_contains_database_photos_and_manifest_and_verifies(self):
        path = backup.make_backup()
        manifest = backup.verify_backup(path, quiet=True)
        with zipfile.ZipFile(path) as archive:
            self.assertIn("ops.sqlite3", archive.namelist())
            self.assertIn("files/attendance-photos/one.jpg", archive.namelist())
        self.assertEqual(manifest["folders"]["attendance-photos"], 1)

    def test_backup_is_consistent_while_the_database_is_in_use(self):
        writer = sqlite3.connect(self.db)
        writer.execute("PRAGMA journal_mode=WAL"); writer.execute("INSERT INTO users(name) VALUES('wal-only')"); writer.commit()
        path = backup.make_backup()   # the row sits in the -wal file, not yet in the main file
        writer.close()
        with zipfile.ZipFile(path) as archive:
            restored = Path(self.temp.name) / "peek.sqlite3"
            restored.write_bytes(archive.read("ops.sqlite3"))
        self.assertEqual(sqlite3.connect(restored).execute("SELECT COUNT(*) FROM users").fetchone()[0], 3)

    def test_corrupted_backup_is_rejected(self):
        path = backup.make_backup()
        broken = path.with_name("ops-backup-broken.zip")
        data = bytearray(path.read_bytes()); data[len(data) // 2] ^= 0xFF
        broken.write_bytes(bytes(data))
        with self.assertRaises(SystemExit):
            backup.verify_backup(broken, quiet=True)
        with self.assertRaises(SystemExit):
            backup.verify_backup(Path(self.temp.name) / "nope.zip", quiet=True)

    def test_restore_brings_back_old_data_and_keeps_the_current_database_aside(self):
        path = backup.make_backup()
        conn = sqlite3.connect(self.db); conn.execute("DELETE FROM users"); conn.commit(); conn.close()
        (self.photos / "one.jpg").unlink()
        (self.photos / "newer.jpg").write_bytes(b"made after backup")
        backup.restore_backup(path)
        self.assertEqual(sqlite3.connect(self.db).execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)
        self.assertEqual((self.photos / "one.jpg").read_bytes(), b"photo-one")
        self.assertEqual((self.photos / "newer.jpg").read_bytes(), b"made after backup")   # newer files are never overwritten
        self.assertTrue(list(self.db.parent.glob("ops.sqlite3.before-restore-*")))

    def test_retention_removes_old_backups_but_always_keeps_the_newest_three(self):
        out = Path(os.environ["BACKUP_DIR"]); out.mkdir()
        old = time.time() - 40 * 86400
        for index in range(6):
            f = out / f"ops-backup-2026010{index}-000000.zip"; f.write_bytes(b"x"); os.utime(f, (old + index, old + index))
        backup.prune(out)
        self.assertEqual([p.name for p in backup.backups(out)],
                         [f"ops-backup-2026010{i}-000000.zip" for i in (3, 4, 5)])

    def test_second_destination_receives_a_copy(self):
        copy_dir = Path(self.temp.name) / "gdrive"
        os.environ["BACKUP_COPY_DIR"] = str(copy_dir)
        path = backup.make_backup()
        self.assertTrue((copy_dir / path.name).exists())


class ManualEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp, cls.httpd, cls.thread, cls.base = start_server("manual.sqlite3")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(); cls.temp.cleanup()

    def login(self, email):
        client = Client(self.base); client.login(email); return client

    def create(self, client, expected=200, **extra):
        body = {"title": "Uji coba mapping", "location": "JCC", "starts_at": "2040-07-01T09:00", "ends_at": "2040-07-03T18:00", **extra}
        status, _, raw = client.request("/api/events/manual", "POST", body)
        self.assertEqual(status, expected, raw.decode())
        return json.loads(raw)

    def test_coordinator_creates_a_multiday_event_without_calendar(self):
        coordinator = self.login("coordinator@captureit.local")
        made = self.create(coordinator, project_code="TEST-MANUAL-1")
        self.assertEqual(made["project_code"], "TEST-MANUAL-1")
        status, _, raw = coordinator.request(f"/api/events/{made['event_id']}")
        detail = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertTrue(detail["is_manual"]); self.assertIsNone(detail["google_event_id"])
        self.assertTrue(detail["multi_day"]); self.assertEqual(detail["event_days"], ["2040-07-01", "2040-07-02", "2040-07-03"])
        status, _, raw = coordinator.request("/api/bootstrap")
        self.assertIn(made["event_id"], [e["id"] for e in json.loads(raw)["events"]])

    def test_temporary_code_when_none_given_and_validation(self):
        coordinator = self.login("coordinator@captureit.local")
        self.assertTrue(self.create(coordinator)["project_code"].startswith("OPS-"))
        self.create(coordinator, expected=400, title="x")
        self.create(coordinator, expected=400, ends_at="2040-06-30T09:00")                    # ends before it starts
        self.create(coordinator, expected=400, ends_at="2040-08-30T09:00")                    # longer than 14 days
        self.create(coordinator, expected=400, starts_at="bukan tanggal")
        self.create(coordinator, expected=400, project_code="a b")
        self.create(coordinator, project_code="TEST-DUP-1")
        self.create(coordinator, expected=400, project_code="test-dup-1")                    # codes are unique, case-insensitive

    def test_only_permitted_roles_can_create(self):
        self.create(self.login("crew@captureit.local"), expected=403)
        self.create(self.login("finance@captureit.local"), expected=403)

    def test_only_administrator_can_delete_and_only_manual_events(self):
        coordinator, admin = self.login("coordinator@captureit.local"), self.login("admin@captureit.local")
        eid = self.create(coordinator)["event_id"]
        self.assertEqual(coordinator.request(f"/api/events/{eid}/delete-manual", "POST", {"confirm": True})[0], 403)
        self.assertEqual(admin.request(f"/api/events/{eid}/delete-manual", "POST", {})[0], 400)                 # needs confirmation
        with ops.get_db() as conn:
            calendar_event = conn.execute("SELECT id FROM events WHERE is_manual=0 LIMIT 1").fetchone()[0]
        self.assertEqual(admin.request(f"/api/events/{calendar_event}/delete-manual", "POST", {"confirm": True})[0], 400)
        self.assertEqual(admin.request(f"/api/events/{eid}/delete-manual", "POST", {"confirm": True})[0], 200)
        self.assertEqual(admin.request(f"/api/events/{eid}")[0], 404)

    def test_event_with_crew_and_attendance_rows_can_be_deleted_but_not_after_payroll_claim(self):
        coordinator, admin = self.login("coordinator@captureit.local"), self.login("admin@captureit.local")
        eid = self.create(coordinator)["event_id"]
        with ops.get_db() as conn:
            crew = conn.execute("SELECT id FROM users WHERE email='crew@captureit.local'").fetchone()[0]
        status, _, raw = coordinator.request(f"/api/events/{eid}/assignment", "POST", {"user_id": crew, "assignment_type": "crew", "skill_ids": [], "days": ["2040-07-01", "2040-07-02"]})
        self.assertEqual(status, 200, raw)
        aid = json.loads(raw)["assignment_id"]
        with ops.get_db() as conn:
            batch = conn.execute("INSERT INTO payroll_batches(period,status,created_by) VALUES('t','exported',?)", (crew,)).lastrowid
            conn.execute("INSERT INTO payroll_assignment_claims(assignment_id,batch_id) VALUES(?,?)", (aid, batch))
        self.assertEqual(admin.request(f"/api/events/{eid}/delete-manual", "POST", {"confirm": True})[0], 400)
        with ops.get_db() as conn:
            conn.execute("DELETE FROM payroll_assignment_claims WHERE assignment_id=?", (aid,))
        self.assertEqual(admin.request(f"/api/events/{eid}/delete-manual", "POST", {"confirm": True})[0], 200)
        with ops.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM event_assignment_days WHERE assignment_id=?", (aid,)).fetchone()[0], 0)

    def test_calendar_sync_never_touches_manual_events(self):
        coordinator = self.login("coordinator@captureit.local")
        eid = self.create(coordinator, project_code="TEST-SYNC-1")["event_id"]
        original = ops.google_sync
        ops.google_sync = lambda conn: {"ok": True, "status": 200, "message": "ok"}
        try:
            ops.run_google_sync("manual")
        finally:
            ops.google_sync = original
        with ops.get_db() as conn:
            self.assertEqual(conn.execute("SELECT project_code FROM events WHERE id=?", (eid,)).fetchone()[0], "TEST-SYNC-1")


if __name__ == "__main__":
    unittest.main()
