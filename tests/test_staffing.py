import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from test_workflows import Client, ops
from staffing import WIB, staff_availability


class StaffingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        ops.DB_PATH = Path(cls.temp.name) / 'staffing.sqlite3'
        ops.initialize(seed=True)
        cls.httpd = ops.ThreadingHTTPServer(('127.0.0.1', 0), ops.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(); cls.temp.cleanup()

    def setUp(self):
        self.coordinator = Client(self.base); self.coordinator.login('coordinator@captureit.local')
        with ops.get_db() as conn:
            conn.execute("DELETE FROM events WHERE project_code LIKE 'staff-test-%'")
            self.uid = conn.execute("SELECT id FROM users WHERE email='crew@captureit.local'").fetchone()[0]
            self.other_uid = conn.execute("SELECT id FROM users WHERE email='pic@captureit.local'").fetchone()[0]
            self.owner = conn.execute("SELECT id FROM users WHERE email='coordinator@captureit.local'").fetchone()[0]
            # Keep relative-date warning fixtures independent of dated demo events.
            conn.execute('DELETE FROM event_assignments WHERE user_id IN (?,?)', (self.uid,self.other_uid))
        self.day = datetime(2040, 6, 1, 8, tzinfo=WIB)
        self.created = 0

    def event(self, offset=0, hours=2, start=None):
        self.created += 1
        start = start or self.day + timedelta(hours=offset)
        with ops.get_db() as conn:
            return conn.execute('''INSERT INTO events(project_code,title,location,starts_at,ends_at,coordinator_id)
                VALUES(?,?,?,?,?,?)''', (f'staff-test-{self.created}', f'Event {self.created}', 'Jakarta',
                                       start.isoformat(), (start + timedelta(hours=hours)).isoformat(), self.owner)).lastrowid

    def assigned(self, event_id, role='crew', buffer=60, uid=None):
        with ops.get_db() as conn:
            aid = conn.execute('''INSERT INTO event_assignments(event_id,user_id,assignment_type,travel_buffer_minutes)
                VALUES(?,?,?,?)''', (event_id, uid or self.uid, role, buffer)).lastrowid
            conn.execute('INSERT INTO attendance(assignment_id) VALUES(?)', (aid,))
            return aid

    def preview(self, eid, minutes=60, client=None):
        status, _, body = (client or self.coordinator).request(f'/api/events/{eid}/staff-availability?user_id={self.uid}&travel_minutes={minutes}')
        return status, json.loads(body)

    def assign(self, eid, expected=200, client=None, **extra):
        status, _, body = (client or self.coordinator).request(f'/api/events/{eid}/assignment', 'POST',
            {'user_id': self.uid, 'assignment_type': 'crew', **extra})
        self.assertEqual(status, expected, body.decode())
        return json.loads(body)

    def test_overlap_warns_but_explicit_confirmation_can_save(self):
        earlier, candidate = self.event(), self.event(1)
        self.assigned(earlier, 'pic')
        status, result = self.preview(candidate)
        self.assertEqual(status, 200); self.assertFalse(result['blocked']);self.assertEqual(result['severity'],'red')
        self.assertEqual(result['conflicts'][0]['roles'], ['pic'])
        self.assign(candidate, 409)
        with ops.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_assignments WHERE event_id=?', (candidate,)).fetchone()[0], 0)
        self.assign(candidate,schedule_confirmed=True,schedule_ack_signature=result['signature'])
        with ops.get_db() as conn:
            row=conn.execute('SELECT schedule_ack_signature,schedule_acknowledged_by,schedule_acknowledged_at FROM event_assignments WHERE event_id=?',(candidate,)).fetchone()
            self.assertEqual(row['schedule_ack_signature'],result['signature']);self.assertEqual(row['schedule_acknowledged_by'],self.owner);self.assertTrue(row['schedule_acknowledged_at'])
            audit=json.loads(conn.execute("SELECT details FROM audit_logs WHERE entity_type='event_assignment' ORDER BY id DESC LIMIT 1").fetchone()[0])
            self.assertEqual(audit['schedule_warning']['severity'],'red')

    def test_loading_never_extends_staff_window_or_invalidates_confirmation(self):
        earlier, candidate = self.event(), self.event(4)
        self.assigned(earlier)
        before=self.preview(candidate)[1]
        with ops.get_db() as conn:
            conn.execute("INSERT INTO event_operations(event_id,loading_date,loading_time) VALUES(?,'2040-06-01','09:30')", (candidate,))
        _, result = self.preview(candidate)
        self.assertFalse(result['blocked']);self.assertEqual(result['severity'],'yellow'); self.assertIn('12:00', result['starts_at'])
        self.assertEqual(result['signature'],before['signature'])
        self.assign(candidate,schedule_confirmed=True,schedule_ack_signature=before['signature'])
        next_day=self.event(24)
        with ops.get_db() as conn:
            conn.execute("INSERT INTO event_operations(event_id,loading_date,loading_time) VALUES(?,'2040-06-01','08:00')",(next_day,))
        self.assertFalse(self.preview(next_day)[1]['needs_confirmation']);self.assign(next_day)

    def test_midnight_timezone_and_half_open_boundary(self):
        earlier = self.event(start=self.day.replace(hour=23), hours=2)
        candidate = self.event(start=(self.day+timedelta(days=1)).replace(hour=0), hours=2)
        self.assigned(earlier, buffer=0)
        with ops.get_db() as conn:
            conn.execute("UPDATE events SET starts_at='2040-06-01T17:00:00Z',ends_at='2040-06-01T19:00:00Z' WHERE id=?", (candidate,))
        self.assertEqual(self.preview(candidate, 0)[1]['severity'],'red')
        with ops.get_db() as conn:
            conn.execute("UPDATE events SET starts_at='2040-06-02T01:00:00+07:00' WHERE id=?", (candidate,))
        result=self.preview(candidate,0)[1]
        self.assertEqual(result['severity'],'yellow');self.assertEqual(result['conflicts'][0]['gap_minutes'],0)
        self.assign(candidate,schedule_confirmed=True,schedule_ack_signature=result['signature'])

    def test_same_day_confirmation_replaces_travel_buffer_and_is_explicit(self):
        earlier, candidate = self.event(), self.event(2.5)
        self.assigned(earlier, buffer=120)
        _, result = self.preview(candidate, 30)
        self.assertFalse(result['blocked']); self.assertTrue(result['needs_confirmation'])
        self.assertNotIn('required_minutes',result['conflicts'][0])
        self.assertEqual(result['conflicts'][0]['gap_minutes'], 30)
        self.assign(candidate, 409)
        self.assign(candidate,409,schedule_confirmed=True,schedule_ack_signature='old')
        self.assign(candidate,409,schedule_confirmed='true',schedule_ack_signature=result['signature'])
        self.assign(candidate,schedule_confirmed=True,schedule_ack_signature=result['signature'])
        with ops.get_db() as conn:
            row = conn.execute('SELECT schedule_ack_signature FROM event_assignments WHERE event_id=?', (candidate,)).fetchone()
            self.assertEqual(row[0],result['signature'])

    def test_schedule_or_location_change_requires_review_of_new_warning(self):
        earlier, candidate = self.event(), self.event(2.5)
        self.assigned(earlier)
        _, preview = self.preview(candidate)
        with ops.get_db() as conn:
            conn.execute("UPDATE events SET location='Bekasi' WHERE id=?", (earlier,))
        rejected = self.assign(candidate, 409, schedule_confirmed=True,schedule_ack_signature=preview['signature'])
        self.assertNotEqual(preview['signature'], rejected['availability']['signature'])
        self.assign(candidate,schedule_confirmed=True,schedule_ack_signature=rejected['availability']['signature'])

    def test_same_day_large_gap_still_warns_and_next_day_does_not(self):
        earlier, candidate = self.event(), self.event(10)
        self.assigned(earlier)
        result=self.preview(candidate)[1]
        self.assertEqual(result['severity'],'yellow');self.assertEqual(result['conflicts'][0]['gap_minutes'],480)
        self.assign(candidate,schedule_confirmed=True,schedule_ack_signature=result['signature'])
        self.assertEqual(self.preview(self.event(24))[1]['conflicts'], [])

    def test_cancelled_absent_and_duplicate_roles(self):
        earlier, candidate = self.event(), self.event(1)
        a = self.assigned(earlier, 'crew'); b = self.assigned(earlier, 'pic')
        self.assertEqual(len(self.preview(candidate)[1]['conflicts']), 1)
        with ops.get_db() as conn:
            conn.execute("UPDATE attendance SET status='absent' WHERE assignment_id IN (?,?)", (a, b))
        self.assertEqual(self.preview(candidate)[1]['conflicts'], [])
        with ops.get_db() as conn:
            conn.execute("UPDATE attendance SET status='not_started' WHERE assignment_id IN (?,?)", (a, b))
            conn.execute("UPDATE events SET status='cancelled' WHERE id=?", (earlier,))
        self.assertEqual(self.preview(candidate)[1]['conflicts'], [])

    def test_acl_and_loading_travel_options_no_longer_control_assignment(self):
        event = self.event()
        crew = Client(self.base); crew.login('crew@captureit.local')
        self.assertEqual(self.preview(event, client=crew)[0], 403)
        self.assertEqual(crew.request(f'/api/events/{event}/assignment','POST',{'user_id':self.uid,'assignment_type':'crew'})[0],403)
        self.assign(event,travel_minutes=999)

    def test_simultaneous_coordinators_must_confirm_new_warning(self):
        a, b = self.event(), self.event(1)
        second = Client(self.base); second.login('coordinator@captureit.local')
        barrier = threading.Barrier(2)
        def book(event, client):
            barrier.wait()
            return client.request(f'/api/events/{event}/assignment', 'POST', {'user_id': self.uid, 'assignment_type': 'crew'})[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(book, a, self.coordinator), pool.submit(book, b, second)]
            self.assertEqual(sorted(r.result() for r in results), [200, 409])
        with ops.get_db() as conn:
            remaining=next(e for e in (a,b) if not conn.execute('SELECT 1 FROM event_assignments WHERE event_id=?',(e,)).fetchone())
        warning=self.preview(remaining)[1]
        self.assign(remaining,schedule_confirmed=True,schedule_ack_signature=warning['signature'])

    def test_changed_existing_schedule_alerts_and_staff_scope(self):
        start = datetime.now(WIB)+timedelta(days=2)
        a, b, c = self.event(start=start), self.event(start=start+timedelta(hours=4)), self.event(start=start)
        self.assigned(a); self.assigned(b)
        self.assigned(a, uid=self.other_uid); self.assigned(c, uid=self.other_uid)
        with ops.get_db() as conn:
            conn.execute('UPDATE events SET starts_at=? WHERE id=?', ((start+timedelta(hours=1)).isoformat(), b))
        crew = Client(self.base); crew.login('crew@captureit.local')
        detail = json.loads(crew.request(f'/api/events/{a}')[2])
        self.assertEqual([p['user_id'] for p in detail['staff_conflicts']], [self.uid])
        self.assertEqual(detail['staff_conflicts'][0]['conflicts'][0]['event_id'], b)
        notifications = json.loads(crew.request('/api/notifications')[2])
        self.assertTrue(any('staff-conflict' in n['key'] and n['event_id']==a for n in notifications['items']))
        detail = json.loads(self.coordinator.request(f'/api/events/{a}')[2])
        self.assertEqual(len(detail['staff_conflicts']), 2)

    def test_migration_preserves_assignments_and_draft_namespace(self):
        event = self.event(); assignment = self.assigned(event)
        conn = sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
        with ops.get_db() as original:
            original.backup(conn)
        try:
            conn.execute('ALTER TABLE event_assignments DROP COLUMN travel_buffer_minutes')
            conn.execute('ALTER TABLE event_assignments DROP COLUMN travel_ack_signature')
            conn.execute('ALTER TABLE event_assignments DROP COLUMN schedule_ack_signature')
            conn.execute('ALTER TABLE event_assignments DROP COLUMN schedule_acknowledged_at')
            conn.execute('ALTER TABLE event_assignments DROP COLUMN schedule_acknowledged_by')
            conn.execute("DELETE FROM app_settings WHERE setting_key='draft_namespace'")
            ops.seed_reference_data(conn)
            namespace = conn.execute("SELECT setting_value FROM app_settings WHERE setting_key='draft_namespace'").fetchone()[0]
            ops.seed_reference_data(conn)
            self.assertEqual(namespace, conn.execute("SELECT setting_value FROM app_settings WHERE setting_key='draft_namespace'").fetchone()[0])
            self.assertEqual(conn.execute('SELECT travel_buffer_minutes FROM event_assignments WHERE id=?', (assignment,)).fetchone()[0], 60)
            self.assertEqual(conn.execute('SELECT schedule_ack_signature FROM event_assignments WHERE id=?',(assignment,)).fetchone()[0],'')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            conn.close()

    def test_event_ending_exact_midnight_does_not_occupy_next_day(self):
        earlier=self.event(start=self.day.replace(hour=22),hours=2)
        candidate=self.event(start=(self.day+timedelta(days=1)).replace(hour=0),hours=2)
        self.assigned(earlier)
        self.assertEqual(self.preview(candidate)[1]['conflicts'],[]);self.assign(candidate)

    def test_warning_uses_account_id_not_same_display_name(self):
        earlier,candidate=self.event(),self.event(1)
        self.assigned(earlier,uid=self.other_uid)
        with ops.get_db() as conn:
            original=conn.execute('SELECT full_name FROM users WHERE id=?',(self.other_uid,)).fetchone()[0]
            name=conn.execute('SELECT full_name FROM users WHERE id=?',(self.uid,)).fetchone()[0]
            conn.execute('UPDATE users SET full_name=? WHERE id=?',(name,self.other_uid))
        try:self.assertEqual(self.preview(candidate)[1]['conflicts'],[])
        finally:
            with ops.get_db() as conn:conn.execute('UPDATE users SET full_name=? WHERE id=?',(original,self.other_uid))

    def test_non_overlapping_same_day_warning_visible_after_assignment(self):
        start=(datetime.now(WIB)+timedelta(days=3)).replace(hour=8)
        earlier,candidate=self.event(start=start),self.event(start=start.replace(hour=18))
        self.assigned(earlier);self.assigned(candidate)
        detail=json.loads(self.coordinator.request(f'/api/events/{candidate}')[2])
        self.assertEqual(detail['staff_conflicts'][0]['severity'],'yellow')
        notifications=json.loads(self.coordinator.request('/api/notifications')[2])['items']
        notice=next(n for n in notifications if 'staff-conflict' in n['key'] and n['event_id']==candidate)
        self.assertEqual(notice['tone'],'gold')


if __name__ == '__main__':
    unittest.main()
