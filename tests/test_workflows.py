import csv
import base64
import http.cookiejar
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as ops


class Client:
    def __init__(self, base_url):
        self.base_url = base_url
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, path, method="GET", body=None, download=False):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        csrf = next((cookie.value for cookie in self.jar if cookie.name == "ops_csrf"), "")
        if method != "GET" and csrf:
            headers["X-CSRF-Token"] = csrf
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def login(self, email, password="demo1234"):
        status, _, body = self.request("/api/login", "POST", {"email": email, "password": password})
        if status != 200:
            raise AssertionError(body.decode())


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        ops.DB_PATH = Path(cls.temp_dir.name) / "test.sqlite3"
        ops.DEMO_MODE = True
        ops.initialize(seed=True)
        cls.httpd = ops.ThreadingHTTPServer(("127.0.0.1", 0), ops.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        cls.temp_dir.cleanup()

    def client(self, email, password="demo1234"):
        client = Client(self.base_url)
        client.login(email,password)
        return client

    def event_id(self, code):
        with ops.get_db() as conn:
            return conn.execute("SELECT id FROM events WHERE project_code=?", (code,)).fetchone()["id"]

    def event(self, client, event_id):
        status, _, body = client.request(f"/api/events/{event_id}")
        self.assertEqual(status, 200, body.decode())
        return json.loads(body)

    def mutate(self, client, path, payload):
        status, _, body = client.request(path, "POST", payload)
        self.assertEqual(status, 200, body.decode())
        return json.loads(body)

    def test_attendance_location_requires_fresh_valid_gps_fix(self):
        fresh = {"latitude": -6.2, "longitude": 106.8, "accuracy_m": 25, "captured_at": ops.now_iso()}
        self.assertEqual(ops.parse_attendance_location(fresh)[:3], (-6.2, 106.8, 25.0))
        with self.assertRaises(ValueError):
            ops.parse_attendance_location({**fresh, "accuracy_m": 501})
        with self.assertRaises(ValueError):
            ops.parse_attendance_location({**fresh, "captured_at": "2020-01-01T00:00:00+00:00"})
        with self.assertRaises(ValueError):
            ops.parse_attendance_location({**fresh, "latitude": 91})

    def test_role_scoped_schedule_and_acl(self):
        coordinator = self.client("coordinator@captureit.local")
        status, _, body = coordinator.request("/api/bootstrap")
        self.assertEqual(status, 200)
        coordinator_data = json.loads(body)
        self.assertGreaterEqual(len(coordinator_data["events"]), 5)
        self.assertIsNotNone(coordinator_data["google_sync"])
        coordinator_permissions = coordinator_data["user"]["permissions"]
        self.assertIn("events.clear", coordinator_permissions)
        self.assertIn("kpi.evaluate_crew", coordinator_permissions)

        crew = self.client("naya@captureit.local")
        status, _, body = crew.request("/api/bootstrap")
        self.assertEqual(status, 200)
        crew_data = json.loads(body)
        visible_ids = {row["id"] for row in crew_data["events"]}
        self.assertIsNone(crew_data["google_sync"])
        self.assertIn(self.event_id("CI-OPS-26001"), visible_ids)
        self.assertNotIn(self.event_id("CI-OPS-26002"), visible_ids)
        self.assertEqual(crew.request(f"/api/events/{self.event_id('CI-OPS-26002')}")[0], 403)
        self.assertEqual(crew.request("/api/payroll?month=2026-09")[0], 403)

    def test_coordinator_can_schedule_calendar_event_without_crm_code_and_export_owned_schedule(self):
        coordinator=self.client("coordinator@captureit.local")
        admin=self.client("admin@captureit.local")
        with ops.get_db() as conn:
            conn.execute("INSERT INTO calendar_code_queue(google_event_id,title,event_type,starts_at,ends_at,location,is_full_day,status) VALUES(?,?,?,?,?,?,0,'scheduled')",
                ("google-no-code-ops-test","Last minute event","Corporate","2026-10-15T10:00:00+07:00","2026-10-15T18:00:00+07:00","Jakarta"))
            intake=conn.execute("SELECT id FROM calendar_code_queue WHERE google_event_id='google-no-code-ops-test'").fetchone()["id"]
        queued=json.loads(coordinator.request("/api/bootstrap")[2])["calendar_code_queue"]
        self.assertTrue(any(row["id"]==intake for row in queued))
        status,_,body=coordinator.request(f"/api/calendar-queue/{intake}/promote","POST",{"is_full_day":True})
        self.assertEqual(status,200,body.decode())
        promoted=json.loads(body)
        with ops.get_db() as conn:
            event=conn.execute("SELECT * FROM events WHERE id=?",(promoted["event_id"],)).fetchone()
            owner=conn.execute("SELECT email FROM users WHERE id=?",(event["coordinator_id"],)).fetchone()["email"]
        self.assertTrue(event["project_code_is_temporary"])
        self.assertEqual(event["project_code"],event["operational_code"])
        self.assertEqual(owner,"coordinator@captureit.local")
        self.assertEqual(event["is_full_day"],1)
        self.assertEqual(event["is_full_day_manual"],1)
        status,_,body=coordinator.request(f"/api/events/{promoted['event_id']}/full-day","POST",{"is_full_day":False})
        self.assertEqual(status,200,body.decode())
        with ops.get_db() as conn:
            event=conn.execute("SELECT is_full_day,is_full_day_manual FROM events WHERE id=?",(promoted["event_id"],)).fetchone()
        self.assertEqual(event["is_full_day"],0)
        self.assertEqual(event["is_full_day_manual"],1)
        status,_,body=coordinator.request(f"/api/events/{promoted['event_id']}/temporary-code","POST",{"project_code":"OPS-MENTARI-FEST-26"})
        self.assertEqual(status,200,body.decode())
        with ops.get_db() as conn:
            event=conn.execute("SELECT project_code,operational_code,project_code_is_temporary FROM events WHERE id=?",(promoted["event_id"],)).fetchone()
        self.assertEqual(event["project_code"],"OPS-MENTARI-FEST-26")
        self.assertEqual(event["operational_code"],"OPS-MENTARI-FEST-26")
        self.assertEqual(event["project_code_is_temporary"],1)
        class MockResponse:
            def __init__(self,payload): self.payload=json.dumps(payload).encode()
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return self.payload
        with ops.get_db() as conn:
            google_id=conn.execute("SELECT google_event_id FROM events WHERE id=?",(promoted["event_id"],)).fetchone()["google_event_id"]
            with patch.dict(os.environ,{"GOOGLE_CLIENT_ID":"id","GOOGLE_CLIENT_SECRET":"secret","GOOGLE_REFRESH_TOKEN":"token","GOOGLE_CALENDAR_ID":"primary"}):
                with patch.object(ops,"urlopen",side_effect=[MockResponse({"access_token":"test"}),MockResponse({"items":[{
                    "id":google_id,"summary":"Last minute event","start":{"dateTime":"2026-10-15T09:00:00+07:00"},
                    "end":{"dateTime":"2026-10-15T18:00:00+07:00"},"updated":"2026-10-01T00:00:00Z"}]})]):
                    sync_result=ops.google_sync(conn)
        self.assertTrue(sync_result["ok"])
        with ops.get_db() as conn:
            event=conn.execute("SELECT is_full_day,is_full_day_manual FROM events WHERE id=?",(promoted["event_id"],)).fetchone()
        self.assertEqual(event["is_full_day"],0)
        self.assertEqual(event["is_full_day_manual"],1)
        with ops.get_db() as conn:
            other_id=conn.execute("SELECT id FROM users WHERE email='head.ops@captureit.local'").fetchone()["id"]
            conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at,coordinator_id) VALUES(?,?,?,?,?)",
                ("OTHER-COORD-26001","Other coordinator event","2026-10-16T10:00:00+07:00","2026-10-16T18:00:00+07:00",other_id))
        status,_,body=admin.request(f"/api/events/{promoted['event_id']}/project-code","POST",{"project_code":"CRM-LAST-MINUTE-26001"})
        self.assertEqual(status,200,body.decode())
        with ops.get_db() as conn:
            event=conn.execute("SELECT * FROM events WHERE id=?",(promoted["event_id"],)).fetchone()
        self.assertFalse(event["project_code_is_temporary"])
        self.assertEqual(event["project_code"],"CRM-LAST-MINUTE-26001")
        self.assertEqual(event["operational_code"],"OPS-MENTARI-FEST-26")
        status,headers,body=coordinator.request("/api/events/export?start=2026-10-01&end=2026-10-31&format=xlsx")
        self.assertEqual(status,200,body[:200])
        self.assertIn("captureit-jadwal-event",headers.get("Content-Disposition",""))
        with zipfile.ZipFile(io.BytesIO(body)) as workbook:
            sheet=ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
            values=[cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is/{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t").text
                    for cell in sheet.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c")
                    if cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is/{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t") is not None]
        # The supplied Events template intentionally has no CRM/code columns.
        self.assertIn("Last minute event",values)
        self.assertNotIn("Other coordinator event",values)
        self.assertEqual(self.client("crew@captureit.local").request("/api/events/export?start=2026-10-01&end=2026-10-31")[0],403)
        with ops.get_db() as conn:
            conn.execute("INSERT INTO calendar_code_queue(google_event_id,title,event_type,starts_at,ends_at,location,is_full_day,status) VALUES(?,?,?,?,?,?,0,'scheduled')",
                ("google-direct-crm-test","CRM code at schedule","Corporate","2026-10-18T10:00:00+07:00","2026-10-18T18:00:00+07:00","Jakarta"))
            direct_intake=conn.execute("SELECT id FROM calendar_code_queue WHERE google_event_id='google-direct-crm-test'").fetchone()["id"]
        status,_,body=coordinator.request(f"/api/calendar-queue/{direct_intake}/promote","POST",{"project_code":"CRM-DIRECT-26001","is_full_day":False})
        self.assertEqual(status,200,body.decode())
        direct=json.loads(body)
        self.assertFalse(direct["temporary"])
        with ops.get_db() as conn:
            event=conn.execute("SELECT project_code,operational_code,project_code_is_temporary,is_full_day FROM events WHERE id=?",(direct["event_id"],)).fetchone()
        self.assertEqual(event["project_code"],"CRM-DIRECT-26001")
        self.assertIsNone(event["operational_code"])
        self.assertEqual(event["project_code_is_temporary"],0)
        self.assertEqual(event["is_full_day"],0)

    def test_runtime_acl_survives_stale_role_permission_rows(self):
        with ops.get_db() as conn:
            role_id = conn.execute("SELECT id FROM roles WHERE code='event_coordinator'").fetchone()["id"]
            conn.execute("BEGIN")
            conn.execute("DELETE FROM role_permissions WHERE role_id=?", (role_id,))
            user_id = conn.execute("SELECT id FROM users WHERE email='coordinator@captureit.local'").fetchone()["id"]
            roles, permissions = ops.role_data(conn, user_id)
            conn.rollback()
        self.assertEqual([role["code"] for role in roles], ["event_coordinator"])
        self.assertIn("events.clear", permissions)
        self.assertIn("kpi.evaluate_crew", permissions)

    def test_user_profiles_ktp_privacy_and_staff_directory(self):
        crew = self.client("naya@captureit.local")
        coordinator = self.client("coordinator@captureit.local")
        head_ops = self.client("head.ops@captureit.local")
        with ops.get_db() as conn:
            naya_id = conn.execute("SELECT id FROM users WHERE email='naya@captureit.local'").fetchone()["id"]

        photo = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffprofile\xff\xd9").decode()
        status, _, body = crew.request("/api/profile", "POST", {
            "full_name": "Naya Putri", "phone": "+628123456789", "photo": photo,
        })
        self.assertEqual(status, 200, body.decode())
        own_data = json.loads(crew.request("/api/bootstrap")[2])
        self.assertEqual(own_data["profile"]["phone"], "+628123456789")
        self.assertEqual(own_data["staff_directory"], [])
        self.assertEqual(crew.request(own_data["profile"]["profile_photo_url"])[0], 200)

        for side in ("front", "back"):
            status, _, body = crew.request("/api/profile/ktp", "POST", {"side": side, "image": photo})
            self.assertEqual(status, 200, body.decode())
        directory = json.loads(coordinator.request("/api/bootstrap")[2])["staff_directory"]
        naya = next(row for row in directory if row["id"] == naya_id)
        self.assertTrue(naya["has_ktp_front"])
        self.assertTrue(naya["has_ktp_back"])
        self.assertEqual(naya["phone"], "+628123456789")
        self.assertEqual(coordinator.request(f"/api/profile-file/{naya_id}/ktp_front")[0], 403)
        self.assertEqual(head_ops.request(f"/api/profile-file/{naya_id}/ktp_front")[0], 200)
        self.assertEqual(crew.request(f"/api/profile-file/{naya_id}/ktp_back")[0], 200)

    def test_event_role_can_be_selected_independently_from_account_acl_role(self):
        coordinator = self.client("coordinator@captureit.local")
        crew = self.client("crew@captureit.local")
        with ops.get_db() as conn:
            dimas_id = conn.execute("SELECT id FROM users WHERE email='crew@captureit.local'").fetchone()["id"]
            coordinator_id = conn.execute("SELECT id FROM users WHERE email='coordinator@captureit.local'").fetchone()["id"]
            pic_position = conn.execute("SELECT id FROM positions WHERE name='PIC Event'").fetchone()["id"]
            event_id = conn.execute("""INSERT INTO events(project_code,title,starts_at,ends_at,location,coordinator_id)
                VALUES('TEST-MANUAL-PIC-26001','Manual PIC role test','2026-10-11T09:00:00+07:00','2026-10-11T18:00:00+07:00','Jakarta',?)""",
                (coordinator_id,)).lastrowid

        status, _, body = coordinator.request(f"/api/events/{event_id}/assignment", "POST", {
            "user_id": dimas_id, "assignment_type": "pic", "position_id": pic_position,
        })
        self.assertEqual(status, 200, body.decode())
        detail = self.event(crew,event_id)
        self.assertEqual(detail["assignments"][0]["assignment_type"], "pic")
        self.assertNotIn("advances.request", json.loads(crew.request("/api/bootstrap")[2])["user"]["permissions"])
        status, _, body = crew.request(f"/api/events/{event_id}/advance", "POST", {"action":"submit"})
        self.assertEqual(status, 200, body.decode())
        self.assertEqual(json.loads(body)["status"], "submitted")

    def test_event_clear_opens_scoped_performance_review(self):
        coordinator = self.client("coordinator@captureit.local")
        crew = self.client("crew@captureit.local")
        naya = self.client("naya@captureit.local")
        head_ops = self.client("head.ops@captureit.local")
        with ops.get_db() as conn:
            dimas_id = conn.execute("SELECT id FROM users WHERE email='crew@captureit.local'").fetchone()["id"]
            crew_pos = conn.execute("SELECT id FROM positions WHERE name='Crew'").fetchone()["id"]
            event_id = conn.execute("""INSERT INTO events(project_code,title,starts_at,ends_at,location,coordinator_id)
                VALUES('TEST-PERF-26001','Performance privacy test','2026-09-10T09:00:00+07:00','2026-09-10T18:00:00+07:00','Jakarta',?)""",
                (conn.execute("SELECT id FROM users WHERE email='coordinator@captureit.local'").fetchone()["id"],)).lastrowid
            assignment_id = conn.execute("""INSERT INTO event_assignments(event_id,user_id,assignment_type,position_id)
                VALUES(?,?,'pic',?)""", (event_id,dimas_id,crew_pos)).lastrowid
            conn.execute("INSERT INTO attendance(assignment_id) VALUES(?)", (assignment_id,))

        self.assertEqual(crew.request("/api/bootstrap")[0], 200)
        crew_events = {row["id"] for row in json.loads(crew.request("/api/bootstrap")[2])["events"]}
        naya_events = {row["id"] for row in json.loads(naya.request("/api/bootstrap")[2])["events"]}
        self.assertIn(event_id, crew_events)
        self.assertNotIn(event_id, naya_events)
        self.assertEqual(naya.request(f"/api/events/{event_id}")[0], 403)
        self.assertEqual(crew.request(f"/api/events/{event_id}/performance", "POST", {"action":"clear"})[0], 403)
        self.assertEqual(head_ops.request(f"/api/events/{event_id}/performance", "POST", {"action":"clear"})[0], 403)

        self.assertEqual(coordinator.request(f"/api/events/{event_id}/performance", "POST", {"action":"clear"})[0], 400)
        self.mutate(crew, f"/api/events/{event_id}/closing", {"action":"submit","version":0,"photos":[],"data":{
            "actual_start":"2026-09-10T09:00:00+07:00","actual_end":"2026-09-10T18:00:00+07:00",
            "service_result":"planned","printing_mode":"digital","ribbon_rolls":[]}})
        self.mutate(coordinator, f"/api/events/{event_id}/closing", {"action":"accept","version":1})
        self.mutate(coordinator, f"/api/events/{event_id}/performance", {"action":"clear"})
        cleared = self.event(coordinator,event_id)
        self.assertEqual(cleared["status"], "completed")
        self.assertIsNone(cleared["assignments"][0]["performance_review"])
        self.assertEqual(head_ops.request(f"/api/events/{event_id}/performance", "POST", {"reviews": []})[0], 403)
        status, _, body = coordinator.request(f"/api/events/{event_id}/performance", "POST", {"reviews": []})
        self.assertEqual(status, 400, body.decode())

        self.mutate(coordinator, f"/api/events/{event_id}/performance", {"reviews":[{
            "assignment_id": assignment_id,
            "scores":{"work_quality":3,"punctuality":2,"teamwork":3,"sop_equipment":3},
            "note":"Perlu meningkatkan ketepatan waktu saat persiapan.",
        }]})
        reviewed = self.event(coordinator,event_id)
        self.assertEqual(reviewed["assignments"][0]["performance_review"]["score_percent"], 55)
        self.assertEqual(reviewed["assignments"][0]["performance_review"]["note"], "Perlu meningkatkan ketepatan waktu saat persiapan.")
        self.assertEqual(coordinator.request(f"/api/events/{event_id}/assignment", "POST", {"action":"remove","assignment_id":assignment_id})[0], 400)

        dimas_reviews = json.loads(crew.request("/api/bootstrap")[2])["performance_reviews"]
        self.assertIn(event_id, {row["event_id"] for row in dimas_reviews})
        self.assertEqual({row["subject_id"] for row in dimas_reviews}, {dimas_reviews[0]["subject_id"]})
        event_review = next(row for row in dimas_reviews if row['event_id'] == event_id)
        self.assertIn("ketepatan waktu", event_review["note"].lower())
        naya_reviews = json.loads(naya.request("/api/bootstrap")[2])["performance_reviews"]
        self.assertNotIn(event_id, {row["event_id"] for row in naya_reviews})
        coordinator_reviews = json.loads(coordinator.request("/api/bootstrap")[2])["performance_reviews"]
        self.assertIn(event_id, {row["event_id"] for row in coordinator_reviews})

    def test_account_specific_kpi_history_and_admin_finance_acl(self):
        coordinator = self.client("coordinator@captureit.local")
        with ops.get_db() as conn:
            naya_id = conn.execute("SELECT id FROM users WHERE email='naya@captureit.local'").fetchone()["id"]
            pic_id = conn.execute("SELECT id FROM users WHERE email='pic@captureit.local'").fetchone()["id"]
            coordinator_id = conn.execute("SELECT id FROM users WHERE email='coordinator@captureit.local'").fetchone()["id"]
        head_ops = self.client("head.ops@captureit.local")
        self.mutate(head_ops, "/api/kpi", {"subject_id": coordinator_id, "category": "Koordinasi event", "score": 5, "note": "Brief rapi", "review_period": "2026-09"})

        crew = self.client("naya@captureit.local")
        status, _, body = crew.request("/api/bootstrap")
        crew_data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("kpi.read_own", crew_data["user"]["permissions"])
        self.assertEqual(crew_data["kpi_reviews"], [])
        naya_event_reviews = crew_data["performance_reviews"]
        self.assertEqual({row["subject_id"] for row in naya_event_reviews}, {naya_id})
        self.assertIn("CI-OPS-25988", {row["project_code"] for row in naya_event_reviews})
        self.assertEqual(next(row["score_percent"] for row in naya_event_reviews if row["project_code"] == "CI-OPS-25988"), 75)

        pic = self.client("pic@captureit.local")
        status, _, body = pic.request("/api/bootstrap")
        pic_data = json.loads(body)
        self.assertEqual(pic_data["kpi_reviews"], [])
        self.assertEqual({row["subject_id"] for row in pic_data["performance_reviews"]}, {pic_id})
        self.assertNotIn(naya_id, {row["subject_id"] for row in pic_data["performance_reviews"]})

        status, _, body = coordinator.request("/api/bootstrap")
        history = json.loads(body)
        visible_subjects = {row["id"] for row in history["kpi_review_subjects"]}
        self.assertIn(naya_id, visible_subjects)
        self.assertIn(pic_id, visible_subjects)
        self.assertNotIn(coordinator_id, visible_subjects)
        self.assertNotIn(coordinator_id, {row["subject_id"] for row in history["kpi_reviews"]})
        self.assertIn(naya_id, {row["subject_id"] for row in history["performance_reviews"]})
        self.assertIn(pic_id, {row["subject_id"] for row in history["performance_reviews"]})
        status, _, body = coordinator.request("/api/kpi", "POST", {"subject_id": naya_id, "category": "Kualitas kerja", "score": 4, "note": "Gunakan review event", "review_period": "2026-09"})
        self.assertEqual(status, 400, body.decode())

        admin_finance = self.client("admin.finance@captureit.local")
        status, _, body = admin_finance.request("/api/bootstrap")
        admin_data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertNotIn("fees.manage", admin_data["user"]["permissions"])
        self.assertEqual(admin_data["rates"], [])
        self.assertEqual(admin_finance.request("/api/rates", "POST", {"user_id": naya_id, "base_fee_rupiah": 90000, "effective_from": "2026-09-01"})[0], 403)

    def test_admin_account_management_and_guardrails(self):
        admin = self.client("admin@captureit.local")
        status, _, body = admin.request("/api/users", "POST", {
            "full_name": "Crew Baru", "email": "crew.baru@captureit.local", "role": "crew", "password": "StrongPass!2026"
        })
        self.assertEqual(status, 200, body.decode())
        new_user_id = json.loads(body)["id"]
        new_user = self.client("crew.baru@captureit.local","StrongPass!2026")
        self.mutate(admin, f"/api/users/{new_user_id}/update", {"action": "role", "role": "pic_event"})
        status, _, body = new_user.request("/api/me")
        self.assertEqual(status, 200)
        self.assertIn("advances.request", json.loads(body)["user"]["permissions"])
        self.mutate(admin, f"/api/users/{new_user_id}/update", {"action": "active", "active": False})
        self.assertEqual(new_user.request("/api/me")[0], 401)
        self.mutate(admin, f"/api/users/{new_user_id}/update", {"action": "active", "active": True})

        with ops.get_db() as conn:
            admin_id = conn.execute("SELECT id FROM users WHERE email='admin@captureit.local'").fetchone()["id"]
        coordinator = self.client("coordinator@captureit.local")
        self.assertEqual(coordinator.request("/api/users", "POST", {"full_name":"Blocked","email":"blocked@captureit.local","role":"crew","password":"StrongPass!2026"})[0], 403)
        self.assertEqual(admin.request(f"/api/users/{admin_id}/update", "POST", {"action":"active","active":False})[0], 400)
        self.assertEqual(admin.request(f"/api/users/{admin_id}/update", "POST", {"action":"role","role":"head_operations"})[0], 400)

    def test_configure_branding_accounts_and_role_acl(self):
        admin = self.client("admin@captureit.local")
        coordinator = self.client("coordinator@captureit.local")
        status, _, body = admin.request("/api/bootstrap")
        admin_data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIsNotNone(admin_data["configure"])
        self.assertIn("app.configure", admin_data["user"]["permissions"])
        self.assertIsNone(json.loads(coordinator.request("/api/bootstrap")[2])["configure"])
        self.assertEqual(coordinator.request("/api/configure/appearance", "POST", {"colors": {}})[0], 403)

        colors = {"primary":"#123456", "accent":"#cba987", "sidebar":"#201030", "background":"#f1f2f3"}
        result = self.mutate(admin, "/api/configure/appearance", {"colors": colors})
        self.assertEqual(result["branding"]["colors"], colors)
        self.assertEqual(admin.request("/api/configure/appearance", "POST", {"colors": {**colors,"primary":"purple"}})[0], 400)

        logo = base64.b64encode((ops.STATIC / "logo.png").read_bytes()).decode()
        status, _, body = admin.request("/api/configure/asset", "POST", {"kind":"logo", "image":f"data:image/png;base64,{logo}"})
        self.assertEqual(status, 200, body.decode())
        logo_url = json.loads(body)["branding"]["logo_url"].split("?", 1)[0]
        status, headers, content = admin.request(logo_url)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "image/png")
        self.assertEqual(content, (ops.STATIC / "logo.png").read_bytes())

        crew = self.client("crew@captureit.local")
        custom = ["events.read_own", "kpi.read_own", "profile.read_own", "profile.update_own"]
        self.mutate(admin, "/api/configure/roles", {"role":"crew", "permissions":custom})
        crew_data = json.loads(crew.request("/api/bootstrap")[2])
        self.assertNotIn("attendance.self", crew_data["user"]["permissions"])
        self.assertIn("events.read_own", crew_data["user"]["permissions"])
        with ops.get_db() as conn:
            ops.seed_reference_data(conn)
            crew_id = conn.execute("SELECT id FROM users WHERE email='crew@captureit.local'").fetchone()["id"]
            self.assertNotIn("attendance.self", ops.role_data(conn, crew_id)[1])
        self.assertEqual(admin.request("/api/configure/roles", "POST", {"role":"crew", "permissions":["users.manage"]})[0], 400)
        self.mutate(admin, "/api/configure/roles", {"role":"crew", "reset_default":True})
        self.assertIn("attendance.self", json.loads(crew.request("/api/bootstrap")[2])["user"]["permissions"])

    def test_calendar_sync_waits_for_admin_crm_code_confirmation(self):
        calendar_payload = {
            "items": [
                {"id":"google-intake-1","summary":"Brand Activation · Jakarta","description":"PROJECT_CODE: CI-GGL-26001","start":{"dateTime":"2026-11-02T10:00:00+07:00"},"end":{"dateTime":"2026-11-02T18:00:00+07:00"},"location":"Jakarta"},
                {"id":"google-intake-2","summary":"Wedding · Bandung","description":"Detail dari sales","start":{"dateTime":"2026-11-03T10:00:00+07:00"},"end":{"dateTime":"2026-11-03T16:00:00+07:00"},"location":"Bandung"},
            ]
        }
        replies = [io.BytesIO(b'{"access_token":"test-token"}'), io.BytesIO(json.dumps(calendar_payload).encode())]
        env = {"GOOGLE_CLIENT_ID":"id","GOOGLE_CLIENT_SECRET":"secret","GOOGLE_REFRESH_TOKEN":"refresh","GOOGLE_CALENDAR_ID":"calendar"}
        with patch.dict(os.environ, env), patch.object(ops, "urlopen", side_effect=replies):
            with ops.get_db() as conn:
                result = ops.google_sync(conn)
        self.assertTrue(result["ok"])
        self.assertEqual(result["queued"], 2)
        with ops.get_db() as conn:
            rows = conn.execute("SELECT id,google_event_id,suggested_project_code FROM calendar_code_queue ORDER BY id").fetchall()
        self.assertEqual(rows[0]["suggested_project_code"], "CI-GGL-26001")

        admin = self.client("admin@captureit.local")
        coordinator = self.client("coordinator@captureit.local")
        status, _, body = admin.request("/api/bootstrap")
        self.assertEqual(len(json.loads(body)["calendar_code_queue"]), 2)
        self.assertEqual(coordinator.request(f"/api/calendar-code/{rows[0]['id']}", "POST", {"project_code":"CI-MAN-26001"})[0], 403)

        self.mutate(admin, f"/api/calendar-code/{rows[0]['id']}", {"project_code":"CI-MAN-26001"})
        with ops.get_db() as conn:
            event = conn.execute("SELECT id,google_event_id FROM events WHERE project_code='CI-MAN-26001'").fetchone()
            self.assertEqual(event["google_event_id"], "google-intake-1")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM design_tasks WHERE event_id=?", (event["id"],)).fetchone()[0], 1)
        status, _, body = admin.request(f"/api/calendar-code/{rows[1]['id']}", "POST", {"project_code":"CI-MAN-26001"})
        self.assertEqual(status, 400)
        self.mutate(admin, f"/api/calendar-code/{rows[1]['id']}", {"project_code":"CI-MAN-26002"})
        status, _, body = admin.request("/api/bootstrap")
        self.assertEqual(json.loads(body)["calendar_code_queue"], [])

    def test_daily_calendar_sync_schedule_and_persisted_status(self):
        now = ops.datetime(2026, 9, 28, 12, 0, tzinfo=ops.timezone.utc)
        day = 24 * 60 * 60
        with patch.dict(os.environ, {"GOOGLE_SYNC_INTERVAL_HOURS":"24"}):
            self.assertEqual(ops.google_sync_interval_seconds(), day)
        self.assertTrue(ops.google_sync_due(None, now=now, interval_seconds=day))
        self.assertFalse(ops.google_sync_due("2026-09-27T13:00:00+00:00", now=now, interval_seconds=day))
        self.assertTrue(ops.google_sync_due("2026-09-27T12:00:00+00:00", now=now, interval_seconds=day))
        self.assertFalse(ops.google_sync_due(None, now=now, interval_seconds=0))

        with ops.get_db() as conn:
            admin_id = conn.execute("SELECT id FROM users WHERE email='admin@captureit.local'").fetchone()["id"]
        success = {"ok":True,"status":200,"message":"Sinkronisasi test berhasil.","queued":2,"queue_updated":1,"updated":3,"skipped":0}
        with patch.object(ops, "google_sync", return_value=success):
            result = ops.run_google_sync("manual", admin_id)
        self.assertTrue(result["ok"])
        with ops.get_db() as conn:
            synced = ops.google_sync_status(conn)
        self.assertEqual(synced["status"], "success")
        self.assertEqual(synced["trigger"], "manual")
        self.assertEqual(synced["queued_count"], 2)
        self.assertEqual(synced["updated_count"], 4)
        self.assertTrue(synced["last_success_at"])

        with patch.object(ops, "google_sync", return_value={"ok":False,"status":502,"message":"Token ditolak."}):
            result = ops.run_google_sync("automatic")
        self.assertFalse(result["ok"])
        with ops.get_db() as conn:
            failed = ops.google_sync_status(conn)
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["trigger"], "automatic")
        self.assertEqual(failed["last_success_at"], synced["last_success_at"])
        self.assertEqual(failed["message"], "Token ditolak.")

    def test_legacy_position_rates_migrate_to_per_account(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript((Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8"))
        user_id = conn.execute("INSERT INTO users(full_name,email,password_hash) VALUES('Legacy Crew','legacy@captureit.local','x')").lastrowid
        position_id = conn.execute("INSERT INTO positions(name) VALUES('Crew')").lastrowid
        event_id = conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at) VALUES('LEGACY-1','Event','2026-09-01T09:00:00+07:00','2026-09-01T17:00:00+07:00')").lastrowid
        conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type,position_id) VALUES(?,?,'crew',?)", (event_id,user_id,position_id))
        conn.execute("INSERT INTO fee_rates(position_id,base_fee_rupiah,effective_from) VALUES(?,?,?)", (position_id,175000,"2026-01-01"))
        ops.migrate_user_fee_rates(conn)
        migrated = conn.execute("SELECT base_fee_rupiah FROM user_fee_rates WHERE user_id=? AND effective_from='2026-01-01'", (user_id,)).fetchone()
        self.assertEqual(migrated["base_fee_rupiah"], 175000)
        conn.close()

    def test_existing_database_gains_assignment_fee_snapshots(self):
        schema = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")
        schema = schema.replace("    base_fee_snapshot_rupiah INTEGER CHECK (base_fee_snapshot_rupiah IS NULL OR base_fee_snapshot_rupiah >= 0),\n", "")
        schema = schema.replace("    skill_name_snapshot TEXT NOT NULL DEFAULT '',\n", "")
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(schema)
        user_id = conn.execute("INSERT INTO users(full_name,email,password_hash) VALUES('Legacy Staff','legacy.staff@captureit.local','x')").lastrowid
        position_id = conn.execute("INSERT INTO positions(name) VALUES('Crew')").lastrowid
        event_id = conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at) VALUES('LEGACY-SNAP-1','Event','2026-09-01T09:00:00+07:00','2026-09-01T17:00:00+07:00')").lastrowid
        assignment_id = conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type,position_id) VALUES(?,?,'crew',?)", (event_id,user_id,position_id)).lastrowid
        skill_id = conn.execute("INSERT INTO skills(name,extra_fee_rupiah) VALUES('Old Skill',30000)").lastrowid
        conn.execute("INSERT INTO assignment_skills(assignment_id,skill_id,extra_fee_rupiah) VALUES(?,?,?)", (assignment_id,skill_id,30000))
        ops.seed_reference_data(conn)
        assignment = conn.execute("SELECT base_fee_snapshot_rupiah FROM event_assignments WHERE id=?", (assignment_id,)).fetchone()
        skill = conn.execute("SELECT skill_name_snapshot FROM assignment_skills WHERE assignment_id=? AND skill_id=?", (assignment_id,skill_id)).fetchone()
        self.assertIsNone(assignment["base_fee_snapshot_rupiah"])
        self.assertEqual(skill["skill_name_snapshot"], "Old Skill")
        conn.close()

    def test_pic_finance_advance_workflow(self):
        event_id = self.event_id("CI-OPS-26003")
        pic = self.client("pic@captureit.local")
        self.mutate(pic, f"/api/events/{event_id}/advance", {"action": "submit"})
        self.assertEqual(self.event(pic, event_id)["advance_status"], "submitted")
        finance = self.client("finance@captureit.local")
        self.mutate(finance, f"/api/events/{event_id}/advance", {"action": "approve"})
        self.mutate(finance, f"/api/events/{event_id}/advance", {"action": "transfer"})
        self.assertEqual(self.event(finance, event_id)["advance_status"], "transferred")

    def test_cash_advance_documents_are_private_and_downloadable_by_finance(self):
        event_id=self.event_id("CI-OPS-26004")
        pic=self.client("pic@captureit.local")
        finance=self.client("finance@captureit.local")
        head_finance=self.client("head.finance@captureit.local")
        admin_finance=self.client("admin.finance@captureit.local")
        crew=self.client("crew@captureit.local")
        pdf=b"%PDF-1.4\nCapture It expense report\n%%EOF"
        payload={"filename":"Rekap pengeluaran.pdf","file":"data:application/pdf;base64,"+base64.b64encode(pdf).decode()}
        self.assertEqual(pic.request(f"/api/events/{event_id}/advance/documents","POST",payload)[0],400)
        self.assertEqual(crew.request(f"/api/events/{event_id}/advance/documents","POST",payload)[0],403)
        self.mutate(pic,f"/api/events/{event_id}/advance",{"action":"submit"})
        self.mutate(finance,f"/api/events/{event_id}/advance",{"action":"approve"})
        self.mutate(head_finance,f"/api/events/{event_id}/advance",{"action":"transfer"})
        status,_,body=pic.request(f"/api/events/{event_id}/advance/documents","POST",payload)
        self.assertEqual(status,200,body.decode())
        document_id=json.loads(body)["document_id"]
        detail=self.event(pic,event_id)
        self.assertEqual(detail["advance_documents"][0]["original_filename"],"Rekap pengeluaran.pdf")
        self.assertEqual(crew.request(f"/api/advance-documents/{document_id}/download")[0],403)
        status,headers,downloaded=finance.request(f"/api/advance-documents/{document_id}/download")
        self.assertEqual(status,200)
        self.assertEqual(headers.get("Content-Disposition"),'attachment; filename="Rekap pengeluaran.pdf"')
        self.assertEqual(downloaded,pdf)
        docs=json.loads(admin_finance.request("/api/advance-documents")[2])["documents"]
        self.assertIn(document_id,{doc["id"] for doc in docs})

    def test_warehouse_readiness_return_and_design_board(self):
        event_id = self.event_id("CI-OPS-26002")
        warehouse = self.client("warehouse@captureit.local")
        self.mutate(warehouse, f"/api/events/{event_id}/warehouse", {"action": "ready"})
        self.mutate(warehouse, f"/api/events/{event_id}/warehouse", {"action": "dispatch"})
        self.mutate(warehouse, f"/api/events/{event_id}/warehouse", {"action": "returned"})
        self.assertEqual(self.event(warehouse, event_id)["warehouse_status"], "returned")

        design = self.client("design@captureit.local")
        status, _, body = design.request("/api/bootstrap")
        task = next(task for task in json.loads(body)["design_tasks"] if task["event_id"] == event_id)
        self.mutate(design, f"/api/design/{task['id']}/status", {"status": "approved"})
        self.assertEqual(self.event(design, event_id)["design_status"], "approved")

    def test_attendance_group_and_assignment(self):
        event_id = self.event_id("CI-OPS-26001")
        # Crew may only check in on the event date, so pretend today is that date.
        with ops.get_db() as conn:
            event_day = ops.eventdays.event_dates(conn.execute("SELECT starts_at,ends_at FROM events WHERE id=?", (event_id,)).fetchone())[0]
        patcher = unittest.mock.patch.object(ops.eventdays, "today_wib", lambda: event_day)
        patcher.start(); self.addCleanup(patcher.stop)
        crew = self.client("crew@captureit.local")
        detail = self.event(crew, event_id)
        own = next(row for row in detail["assignments"] if row["user_id"] == json.loads(crew.request("/api/me")[2])["user"]["id"])
        attendance_url = f"/api/events/{event_id}/attendance"
        status, _, body = crew.request(attendance_url, "POST", {"assignment_id": own["assignment_id"], "action": "check_in"})
        self.assertEqual(status, 400, body.decode())
        photo = b"\xff\xd8\xff\xe0test-camera-jpeg\xff\xd9"
        photo_url = "data:image/jpeg;base64," + base64.b64encode(photo).decode()
        location = {"latitude": -6.2, "longitude": 106.8, "accuracy_m": 10, "captured_at": ops.now_iso()}
        check_in = self.mutate(crew, attendance_url, {"assignment_id": own["assignment_id"], "action": "check_in", "photo": photo_url, "location": location})
        self.assertTrue(check_in["timestamp"])
        checked_in = next(row for row in self.event(crew,event_id)["assignments"] if row["assignment_id"] == own["assignment_id"])
        self.assertEqual(checked_in["attendance_status"], "checked_in")
        self.assertTrue(checked_in["has_check_in_photo"])
        self.assertEqual(crew.request(attendance_url, "POST", {"assignment_id": own["assignment_id"], "action": "check_in", "photo": photo_url, "location": location})[0], 400)
        status, headers, body = crew.request(f"/api/attendance/{own['assignment_id']}/photo/check_in")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/jpeg")
        self.assertEqual(body, photo)
        warehouse = self.client("warehouse@captureit.local")
        self.assertEqual(warehouse.request(f"/api/attendance/{own['assignment_id']}/photo/check_in")[0], 403)
        self.mutate(crew, attendance_url, {"assignment_id": own["assignment_id"], "action": "check_out", "photo": photo_url, "location": {**location, "captured_at": ops.now_iso()}})
        checked_out = next(row for row in self.event(crew,event_id)["assignments"] if row["assignment_id"] == own["assignment_id"])
        self.assertEqual(checked_out["attendance_status"], "checked_out")
        self.assertTrue(checked_out["has_check_out_photo"])
        self.assertEqual(crew.request(attendance_url, "POST", {"assignment_id": own["assignment_id"], "action": "check_out", "photo": photo_url, "location": {**location, "captured_at": ops.now_iso()}})[0], 400)
        with ops.get_db() as conn:
            stored = conn.execute("SELECT check_in_latitude,check_in_longitude,check_in_accuracy_m,check_out_accuracy_m FROM attendance WHERE assignment_id=?", (own["assignment_id"],)).fetchone()
        self.assertEqual((stored["check_in_latitude"], stored["check_in_longitude"], stored["check_in_accuracy_m"], stored["check_out_accuracy_m"]), (-6.2, 106.8, 10.0, 10.0))

        coordinator = self.client("coordinator@captureit.local")
        self.mutate(coordinator, f"/api/events/{event_id}/group", {"status": "invites_sent", "group_link": "https://chat.whatsapp.com/example"})
        self.assertEqual(self.event(coordinator,event_id)["group_status"], "invites_sent")
        new_event = self.event_id("CI-OPS-26004")
        with ops.get_db() as conn:
            naya_id = conn.execute("SELECT id FROM users WHERE email='naya@captureit.local'").fetchone()["id"]
            crew_pos = conn.execute("SELECT id FROM positions WHERE name='Crew'").fetchone()["id"]
            pic_pos = conn.execute("SELECT id FROM positions WHERE name='PIC Event'").fetchone()["id"]
        admin = self.client("admin@captureit.local")
        status, _, body = admin.request("/api/users", "POST", {"full_name": "Test PIC Scheduler", "email": "test.pic.scheduler@captureit.local", "role": "pic_event", "password": "StrongPass!2026"})
        self.assertEqual(status, 200, body.decode())
        pic_id = json.loads(body)["id"]
        head_ops = self.client("head.ops@captureit.local")
        self.mutate(head_ops, "/api/skills", {"name": "Laser Setup", "extra_fee_rupiah": 75000})
        self.mutate(head_ops, "/api/rates", {"user_id": naya_id, "base_fee_rupiah": 140000, "effective_from": "2026-01-01"})
        with ops.get_db() as conn:
            skill_id = conn.execute("SELECT id FROM skills WHERE name='Laser Setup'").fetchone()["id"]
        self.mutate(coordinator, f"/api/events/{new_event}/assignment", {"user_id": naya_id, "assignment_type": "crew", "position_id": crew_pos, "skill_ids": [skill_id]})
        assigned = next(row for row in self.event(coordinator,new_event)["assignments"] if row["full_name"] == "Naya Putri")
        self.assertEqual(assigned["skills"], "Laser Setup")
        with ops.get_db() as conn:
            assignment_snapshot = conn.execute("SELECT base_fee_snapshot_rupiah FROM event_assignments WHERE id=?", (assigned["assignment_id"],)).fetchone()
            skill_snapshot = conn.execute("SELECT extra_fee_rupiah,skill_name_snapshot FROM assignment_skills WHERE assignment_id=? AND skill_id=?", (assigned["assignment_id"],skill_id)).fetchone()
        self.assertEqual(assignment_snapshot["base_fee_snapshot_rupiah"], 140000)
        self.assertEqual(skill_snapshot["extra_fee_rupiah"], 75000)
        self.assertEqual(skill_snapshot["skill_name_snapshot"], "Laser Setup")
        self.mutate(head_ops, "/api/skills", {"id": skill_id, "name": "Laser Setup v2", "extra_fee_rupiah": 90000})
        self.mutate(head_ops, "/api/rates", {"user_id": naya_id, "base_fee_rupiah": 155000, "effective_from": "2026-01-01"})
        with ops.get_db() as conn:
            assignment_snapshot = conn.execute("SELECT base_fee_snapshot_rupiah FROM event_assignments WHERE id=?", (assigned["assignment_id"],)).fetchone()
            skill_snapshot = conn.execute("SELECT extra_fee_rupiah,skill_name_snapshot FROM assignment_skills WHERE assignment_id=? AND skill_id=?", (assigned["assignment_id"],skill_id)).fetchone()
        self.assertEqual(assignment_snapshot["base_fee_snapshot_rupiah"], 140000)
        self.assertEqual(skill_snapshot["extra_fee_rupiah"], 75000)
        self.assertEqual(skill_snapshot["skill_name_snapshot"], "Laser Setup")
        renamed_view = next(row for row in self.event(coordinator,new_event)["assignments"] if row["assignment_id"] == assigned["assignment_id"])
        self.assertEqual(renamed_view["skills"], "Laser Setup")
        self.mutate(coordinator, f"/api/events/{new_event}/assignment", {"user_id": pic_id, "assignment_type": "pic", "position_id": pic_pos})
        pic_assignment = next(row for row in self.event(coordinator,new_event)["assignments"] if row["user_id"] == pic_id)
        self.assertEqual(pic_assignment["assignment_type"], "pic")

    def test_fee_edit_and_payroll_snapshot(self):
        head_ops = self.client("head.ops@captureit.local")
        with ops.get_db() as conn:
            dimas_id = conn.execute("SELECT id FROM users WHERE email='crew@captureit.local'").fetchone()["id"]
            naya_id = conn.execute("SELECT id FROM users WHERE email='naya@captureit.local'").fetchone()["id"]
        self.mutate(head_ops, "/api/rates", {"user_id": dimas_id, "base_fee_rupiah": 180000, "effective_from": "2026-01-01"})
        self.mutate(head_ops, "/api/rates", {"user_id": naya_id, "base_fee_rupiah": 125000, "effective_from": "2026-01-01"})
        finance = self.client("finance@captureit.local")
        status, _, body = finance.request("/api/payroll?month=2026-09")
        self.assertEqual(status, 200)
        preview = json.loads(body)
        self.assertGreater(preview["total"], 0)
        status, headers, body = finance.request("/api/payroll/export", "POST", {"month": "2026-09", "format": "csv"})
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers.get("Content-Type", ""))
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        self.assertTrue(rows)
        self.assertTrue(any(row["Nama"] == "Dimas Ardi" and row["Fee Dasar (Rp)"] == "180000" for row in rows))
        self.assertTrue(any(row["Nama"] == "Naya Putri" and row["Fee Dasar (Rp)"] == "125000" for row in rows))
        self.mutate(head_ops, "/api/rates", {"user_id": dimas_id, "base_fee_rupiah": 250000, "effective_from": "2026-01-01"})
        status, headers, body = finance.request("/api/payroll/export", "POST", {"month": "2026-09", "format": "csv"})
        self.assertEqual(status, 200)
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        self.assertTrue(any(row["Nama"] == "Dimas Ardi" and row["Fee Dasar (Rp)"] == "180000" for row in rows))
        status, _, body = finance.request("/api/payroll?start=2026-09-19&end=2026-09-25")
        self.assertEqual(status, 200, body.decode())
        weekly = json.loads(body)
        self.assertEqual(weekly["rows"], [])  # September batch already claimed these assignments.
        status, headers, body = finance.request("/api/payroll/export", "POST", {"start": "2026-09-19", "end": "2026-09-25"})
        self.assertEqual(status, 200)
        self.assertIn("spreadsheetml.sheet", headers.get("Content-Type", ""))
        with zipfile.ZipFile(io.BytesIO(body)) as workbook:
            self.assertIn("xl/worksheets/sheet1.xml", workbook.namelist())
            sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
            ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            cells = {cell.attrib["r"]: cell for cell in sheet.findall(".//x:c", ns)}
            self.assertIn("Fee Skill (Rp)", "".join(sheet.itertext()))
            self.assertNotIn("K5", cells)
        status, headers, body = finance.request("/api/payroll/export", "POST", {"start": "2026-09-19", "end": "2026-09-25", "format": "csv"})
        self.assertEqual(status, 200, body.decode())
        weekly_rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        self.assertEqual(weekly_rows, [])
        self.assertIn("Fee Skill (Rp)", body.decode("utf-8-sig"))
        payroll_state=json.loads(finance.request("/api/payroll?month=2026-09")[2])
        batch=payroll_state["batch"]
        self.assertIsNotNone(batch)
        crew=self.client("crew@captureit.local")
        self.assertIn("payroll.read_own",json.loads(crew.request("/api/bootstrap")[2])["user"]["permissions"])
        pic=self.client("pic@captureit.local")
        self.assertIn("payroll.read_own",json.loads(pic.request("/api/bootstrap")[2])["user"]["permissions"])
        self.assertEqual(crew.request("/api/payroll/slips?year=2026")[0],200)
        self.assertEqual(json.loads(crew.request("/api/payroll/slips?year=2026")[2])["slips"],[])
        self.assertEqual(finance.request(f"/api/payroll/batch/{batch['id']}/transfer","POST",{"transfer_reference":"TRF-SEPTEMBER-1","pay_date":"2026-09-26"})[0],200)
        self.assertEqual(finance.request(f"/api/payroll/batch/{batch['id']}/transfer","POST",{"transfer_reference":"TRF-SEPTEMBER-DUP","pay_date":"2026-09-26"})[0],400)
        crew_slips=json.loads(crew.request("/api/payroll/slips?year=2026")[2])
        self.assertEqual(crew_slips["selected_year"],"2026")
        self.assertEqual(len(crew_slips["slips"]),1)
        self.assertEqual(crew_slips["slips"][0]["transfer_reference"],"TRF-SEPTEMBER-1")
        self.assertTrue(all(row["event_title"] and row["project_code"] for row in crew_slips["slips"][0]["rows"]))
        self.assertEqual(json.loads(crew.request("/api/payroll/slips?year=2025")[2])["slips"],[])
        self.assertEqual(finance.request(f"/api/payroll/batch/{batch['id']}/transfer","POST",{})[0],400)
        self.assertEqual(finance.request("/api/payroll?start=2026-09-26&end=2026-09-19")[0], 400)

    def test_finance_inhouse_attendance_export_and_monthly_slips(self):
        admin = self.client("admin@captureit.local")
        status, _, body = admin.request("/api/users", "POST", {
            "full_name":"In-house Payroll Test", "email":"inhouse.payroll.test@captureit.local",
            "role":"content_team", "password":"StrongPass!2026",
        })
        self.assertEqual(status,200,body.decode())
        with ops.get_db() as conn:
            person_id = conn.execute("SELECT id FROM users WHERE email='inhouse.payroll.test@captureit.local'").fetchone()["id"]
        worker = self.client("inhouse.payroll.test@captureit.local","StrongPass!2026")
        finance = self.client("finance@captureit.local")
        self.assertEqual(worker.request("/api/inhouse-payroll/slips")[0],200)
        photo_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffinhouse-payroll-photo\xff\xd9").decode()
        location = {"latitude": -6.2, "longitude": 106.8, "accuracy_m": 10, "captured_at": ops.now_iso()}
        self.assertEqual(worker.request("/api/inhouse-attendance","POST",{"action":"check_in","photo":photo_url,"location":location})[0],200)
        self.assertEqual(worker.request("/api/inhouse-attendance","POST",{"action":"check_in","photo":photo_url,"location":location})[0],400)
        self.assertEqual(worker.request("/api/inhouse-attendance","POST",{"action":"check_out","photo":photo_url,"location":{**location,"captured_at":ops.now_iso()}})[0],200)
        self.assertEqual(worker.request("/api/inhouse-attendance","POST",{"action":"check_out","photo":photo_url,"location":{**location,"captured_at":ops.now_iso()}})[0],400)
        today = json.loads(worker.request("/api/inhouse-attendance")[2])["today"]
        status, _, body = finance.request(f"/api/inhouse-attendance?date={today}")
        self.assertEqual(status,200,body.decode())
        visible = next(row for row in json.loads(body)["records"] if row["user_id"]==person_id)
        self.assertEqual(visible["status"],"checked_out")
        status, headers, body = finance.request("/api/inhouse-attendance/export","POST",{"start":today,"end":today,"format":"csv"})
        self.assertEqual(status,200,body.decode(errors="replace"))
        self.assertIn("text/csv",headers.get("Content-Type",""))
        exported = next(row for row in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))) if row["Email"]=="inhouse.payroll.test@captureit.local")
        self.assertEqual(exported["Status"],"Selesai")
        self.assertEqual(exported["GPS masuk latitude"],"-6.2")
        self.assertEqual(exported["Akurasi pulang (m)"],"10.0")
        status, _, body = finance.request("/api/inhouse-attendance/export","POST",{"start":today,"end":today,"format":"xlsx"})
        self.assertEqual(status,200,body.decode(errors="replace"))
        with zipfile.ZipFile(io.BytesIO(body)) as workbook:
            sheet=ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        self.assertIn("In-house Payroll Test"," ".join(sheet.itertext()))

        status, _, body = finance.request("/api/inhouse-payroll/salaries","POST",{"entries":[{"user_id":person_id,"monthly_salary_rupiah":5000000}]})
        self.assertEqual(status,200,body.decode())
        period={"start":today,"end":today,"pay_date":today}
        status, _, body = finance.request("/api/inhouse-payroll","POST",{**period,"entries":[{"user_id":person_id,"allowance_rupiah":100000,"deduction_rupiah":50000,"note":"Tunjangan shift"}]})
        self.assertEqual(status,200,body.decode())
        payout=next(row for row in json.loads(body)["rows"] if row["user_id"]==person_id)
        self.assertEqual(payout["total_rupiah"],5050000)
        self.assertEqual(payout["attendance_days"],1)
        self.assertEqual(payout["completed_days"],1)
        transfer_path=f"/api/inhouse-payroll/payout/{payout['payout_id']}/transfer"
        self.assertEqual(finance.request(transfer_path,"POST",{"transfer_reference":"TRF-INHOUSE-TEST"})[0],200)
        self.assertEqual(finance.request(transfer_path,"POST",{"transfer_reference":"TRF-INHOUSE-DUP"})[0],400)
        from datetime import date, timedelta
        # The overlapping salary claim must stay in the same month, including month-end runs.
        adjacent=(date.fromisoformat(today)+timedelta(days=-1 if date.fromisoformat(today).day>1 else 1)).isoformat()
        status, _, body=finance.request("/api/inhouse-payroll","POST",{"start":adjacent,"end":adjacent,"pay_date":adjacent,"entries":[{"user_id":person_id}]})
        self.assertEqual(status,200,body.decode())
        overlapping=next(row for row in json.loads(body)["rows"] if row["user_id"]==person_id)
        self.assertEqual(finance.request(f"/api/inhouse-payroll/payout/{overlapping['payout_id']}/transfer","POST",{"transfer_reference":"TRF-INHOUSE-OVERLAP"})[0],400)
        status, _, body=worker.request("/api/inhouse-payroll/slips")
        self.assertEqual(status,200,body.decode())
        slips=json.loads(body)["slips"]
        self.assertEqual(len(slips),1)
        self.assertEqual(slips[0]["total_rupiah"],5050000)
        self.assertEqual(slips[0]["transfer_reference"],"TRF-INHOUSE-TEST")
        self.assertEqual(slips[0]["note"],"Tunjangan shift")

        admin_finance=self.client("admin.finance@captureit.local")
        # Admin Finance may not see salary lists (In-house) nor fee lists (freelance).
        self.assertEqual(admin_finance.request(f"/api/inhouse-payroll?start={today}&end={today}")[0],403)
        self.assertEqual(admin_finance.request("/api/payroll")[0],403)
        self.assertEqual(admin_finance.request("/api/inhouse-payroll/salaries","POST",{"entries":[]})[0],403)
        crew=self.client("crew@captureit.local")
        self.assertEqual(crew.request(f"/api/inhouse-payroll?start={today}&end={today}")[0],403)
        self.assertEqual(crew.request("/api/inhouse-payroll/slips")[0],403)


if __name__ == "__main__":
    unittest.main()
