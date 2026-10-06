import base64
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from test_workflows import Client, ops
from staffing import WIB
import eventdays


PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0cam\xff\xd9").decode()


class CalendarBoundsTests(unittest.TestCase):
    def test_all_day_end_is_exclusive(self):
        # Google: 3-4 Oct all-day => start 2026-10-03, end 2026-10-05 (exclusive)
        start, end = ops.calendar_bounds({"date": "2026-10-03"}, {"date": "2026-10-05"})
        self.assertEqual(start, "2026-10-03T00:00:00+07:00")
        self.assertEqual(end, "2026-10-04T23:59:00+07:00")
        event = {"starts_at": start, "ends_at": end}
        self.assertEqual(eventdays.event_dates(event), ["2026-10-03", "2026-10-04"])
        self.assertTrue(eventdays.is_multiday(event))

    def test_single_all_day_stays_one_day(self):
        start, end = ops.calendar_bounds({"date": "2026-10-02"}, {"date": "2026-10-03"})
        self.assertEqual(eventdays.event_dates({"starts_at": start, "ends_at": end}), ["2026-10-02"])
        self.assertFalse(eventdays.is_multiday({"starts_at": start, "ends_at": end}))

    def test_timed_events_keep_their_own_timestamps(self):
        self.assertEqual(ops.calendar_bounds({"dateTime": "2026-10-03T09:00:00+07:00"}, {"dateTime": "2026-10-05T18:00:00+07:00"}),
                         ("2026-10-03T09:00:00+07:00", "2026-10-05T18:00:00+07:00"))

    def test_overnight_event_is_not_multiday(self):
        self.assertFalse(eventdays.is_multiday({"starts_at": "2026-10-03T20:00:00+07:00", "ends_at": "2026-10-04T04:00:00+07:00"}))


class MultiDayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        ops.DB_PATH = Path(cls.temp.name) / "multiday.sqlite3"
        ops.initialize(seed=True)
        cls.httpd = ops.ThreadingHTTPServer(("127.0.0.1", 0), ops.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(); cls.temp.cleanup()

    def setUp(self):
        self.coordinator = Client(self.base); self.coordinator.login("coordinator@captureit.local")
        with ops.get_db() as conn:
            conn.execute("""DELETE FROM payroll_assignment_claims WHERE assignment_id IN
                (SELECT ea.id FROM event_assignments ea JOIN events e ON e.id=ea.event_id WHERE e.project_code LIKE 'md-%')""")
            conn.execute("DELETE FROM events WHERE project_code LIKE 'md-%'")
            conn.execute("DELETE FROM event_assignments WHERE user_id IN (SELECT id FROM users WHERE email IN ('crew@captureit.local','pic@captureit.local'))")
            self.a = conn.execute("SELECT id FROM users WHERE email='crew@captureit.local'").fetchone()[0]
            self.b = conn.execute("SELECT id FROM users WHERE email='pic@captureit.local'").fetchone()[0]
            self.owner = conn.execute("SELECT id FROM users WHERE email='coordinator@captureit.local'").fetchone()[0]
        self.n = 0

    def make_event(self, start, days=3):
        self.n += 1
        end = start + timedelta(days=days) - timedelta(minutes=1)
        with ops.get_db() as conn:
            return conn.execute("""INSERT INTO events(project_code,title,location,starts_at,ends_at,coordinator_id,is_full_day)
                VALUES(?,?,?,?,?,?,1)""", (f"md-{self.n}", f"Expo {self.n}", "JCC", start.isoformat(), end.isoformat(), self.owner)).lastrowid

    def assign(self, event_id, uid, days=None, expected=200, role="crew", **extra):
        body = {"user_id": uid, "assignment_type": role, "skill_ids": [], **extra}
        if days is not None:
            body["days"] = days
        status, _, raw = self.coordinator.request(f"/api/events/{event_id}/assignment", "POST", body)
        self.assertEqual(status, expected, raw.decode())
        return json.loads(raw)

    def detail(self, event_id, client=None):
        status, _, raw = (client or self.coordinator).request(f"/api/events/{event_id}")
        self.assertEqual(status, 200, raw.decode())
        return json.loads(raw)

    def test_day_mapping_swap_crew_between_days(self):
        start = datetime(2040, 6, 1, 0, 0, tzinfo=WIB)
        eid = self.make_event(start, days=2)
        d1, d2 = "2040-06-01", "2040-06-02"
        a = self.assign(eid, self.a, [d1, d2])["assignment_id"]
        b = self.assign(eid, self.b, [d1], role="pic")["assignment_id"]
        detail = self.detail(eid)
        self.assertTrue(detail["multi_day"]); self.assertEqual(detail["event_days"], [d1, d2])
        by = {x["assignment_id"]: x for x in detail["assignments"]}
        self.assertEqual([d["work_date"] for d in by[a]["days"]], [d1, d2])
        self.assertEqual([d["work_date"] for d in by[b]["days"]], [d1])
        # Day 2 changes: A stays on day 1 only, B takes day 2 as well.
        self.assertEqual(self.coordinator.request(f"/api/events/{eid}/assignment", "POST", {"action": "days", "assignment_id": a, "days": [d1]})[0], 200)
        self.assertEqual(self.coordinator.request(f"/api/events/{eid}/assignment", "POST", {"action": "days", "assignment_id": b, "days": [d1, d2]})[0], 200)
        by = {x["assignment_id"]: x for x in self.detail(eid)["assignments"]}
        self.assertEqual([d["work_date"] for d in by[a]["days"]], [d1])
        self.assertEqual([d["work_date"] for d in by[b]["days"]], [d1, d2])

    def test_rejects_days_outside_event_and_empty_days(self):
        eid = self.make_event(datetime(2040, 6, 1, tzinfo=WIB), days=2)
        self.assign(eid, self.a, ["2040-06-09"], expected=400)
        aid = self.assign(eid, self.a, ["2040-06-01"])["assignment_id"]
        status, _, _ = self.coordinator.request(f"/api/events/{eid}/assignment", "POST", {"action": "days", "assignment_id": aid, "days": ["2040-07-01"]})
        self.assertEqual(status, 400)

    def test_availability_only_conflicts_on_shared_days(self):
        first = self.make_event(datetime(2040, 6, 1, tzinfo=WIB), days=2)       # 1-2 Jun
        second = self.make_event(datetime(2040, 6, 2, tzinfo=WIB), days=2)      # 2-3 Jun
        self.assign(first, self.a, ["2040-06-01"])                               # A works day 1 only
        status, _, raw = self.coordinator.request(f"/api/events/{second}/staff-availability?user_id={self.a}&days=2040-06-02,2040-06-03")
        self.assertEqual(json.loads(raw)["conflicts"], [])                       # day 2 is free for A
        self.assign(first, self.b, ["2040-06-01", "2040-06-02"], role="pic")
        status, _, raw = self.coordinator.request(f"/api/events/{second}/staff-availability?user_id={self.b}&days=2040-06-02,2040-06-03")
        conflicts = json.loads(raw)["conflicts"]
        self.assertEqual(len(conflicts), 1); self.assertEqual(conflicts[0]["shared_days"], ["2040-06-02"])

    def test_per_day_attendance_and_payroll_multiplies_by_days_worked(self):
        today = datetime.now(WIB).replace(hour=0, minute=0, second=0, microsecond=0)
        eid = self.make_event(today - timedelta(days=1), days=3)               # yesterday..tomorrow
        yesterday, tomorrow = (today - timedelta(days=1)).date().isoformat(), (today + timedelta(days=1)).date().isoformat()
        t = today.date().isoformat()
        aid = self.assign(eid, self.a, [t, tomorrow])["assignment_id"]
        crew = Client(self.base); crew.login("crew@captureit.local")
        url = f"/api/events/{eid}/attendance"
        loc = {"latitude": -6.2, "longitude": 106.8, "accuracy_m": 10, "captured_at": ops.now_iso()}
        # Not mapped to yesterday.
        self.assertEqual(crew.request(url, "POST", {"assignment_id": aid, "action": "check_in", "photo": PHOTO, "location": loc, "work_date": yesterday})[0], 400)
        self.assertEqual(crew.request(url, "POST", {"assignment_id": aid, "action": "check_in", "photo": PHOTO, "location": loc})[0], 200)
        mine = next(x for x in self.detail(eid, crew)["assignments"] if x["assignment_id"] == aid)
        self.assertEqual(mine["attendance_status"], "checked_in")
        self.assertEqual(crew.request(url, "POST", {"assignment_id": aid, "action": "check_out", "photo": PHOTO, "location": loc})[0], 200)
        mine = next(x for x in self.detail(eid, crew)["assignments"] if x["assignment_id"] == aid)
        # Tomorrow is still pending, so the assignment is not payable yet.
        self.assertEqual(mine["attendance_status"], "checked_in")
        self.assertEqual([d["status"] for d in mine["days"]], ["checked_out", "not_started"])
        self.assertEqual(crew.request(f"/api/attendance/{aid}/photo/check_in?date={t}")[0], 200)
        with ops.get_db() as conn:   # tomorrow is marked absent by the coordinator
            conn.execute("INSERT INTO attendance_days(assignment_id,work_date,status) VALUES(?,?,'absent')", (aid, tomorrow))
            eventdays.refresh_rollup(conn, dict(conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()), aid)
            conn.execute("UPDATE user_fee_rates SET base_fee_rupiah=100000 WHERE user_id=?", (self.a,))
            conn.execute("UPDATE event_assignments SET base_fee_snapshot_rupiah=100000 WHERE id=?", (aid,))
            rows = [r for r in ops.payroll_rows(conn, yesterday, tomorrow) if r["assignment_id"] == aid]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["work_days"], 1)
        self.assertEqual(rows[0]["base_fee"], 100000); self.assertEqual(rows[0]["meal_fee"], 35000)

    def test_cannot_release_a_day_that_already_has_attendance(self):
        today = datetime.now(WIB).replace(hour=0, minute=0, second=0, microsecond=0)
        eid = self.make_event(today, days=2)
        t, tomorrow = today.date().isoformat(), (today + timedelta(days=1)).date().isoformat()
        aid = self.assign(eid, self.a, [t, tomorrow])["assignment_id"]
        crew = Client(self.base); crew.login("crew@captureit.local")
        loc = {"latitude": -6.2, "longitude": 106.8, "accuracy_m": 10, "captured_at": ops.now_iso()}
        self.assertEqual(crew.request(f"/api/events/{eid}/attendance", "POST", {"assignment_id": aid, "action": "check_in", "photo": PHOTO, "location": loc})[0], 200)
        self.assertEqual(self.coordinator.request(f"/api/events/{eid}/assignment", "POST", {"action": "days", "assignment_id": aid, "days": [tomorrow]})[0], 400)

    def test_calendar_date_change_prunes_removed_days(self):
        eid = self.make_event(datetime(2040, 6, 1, tzinfo=WIB), days=3)
        aid = self.assign(eid, self.a, ["2040-06-01", "2040-06-03"])["assignment_id"]
        with ops.get_db() as conn:
            conn.execute("UPDATE events SET ends_at='2040-06-02T23:59:00+07:00' WHERE id=?", (eid,))
            ops.reconcile_assignment_days(conn, eid)
            days = [r[0] for r in conn.execute("SELECT work_date FROM event_assignment_days WHERE assignment_id=? ORDER BY 1", (aid,))]
        self.assertEqual(days, ["2040-06-01"])

    # ---- attendance correction by Event Coordinator / Head Operations ----
    def correction_setup(self):
        today = datetime.now(WIB).replace(hour=0, minute=0, second=0, microsecond=0)
        eid = self.make_event(today - timedelta(days=2), days=4)               # two days ago .. tomorrow
        days = [(today + timedelta(days=d)).date().isoformat() for d in (-2, -1, 0, 1)]
        aid = self.assign(eid, self.a, days)["assignment_id"]
        return eid, aid, days

    def correct(self, eid, aid, work_date, client=None, expected=200, **extra):
        body = {"action": "correct", "assignment_id": aid, "work_date": work_date, "result": "present",
                "check_in_at": f"{work_date}T09:00:00+07:00", "check_out_at": f"{work_date}T17:00:00+07:00",
                "note": "Lupa absen, hadir sesuai laporan PIC", **extra}
        status, _, raw = (client or self.coordinator).request(f"/api/events/{eid}/attendance", "POST", body)
        self.assertEqual(status, expected, raw.decode())
        return raw

    def test_missed_past_day_is_flagged_and_can_be_corrected(self):
        eid, aid, days = self.correction_setup()
        mine = next(x for x in self.detail(eid)["assignments"] if x["assignment_id"] == aid)
        self.assertEqual([d["needs_action"] for d in mine["days"]], [True, True, False, False])
        self.assertTrue(mine["needs_action"]); self.assertEqual(self.detail(eid)["attendance_needs_action"], 1)
        self.correct(eid, aid, days[0])
        self.coordinator.request(f"/api/events/{eid}/attendance", "POST", {"action": "absent", "assignment_id": aid, "work_date": days[1], "note": "Tidak datang"})
        mine = next(x for x in self.detail(eid)["assignments"] if x["assignment_id"] == aid)
        self.assertEqual([d["status"] for d in mine["days"]], ["checked_out", "absent", "not_started", "not_started"])
        self.assertTrue(mine["days"][0]["corrected"]); self.assertIn("Lupa absen", mine["days"][0]["note"])
        self.assertFalse(mine["needs_action"])
        with ops.get_db() as conn:
            entry = conn.execute("SELECT details FROM audit_logs WHERE action='corrected' AND entity_id=?", (aid,)).fetchone()
        self.assertIn("previous", json.loads(entry[0]))

    def test_correction_rules(self):
        eid, aid, days = self.correction_setup()
        self.correct(eid, aid, days[0], note="x", expected=400)                                  # reason required
        self.correct(eid, aid, days[3], expected=400)                                            # future day
        self.correct(eid, aid, days[0], check_out_at=f"{days[0]}T08:00:00+07:00", expected=400)  # out before in
        self.correct(eid, aid, days[0], check_in_at=f"{days[1]}T09:00:00+07:00", expected=400)   # in on another date
        self.correct(eid, aid, "2001-01-01", expected=400)                                       # not a work day
        pic = Client(self.base); pic.login("pic@captureit.local")
        crew = Client(self.base); crew.login("crew@captureit.local")
        self.correct(eid, aid, days[0], client=pic, expected=403)                                # PIC may not correct
        self.correct(eid, aid, days[0], client=crew, expected=403)                               # crew may not
        self.correct(eid, aid, days[0])                                                          # coordinator may
        self.correct(eid, aid, days[0], expected=400)                                            # complete record is locked

    def test_nobody_corrects_their_own_attendance(self):
        today = datetime.now(WIB).replace(hour=0, minute=0, second=0, microsecond=0)
        eid = self.make_event(today - timedelta(days=2), days=3)
        days = [(today + timedelta(days=d)).date().isoformat() for d in (-2, -1, 0)]
        with ops.get_db() as conn:   # the API refuses to staff a coordinator, so seed the row directly
            aid = conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type) VALUES(?,?,'crew')", (eid, self.owner)).lastrowid
            conn.execute("INSERT INTO attendance(assignment_id) VALUES(?)", (aid,))
            conn.executemany("INSERT INTO event_assignment_days(assignment_id,work_date) VALUES(?,?)", [(aid, d) for d in days])
        self.correct(eid, aid, days[0], expected=403)

    def test_head_operations_can_correct_and_clear_is_blocked_until_resolved(self):
        eid, aid, days = self.correction_setup()
        head = Client(self.base); head.login("head.ops@captureit.local")
        with ops.get_db() as conn:
            self.assertEqual(ops.event_unresolved_attendance(conn, eid), [conn.execute("SELECT full_name FROM users WHERE id=?", (self.a,)).fetchone()[0]])
        for d in days[:2]:
            self.correct(eid, aid, d, client=head)
        with ops.get_db() as conn:
            conn.executemany("INSERT OR REPLACE INTO attendance_days(assignment_id,work_date,status) VALUES(?,?,'absent')", [(aid, d) for d in days[2:]])
            self.assertEqual(ops.event_unresolved_attendance(conn, eid), [])

    def test_correction_blocked_after_payroll_claim(self):
        eid, aid, days = self.correction_setup()
        with ops.get_db() as conn:
            batch = conn.execute("INSERT INTO payroll_batches(period,status,created_by) VALUES('test','exported',?)", (self.owner,)).lastrowid
            conn.execute("INSERT INTO payroll_assignment_claims(assignment_id,batch_id) VALUES(?,?)", (aid, batch))
        self.correct(eid, aid, days[0], expected=400)



class FeeVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        ops.DB_PATH = Path(cls.temp.name) / "fees.sqlite3"
        ops.initialize(seed=True)
        cls.httpd = ops.ThreadingHTTPServer(("127.0.0.1", 0), ops.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(); cls.temp.cleanup()

    def test_admin_finance_and_sales_cannot_see_fee_lists(self):
        for role in ("admin_finance", "sales_staff"):
            self.assertFalse(ops.ROLE_PERMISSIONS[role] & ops.FEE_RESTRICTED_PERMISSIONS, role)
        client = Client(self.base); client.login("admin.finance@captureit.local")
        for path in ("/api/payroll", "/api/inhouse-payroll", "/api/compensation-history?kind=fee&user_id=1",
                     "/api/compensation-history?kind=salary&user_id=1"):
            self.assertEqual(client.request(path)[0], 403, path)
        status, _, raw = client.request("/api/bootstrap")
        data = json.loads(raw)
        self.assertEqual(data.get("rates", []), [])
        self.assertNotIn("payroll.view", data["user"]["permissions"])

    def test_head_finance_and_finance_still_see_fees(self):
        for email in ("head.finance@captureit.local", "finance@captureit.local"):
            client = Client(self.base); client.login(email)
            self.assertEqual(client.request("/api/payroll")[0], 200, email)

    def test_stored_acl_cannot_grant_fee_access_back(self):
        with ops.get_db() as conn:
            role = conn.execute("SELECT id FROM roles WHERE code='admin_finance'").fetchone()[0]
            pid = conn.execute("SELECT id FROM permissions WHERE code='payroll.view'").fetchone()[0]
            conn.execute("UPDATE role_acl_settings SET customized=1 WHERE role_id=?", (role,))
            conn.execute("INSERT OR IGNORE INTO role_permissions(role_id,permission_id) VALUES(?,?)", (role, pid))
        try:
            client = Client(self.base); client.login("admin.finance@captureit.local")
            self.assertEqual(client.request("/api/payroll")[0], 403)
        finally:
            with ops.get_db() as conn:
                conn.execute("UPDATE role_acl_settings SET customized=0 WHERE role_id=?", (role,))
                conn.execute("DELETE FROM role_permissions WHERE role_id=? AND permission_id=?", (role, pid))


if __name__ == "__main__":
    unittest.main()
