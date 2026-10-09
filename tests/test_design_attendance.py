import base64
import csv
import io
import json
import re
import unittest
import unittest.mock
import zipfile
from datetime import datetime, timedelta, timezone

import eventdays
from test_security_backup import start_server
from test_workflows import Client, ops
from staffing import WIB

PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0cam\xff\xd9").decode()


def fixed_clock(moment):
    """Make the office-hours clock read `moment` (the login session keeps using the real clock)."""
    return unittest.mock.patch.object(ops, "wib_now", lambda: moment.astimezone(timezone(timedelta(hours=7))))


class Base(unittest.TestCase):
    counter = 0

    @classmethod
    def setUpClass(cls):
        cls.temp, cls.httpd, cls.thread, cls.base = start_server(cls.__name__.lower() + ".sqlite3")
        cls.admin = Client(cls.base); cls.admin.login("admin@captureit.local")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(); cls.temp.cleanup()

    def person(self, role, name):
        Base.counter += 1
        email = f"t{Base.counter}-{role}@captureit.local"
        status, _, raw = self.admin.request("/api/users", "POST", {"full_name": name, "email": email, "role": role, "password": "Tes!Pass2026x"})
        self.assertEqual(status, 200, raw.decode())
        client = Client(self.base); client.login(email, "Tes!Pass2026x")
        return json.loads(raw)["id"], client

    def login(self, email):
        client = Client(self.base); client.login(email); return client

    def json(self, client, path, method="GET", body=None, expected=200):
        status, _, raw = client.request(path, method, body)
        self.assertEqual(status, expected, raw.decode())
        return json.loads(raw)


class DesignAccessTests(Base):
    def setUp(self):
        self.head_id, self.head = self.person("design_head", "Head Design")
        self.s1_id, self.s1 = self.person("design_team", "Staff Satu")
        self.s2_id, self.s2 = self.person("design_team", "Staff Dua")
        self.coordinator = self.login("coordinator@captureit.local")
        self.head_ops = self.login("head.ops@captureit.local")
        self.events = {}
        for label in "ABC":
            made = self.json(self.coordinator, "/api/events/manual", "POST", {"title": f"Desain {label}", "starts_at": "2041-03-01T09:00", "ends_at": "2041-03-01T18:00"})
            self.events[label] = made["event_id"]

    def task_id(self, label):
        with ops.get_db() as conn:
            return conn.execute("SELECT id FROM design_tasks WHERE event_id=?", (self.events[label],)).fetchone()[0]

    def brief(self, client, label, assignee, expected=200):
        with ops.get_db() as conn:
            version = conn.execute("SELECT brief_version FROM design_tasks WHERE event_id=?", (self.events[label],)).fetchone()[0]
        return self.json(client, f"/api/design/{self.task_id(label)}/brief", "POST",
                         {"brief_text": f"Brief {label} {version}", "assignee_id": assignee, "version": version}, expected)

    def mine(self, client):
        data = self.json(client, "/api/bootstrap")
        ids = {t["event_id"] for t in data["design_tasks"]}
        return {label for label, eid in self.events.items() if eid in ids}, data

    def test_head_admin_headops_coordinator_can_pick_the_designer_and_staff_cannot(self):
        self.brief(self.head, "A", self.s1_id)
        self.brief(self.coordinator, "B", self.s2_id)
        self.brief(self.head_ops, "C", self.s1_id)
        self.brief(self.admin, "C", self.s2_id)
        self.brief(self.s1, "A", self.s2_id, expected=403)          # Team Design cannot reassign
        self.assertEqual(self.json(self.head, "/api/bootstrap")["designers"] != [], True)
        self.assertEqual(self.json(self.s1, "/api/bootstrap")["designers"], [])

    def test_staff_see_only_their_cards_head_and_managers_see_all(self):
        self.brief(self.head, "A", self.s1_id); self.brief(self.head, "B", self.s2_id)      # C stays unassigned
        self.assertEqual(self.mine(self.s1)[0], {"A"})
        self.assertEqual(self.mine(self.s2)[0], {"B"})
        for client in (self.head, self.admin, self.coordinator, self.head_ops):
            self.assertEqual(self.mine(client)[0], {"A", "B", "C"})
        self.brief(self.head, "A", self.s2_id)                                               # reassign A to staff 2
        self.assertEqual(self.mine(self.s1)[0], set()); self.assertEqual(self.mine(self.s2)[0], {"A", "B"})

    def test_dashboard_counts_only_own_cards_for_staff(self):
        self.brief(self.head, "A", self.s1_id)
        self.assertEqual(self.json(self.s1, "/api/bootstrap")["dashboard"]["design_active"], 1)
        self.assertEqual(self.json(self.s2, "/api/bootstrap")["dashboard"]["design_active"], 0)
        self.assertGreaterEqual(self.json(self.head, "/api/bootstrap")["dashboard"]["design_active"], 3)

    def test_staff_cannot_open_or_change_someone_elses_card(self):
        self.brief(self.head, "A", self.s1_id); self.brief(self.head, "B", self.s2_id)
        other = self.json(self.s1, f"/api/events/{self.events['B']}")
        self.assertIsNone(other["design_task"]); self.assertTrue(other["design_restricted"]); self.assertEqual(other["design_notes"], [])
        own = self.json(self.s1, f"/api/events/{self.events['A']}")
        self.assertEqual(own["design_task"]["assignee_id"], self.s1_id)
        self.json(self.s1, f"/api/design/{self.task_id('B')}/status", "POST", {"status": "in_progress"}, expected=403)
        self.json(self.s1, f"/api/design/{self.task_id('A')}/status", "POST", {"status": "in_progress"})
        self.json(self.head, f"/api/design/{self.task_id('B')}/status", "POST", {"status": "in_progress"})   # head can

    def test_event_list_exposes_assignee_so_the_ui_hides_other_designers_badges(self):
        self.brief(self.head, "A", self.s1_id)
        rows = {e["id"]: e for e in self.json(self.s1, "/api/bootstrap")["events"]}
        self.assertEqual(rows[self.events["A"]]["design_assignee_id"], self.s1_id)
        self.assertIsNone(rows[self.events["B"]]["design_assignee_id"])

    def test_brief_notifications_go_to_the_chosen_designer_only(self):
        def titles(client):
            return [i["title"] for i in self.json(client, "/api/notifications")["items"]]
        self.brief(self.head, "A", self.s1_id)
        self.assertIn("Brief desain baru", titles(self.s1)); self.assertNotIn("Brief desain baru", titles(self.s2))
        self.brief(self.coordinator, "C", None)                       # not assigned: only Head Design hears about it
        self.assertEqual(titles(self.s2).count("Brief desain baru"), 0)
        self.assertEqual(titles(self.s1).count("Brief desain baru"), 1)
        self.assertEqual(titles(self.head).count("Brief desain baru"), 1)       # only the unassigned one reaches the Head Design

    def test_reminders_for_staff_cover_only_their_cards(self):
        self.brief(self.head, "A", self.s1_id)
        with ops.get_db() as conn:
            s2 = ops.notification_user(conn, self.s2_id); s1 = ops.notification_user(conn, self.s1_id)
        self.assertEqual(s2["permissions"] & {"design.read_all"}, set())
        data1 = self.json(self.s1, "/api/bootstrap"); data2 = self.json(self.s2, "/api/bootstrap")
        pick = lambda data: [t for t in data.get("tasks", data.get("focus", [])) if isinstance(t, dict)]
        self.assertIsNotNone(data1); self.assertIsNotNone(data2)

    def test_permissions_per_role(self):
        for role, read_all, assign in (("design_head", True, True), ("design_team", False, False), ("head_operations", True, True),
                                       ("event_coordinator", True, True), ("administrator", True, True)):
            perms = ops.ROLE_PERMISSIONS[role]
            self.assertEqual("design.read_all" in perms, read_all, role); self.assertEqual("design.assign" in perms, assign, role)
        for code, perms in ops.ROLE_PERMISSIONS.items():                              # nobody else loses the board
            if code != "design_team" and "design.read" in perms:
                self.assertIn("design.read_all", perms, code)


class EventCrewDateTests(Base):
    def setUp(self):
        self.coordinator = self.login("coordinator@captureit.local")
        self.crew_id, self.crew = self.person("crew", "Crew Tanggal")
        self.loc = lambda: {"latitude": -6.2, "longitude": 106.8, "accuracy_m": 10, "captured_at": ops.now_iso()}

    def event_on(self, day, hours=(9, 18)):
        made = self.json(self.coordinator, "/api/events/manual", "POST",
                         {"title": f"Hari {day}", "starts_at": f"{day}T{hours[0]:02d}:00", "ends_at": f"{day}T{hours[1]:02d}:00"})
        assigned = self.json(self.coordinator, f"/api/events/{made['event_id']}/assignment", "POST",
                             {"user_id": self.crew_id, "assignment_type": "crew", "skill_ids": []})
        return made["event_id"], assigned["assignment_id"]

    def act(self, eid, aid, action, expected=200, **extra):
        status, _, raw = self.crew.request(f"/api/events/{eid}/attendance", "POST",
            {"assignment_id": aid, "action": action, "photo": PHOTO, "location": self.loc(), **extra})
        self.assertEqual(status, expected, raw.decode())
        return json.loads(raw)

    def test_crew_cannot_check_in_before_or_after_the_event_date(self):
        today = datetime.now(WIB).date()
        future, past = (today + timedelta(days=3)).isoformat(), (today - timedelta(days=3)).isoformat()
        for day in (future, past):
            eid, aid = self.event_on(day)
            result = self.act(eid, aid, "check_in", expected=400)
            self.assertIn("tanggal event", result["error"]); self.assertIn(day, result["error"])

    def test_crew_can_check_in_and_out_on_the_event_date(self):
        eid, aid = self.event_on(datetime.now(WIB).date().isoformat())
        self.act(eid, aid, "check_in"); self.act(eid, aid, "check_out")

    def test_checkout_allowed_the_day_after_but_not_later(self):
        today = datetime.now(WIB).date()
        eid, aid = self.event_on(today.isoformat())
        self.act(eid, aid, "check_in")
        with unittest.mock.patch.object(eventdays, "today_wib", lambda: (today + timedelta(days=2)).isoformat()):
            self.assertIn("Coordinator", self.act(eid, aid, "check_out", expected=400)["error"])
        with unittest.mock.patch.object(eventdays, "today_wib", lambda: (today + timedelta(days=1)).isoformat()):
            self.act(eid, aid, "check_out")                         # a shift that ran past midnight

    def test_multiday_overnight_checkout_closes_yesterdays_open_day(self):
        today = datetime.now(WIB).replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = (today - timedelta(days=1)).date().isoformat(); t = today.date().isoformat()
        made = self.json(self.coordinator, "/api/events/manual", "POST",
                         {"title": "Malam", "starts_at": f"{yesterday}T00:00", "ends_at": f"{t}T23:59"})
        eid = made["event_id"]
        aid = self.json(self.coordinator, f"/api/events/{eid}/assignment", "POST",
                        {"user_id": self.crew_id, "assignment_type": "crew", "skill_ids": [], "days": [yesterday, t]})["assignment_id"]
        with ops.get_db() as conn:
            conn.execute("INSERT INTO attendance_days(assignment_id,work_date,status,check_in_at) VALUES(?,?,'checked_in',?)", (aid, yesterday, ops.now_iso()))
        self.act(eid, aid, "check_out")                              # no work_date: closes yesterday, not today
        days = {d["work_date"]: d["status"] for d in next(a for a in self.json(self.crew, f"/api/events/{eid}")["assignments"] if a["assignment_id"] == aid)["days"]}
        self.assertEqual(days[yesterday], "checked_out"); self.assertEqual(days[t], "not_started")
        self.act(eid, aid, "check_in", work_date=yesterday, expected=400)   # cannot start a past day
        self.act(eid, aid, "check_in")


class InhousePunctualityTests(Base):
    def setUp(self):
        with ops.get_db() as conn:
            conn.execute("DELETE FROM app_settings WHERE setting_key LIKE 'inhouse_%'")
        self.finance_admin = self.login("admin.finance@captureit.local")     # holds attendance.inhouse.read_all
        self.loc = lambda: {"latitude": -6.2, "longitude": 106.8, "accuracy_m": 10, "captured_at": ops.now_iso()}

    def at(self, hour, minute, day="2041-05-12"):
        return fixed_clock(datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:30+07:00"))

    def punch(self, client, action, hour, minute, day="2041-05-12", expected=200):
        with self.at(hour, minute, day):
            status, _, raw = client.request("/api/inhouse-attendance", "POST", {"action": action, "photo": PHOTO, "location": self.loc()})
        self.assertEqual(status, expected, raw.decode())
        return json.loads(raw)

    def notices(self, client, kind_word):
        return [i for i in self.json(client, "/api/notifications")["items"] if kind_word in i["title"]]

    def test_late_checkin_is_flagged_recorded_and_overseers_are_told(self):
        uid, emp = self.person("design_team", "Dimas Telat")
        result = self.punch(emp, "check_in", 10, 0)
        self.assertEqual(result["late_minutes"], 60)
        self.assertIn("Terlambat", result["punctuality"]); self.assertEqual(result["scheduled"], "09:00")
        mine = self.json(emp, "/api/inhouse-attendance?date=2041-05-12")
        self.assertEqual(mine["schedule"]["work_start"], "09:00")
        told = self.notices(self.finance_admin, "Dimas Telat")
        self.assertEqual(len(told), 1); self.assertIn("jadwal masuk 09:00", told[0]["body"]); self.assertIn("terlambat", told[0]["body"])
        self.assertEqual(self.notices(emp, "Dimas Telat"), [])                                 # not the employee
        crew = self.person("crew", "Crew Biasa")[1]
        self.assertEqual(self.notices(crew, "Dimas Telat"), [])                                # nor people without access

    def test_on_time_checkin_is_silent(self):
        uid, emp = self.person("design_team", "Rapi Tepat")
        result = self.punch(emp, "check_in", 8, 55)
        self.assertEqual(result["late_minutes"], 0); self.assertEqual(result["punctuality"], "")
        self.assertEqual(self.notices(self.finance_admin, "Rapi Tepat"), [])

    def test_early_checkout_is_flagged_and_on_time_checkout_is_not(self):
        uid, early = self.person("design_team", "Pulang Awal")
        self.punch(early, "check_in", 8, 50)
        result = self.punch(early, "check_out", 17, 30)
        self.assertEqual(result["early_leave_minutes"], 29); self.assertIn("Pulang lebih awal 29 menit", result["punctuality"])
        told = self.notices(self.finance_admin, "Pulang Awal")
        self.assertEqual(len(told), 1); self.assertIn("jadwal pulang 18:00", told[0]["body"])
        uid2, ok = self.person("design_team", "Pulang Pas")
        self.punch(ok, "check_in", 8, 50)
        self.assertEqual(self.punch(ok, "check_out", 18, 10)["punctuality"], "")
        self.assertEqual(self.notices(self.finance_admin, "Pulang Pas"), [])

    def test_grace_minutes_are_respected(self):
        self.json(self.admin, "/api/settings/inhouse-schedule", "POST",
                  {"work_start": "09:00", "work_end": "18:00", "late_grace_minutes": 15, "early_grace_minutes": 10})
        uid, emp = self.person("design_team", "Dalam Toleransi")
        self.assertEqual(self.punch(emp, "check_in", 9, 10)["punctuality"], "")
        self.assertEqual(self.punch(emp, "check_out", 17, 55)["punctuality"], "")
        self.assertEqual(self.notices(self.finance_admin, "Dalam Toleransi"), [])
        uid2, over = self.person("design_team", "Lewat Toleransi")
        self.assertIn("Terlambat", self.punch(over, "check_in", 9, 20)["punctuality"])

    def test_schedule_settings_validation_permission_and_snapshot(self):
        body = {"work_start": "08:00", "work_end": "17:00", "late_grace_minutes": 0, "early_grace_minutes": 0}
        self.json(self.finance_admin, "/api/settings/inhouse-schedule", "POST", body, expected=403)
        for bad in ({"work_start": "8am"}, {"work_end": "08:00"}, {"late_grace_minutes": -1}, {"early_grace_minutes": 500}, {"late_grace_minutes": "abc"}):
            self.json(self.admin, "/api/settings/inhouse-schedule", "POST", {**body, **bad}, expected=400)
        uid, emp = self.person("design_team", "Snapshot Satu")
        self.punch(emp, "check_in", 9, 30)                                            # recorded against 09:00
        self.json(self.admin, "/api/settings/inhouse-schedule", "POST", body)
        self.assertEqual(self.json(self.admin, "/api/bootstrap")["inhouse_schedule"]["work_start"], "08:00")
        with ops.get_db() as conn:
            row = conn.execute("SELECT scheduled_start,late_minutes FROM inhouse_attendance WHERE user_id=?", (uid,)).fetchone()
        self.assertEqual((row[0], row[1]), ("09:00", 30))                              # history keeps what applied then

    def test_export_contains_schedule_and_punctuality_details_in_csv_and_excel(self):
        uid, emp = self.person("design_team", "Export Detail")
        self.punch(emp, "check_in", 10, 5, day="2041-06-03"); self.punch(emp, "check_out", 17, 0, day="2041-06-03")
        for fmt in ("csv", "xlsx"):
            status, headers, raw = self.finance_admin.request("/api/inhouse-attendance/export", "POST", {"start": "2041-06-03", "end": "2041-06-03", "format": fmt})
            self.assertEqual(status, 200)
            if fmt == "csv":
                rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
                header = rows[0]; line = next(r for r in rows[1:] if r[1] == "Export Detail")
                for name in ("Jadwal masuk", "Terlambat (menit)", "Jadwal pulang", "Pulang lebih awal (menit)", "Keterangan"):
                    self.assertIn(name, header)
                get = lambda name: line[header.index(name)]
                self.assertEqual(get("Jadwal masuk"), "09:00"); self.assertEqual(get("Jadwal pulang"), "18:00")
                self.assertEqual(get("Terlambat (menit)"), "65"); self.assertEqual(get("Pulang lebih awal (menit)"), "59")   # whole minutes, rounded down (17:00:30 is 59.5 min early)
                self.assertEqual(get("Keterangan"), "Terlambat 65 menit · Pulang lebih awal 59 menit")
            else:
                sheet = zipfile.ZipFile(io.BytesIO(raw)).read("xl/worksheets/sheet1.xml").decode()
                self.assertIn("Terlambat 65 menit · Pulang lebih awal 59 menit", sheet); self.assertIn('ref="A1:T2"', sheet)
                self.assertIn("Pulang lebih awal (menit)", sheet)

    def test_old_records_without_schedule_data_export_without_inventing_numbers(self):
        uid, emp = self.person("design_team", "Data Lama")
        with ops.get_db() as conn:
            conn.execute("INSERT INTO inhouse_attendance(user_id,work_date,status,check_in_at,check_out_at) VALUES(?,?,'checked_out',?,?)",
                         (uid, "2041-07-01", "2041-07-01T02:00:00+00:00", "2041-07-01T10:00:00+00:00"))
        status, _, raw = self.finance_admin.request("/api/inhouse-attendance/export", "POST", {"start": "2041-07-01", "end": "2041-07-01", "format": "csv"})
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")))); line = next(r for r in rows[1:] if r[1] == "Data Lama")
        header = rows[0]
        self.assertEqual(line[header.index("Terlambat (menit)")], ""); self.assertEqual(line[header.index("Keterangan")], "")


if __name__ == "__main__":
    unittest.main()
