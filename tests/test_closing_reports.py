import base64
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from test_workflows import Client, ops
from test_operations_features import read_xlsx
from closing_reports import validate_report
from operations import WIB, EXPORT_HEADERS

# Valid one-pixel PNG; served only through the authenticated photo endpoint.
PHOTO = 'data:image/png;base64,' + base64.b64encode(base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')).decode()


class ClosingReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        ops.DB_PATH = Path(cls.temp.name) / 'closing.sqlite3'
        ops.initialize(seed=True)
        cls.httpd = ops.ThreadingHTTPServer(('127.0.0.1', 0), ops.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(); cls.temp.cleanup()

    def setUp(self):
        self.coordinator = self.client('coordinator@captureit.local')
        # A global Crew account can report when assigned as this event's PIC.
        self.pic = self.client('crew@captureit.local')
        end = datetime.now(WIB) - timedelta(hours=2)
        start = end - timedelta(hours=6)
        self.data = {'actual_start': start.isoformat(), 'actual_end': end.isoformat(), 'service_result': 'planned',
                     'printing_mode': 'print', 'prints_total': 160, 'prints_failed': 10,
                     'ribbon_rolls': [{'start': 60, 'end': 0}, {'start': 400, 'end': 300}],
                     'issues': 'Printer macet', 'resolution': 'Ganti ribbon', 'follow_up': 'PIC cek printer besok'}
        with ops.get_db() as conn:
            self.owner = conn.execute("SELECT id FROM users WHERE email='coordinator@captureit.local'").fetchone()[0]
            self.pic_id = conn.execute("SELECT id FROM users WHERE email='crew@captureit.local'").fetchone()[0]
            self.event_id = conn.execute('''INSERT INTO events(project_code,title,starts_at,ends_at,coordinator_id)
                VALUES(?,?,?,?,?)''', (self.id(), self.id(), start.isoformat(), end.isoformat(), self.owner)).lastrowid
            self.assignment_id = conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type) VALUES(?,?,'pic')",
                                               (self.event_id, self.pic_id)).lastrowid
            conn.execute('INSERT INTO attendance(assignment_id) VALUES(?)', (self.assignment_id,))
        self.path = f'/api/events/{self.event_id}/closing'

    def client(self, email):
        client = Client(self.base); client.login(email); return client

    def post(self, client, payload, expected=200, path=None):
        status, _, body = client.request(path or self.path, 'POST', payload)
        self.assertEqual(status, expected, body.decode())
        return json.loads(body)

    def detail(self, client=None):
        status, _, body = (client or self.pic).request(f'/api/events/{self.event_id}')
        self.assertEqual(status, 200, body.decode())
        return json.loads(body)

    def submit(self, version=0, photos=None, action='submit'):
        return self.post(self.pic, {'action': action, 'version': version, 'data': self.data, 'photos': photos or []})

    def test_all_materials_and_print_counts_optional_in_submitted_report(self):
        self.data={key:self.data[key] for key in ('actual_start','actual_end','service_result')}
        self.data['ribbon_rolls']=[{'start':'','end':''}]
        self.submit()
        report=self.detail()['closing_report']['data']
        self.assertEqual(report['ribbon_rolls'],[]);self.assertIsNone(report['ribbon_used'])
        self.assertIsNone(report['prints_total']);self.assertTrue(all(q is None for q in report['materials'].values()))
        self.post(self.coordinator,{'action':'accept','version':1})
        day=self.data['actual_start'][:10]
        rows=read_xlsx(self.coordinator.request(f'/api/events/export?start={day}&end={day}')[2])[0]
        row=next(row for row in rows if row[0]==self.id())
        self.assertEqual(row[17:20],[None,None,None]);self.assertNotIn('None',row[20])

    def test_material_quantities_zero_and_export_notes(self):
        self.data['materials']={'frame':150,'lenticular':'','magnet':0,'keychain':20}
        self.submit();self.post(self.coordinator,{'action':'accept','version':1})
        materials=self.detail()['closing_report']['data']['materials']
        self.assertEqual(materials,{'frame':150,'lenticular':None,'magnet':0,'keychain':20})
        day=self.data['actual_start'][:10]
        rows=read_xlsx(self.coordinator.request(f'/api/events/export?start={day}&end={day}')[2])[0]
        row=next(row for row in rows if row[0]==self.id())
        for note in ('Frame: 150 pcs.','Magnet: 0 pcs.','Keychain: 20 pcs.'):
            self.assertIn(note,row[20])
        self.assertNotIn('Lensa lenticular:',row[20]);self.assertEqual(len(row),21)

    def test_invalid_material_quantities_and_legacy_ribbon_prefill(self):
        for materials in ({'frame':-1},{'magnet':1.5},{'lenticular':True},{'keychain':'abc'},{'unknown':4}):
            with self.subTest(materials=materials),self.assertRaises(ValueError):
                validate_report({**self.data,'materials':materials},True)
        with self.assertRaises(ValueError):
            validate_report({**self.data,'ribbon_rolls':[{'start':50,'end':''}]},True)
        self.post(self.coordinator,{'ribbon_start':400,'ribbon_end':102},400,path=f'/api/events/{self.event_id}/logistics')
        self.post(self.coordinator,{'notes':'Legacy'},path=f'/api/events/{self.event_id}/logistics')
        with ops.get_db() as conn:
            conn.execute('UPDATE event_operations SET ribbon_start=400,ribbon_end=102 WHERE event_id=?',(self.event_id,))
        self.assertEqual(self.detail()['closing_report']['data']['ribbon_rolls'],[{'start':400,'end':102}])

    def test_draft_revision_accept_clear_and_locked_content(self):
        self.post(self.coordinator, {'action': 'clear'}, 400, f'/api/events/{self.event_id}/performance')
        self.post(self.pic, {'action': 'draft', 'version': 0, 'data': {}, 'photos': []})
        self.assertEqual(self.detail()['closing_report']['status'], 'draft')
        self.submit(1)
        self.post(self.pic, {'action': 'draft', 'version': 2, 'data': {}}, 400)
        self.post(self.coordinator, {'action': 'revise', 'version': 2, 'note': ''}, 400)
        self.post(self.coordinator, {'action': 'revise', 'version': 2, 'note': 'Lengkapi penanganan'})
        self.assertTrue(self.detail()['closing_report']['can_edit'])
        self.submit(3, action='draft')
        self.assertEqual(self.detail()['closing_report']['status'], 'revision')
        self.submit(4)
        self.post(self.coordinator, {'action': 'accept', 'version': 5, 'note': 'Sudah sesuai'})
        report = self.detail(self.coordinator)['closing_report']
        self.assertTrue(report['can_clear'])
        self.assertEqual(report['data']['ribbon_used'], 160)
        self.assertEqual(len(report['history']), 6)
        self.post(self.pic, {'action': 'draft', 'version': 6, 'data': self.data}, 400)
        self.post(self.coordinator, {'action': 'remove', 'assignment_id': self.assignment_id}, 400, f'/api/events/{self.event_id}/assignment')
        self.post(self.coordinator, {'action': 'clear'}, path=f'/api/events/{self.event_id}/performance')
        self.assertEqual(self.detail()['status'], 'completed')
        self.post(self.coordinator, {'action': 'revise', 'version': 6, 'note': 'Again'}, 400)

    def test_role_scope_assigned_pic_and_owner_coordinator(self):
        unrelated_pic = self.client('pic@captureit.local')
        self.post(unrelated_pic, {'action': 'draft', 'version': 0, 'data': {}}, 403)
        self.post(self.coordinator, {'action': 'draft', 'version': 0, 'data': {}}, 403)
        self.submit()
        self.post(self.pic, {'action': 'accept', 'version': 1}, 403)
        admin = self.client('admin@captureit.local')
        with ops.get_db() as conn:
            conn.execute('UPDATE events SET coordinator_id=NULL WHERE id=?', (self.event_id,))
        self.post(self.coordinator, {'action': 'accept', 'version': 1}, 403)
        self.post(admin, {'action': 'accept', 'version': 1})
        self.post(self.coordinator, {'action': 'clear'}, 403, f'/api/events/{self.event_id}/performance')
        self.post(admin, {'action': 'clear'}, path=f'/api/events/{self.event_id}/performance')

    def test_photo_privacy_rollback_and_removal(self):
        self.submit(photos=[{'kind': 'setup', 'image': PHOTO}], action='draft')
        report = self.detail()['closing_report']; photo = report['photos'][0]
        status, headers, content = self.pic.request(photo['url'])
        self.assertEqual(status, 200); self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(content, base64.b64decode(PHOTO.split(',')[1]))
        self.assertEqual(Client(self.base).request(photo['url'])[0], 401)
        self.assertEqual(self.client('naya@captureit.local').request(photo['url'])[0], 403)
        self.assertEqual(self.coordinator.request(photo['url'])[0], 200)
        bad = {'action': 'draft', 'version': 1, 'data': self.data, 'photos': [{'kind': 'event', 'image': 'data:image/png;base64,AA=='}]}
        self.post(self.pic, bad, 400)
        self.assertEqual(self.detail()['closing_report']['version'], 1)
        self.assertEqual(self.pic.request(photo['url'])[0], 200)
        self.submit(1, photos=[{'id': photo['id']}], action='draft')
        self.submit(2, action='draft')
        self.assertEqual(self.pic.request(photo['url'])[0], 404)

    def test_photo_cannot_be_borrowed_from_another_event(self):
        self.submit(photos=[{'kind': 'event', 'image': PHOTO}], action='draft')
        photo_id = self.detail()['closing_report']['photos'][0]['id']
        with ops.get_db() as conn:
            other = conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at) VALUES('other-photo','Other',?,?)",
                                 (self.data['actual_start'], self.data['actual_end'])).lastrowid
            conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type) VALUES(?,?,'pic')", (other, self.pic_id))
        self.post(self.pic, {'action': 'draft', 'version': 0, 'data': {}, 'photos': [{'id': photo_id}]}, 400,
                  f'/api/events/{other}/closing')
        with ops.get_db() as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM event_closing_reports WHERE event_id=?', (other,)).fetchone())

    def test_stale_versions_do_not_overwrite_reports_or_reviews(self):
        self.submit(action='draft')
        self.post(self.pic, {'action': 'draft', 'version': 0, 'data': {}}, 400)
        self.submit(1)
        self.post(self.coordinator, {'action': 'revise', 'version': 1, 'note': 'stale'}, 400)
        self.assertEqual(self.detail()['closing_report']['status'], 'submitted')
        self.post(self.coordinator, {'action': 'revise', 'version': 2, 'note': 'correct'})
        self.post(self.coordinator, {'action': 'accept', 'version': 2}, 400)
        self.assertEqual(self.detail()['closing_report']['review_note'], 'correct')

    def test_validation_digital_zero_and_overnight(self):
        for changes in [{'prints_failed': 161}, {'prints_total': -1}, {'prints_total': True}, {'ribbon_rolls': [{'start': 1, 'end': 2}]},
                        {'service_result': 'changed'}, {'resolution': ''}, {'actual_end': '2000-01-01T00:00:00+07:00'},
                        {'actual_end': (datetime.now(WIB)+timedelta(days=1)).isoformat()}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_report({**self.data, **changes}, True)
        overnight = {**self.data, 'actual_start': '2026-09-01T23:00:00+07:00', 'actual_end': '2026-09-02T01:00:00+07:00'}
        self.assertEqual(validate_report(overnight, True)['ribbon_used'], 160)
        data = validate_report({**self.data, 'prints_total': 0, 'prints_failed': 0, 'ribbon_rolls': [{'start': 0, 'end': 0}]}, True)
        self.assertEqual(data['ribbon_used'], 0)
        digital = {**self.data, 'printing_mode': 'digital', 'prints_total': 0, 'prints_failed': 0, 'ribbon_rolls': []}
        self.data = digital; self.submit()
        self.post(self.coordinator, {'action': 'accept', 'version': 1})
        self.assertIsNone(self.detail()['closing_report']['data']['ribbon_used'])

    def test_export_uses_only_accepted_report_and_sums_rolls(self):
        self.post(self.coordinator, {'notes': 'Catatan awal', 'client_phone': '0812345'},
                  path=f'/api/events/{self.event_id}/logistics')
        with ops.get_db() as conn:
            # Historical data remains available while ribbon entry moves to Closing.
            conn.execute('UPDATE event_operations SET ribbon_start=50,ribbon_end=20 WHERE event_id=?',(self.event_id,))
        day = self.data['actual_start'][:10]
        def exported():
            status, _, body = self.coordinator.request(f'/api/events/export?start={day}&end={day}')
            self.assertEqual(status, 200)
            rows, sheet, ns = read_xlsx(body)
            self.assertEqual(rows[0], EXPORT_HEADERS)
            row_index, row = next((i, row) for i, row in enumerate(rows, 1) if row[0] == self.id())
            return row, sheet.find(f"m:sheetData/m:row[@r='{row_index}']/m:c[@r='T{row_index}']/m:f", ns).text
        self.submit()
        self.assertEqual(exported()[0][17:20], [50, 20, 30])
        self.post(self.coordinator, {'action': 'accept', 'version': 1})
        row, formula = exported()
        self.assertEqual(row[17:20], [460, 300, 160]); self.assertEqual(row[2], '0812345')
        self.assertRegex(formula, r'R\d+-S\d+')
        self.assertIn('roll 2: 400 → 300', row[20]); self.assertIn('Catatan awal', row[20])
        self.assertIn('PIC cek printer besok', row[20])

    def test_notifications_track_review_revision_and_clear(self):
        def items(client):
            status, _, body = client.request('/api/notifications'); self.assertEqual(status, 200)
            return [n for n in json.loads(body)['items'] if n['event_id'] == self.event_id and n['tab'] == 'closing']
        self.assertTrue(any('Lengkapi' in n['title'] for n in items(self.pic)))
        self.submit(); self.assertTrue(any('review' in n['title'] for n in items(self.coordinator)))
        self.post(self.coordinator, {'action': 'revise', 'version': 1, 'note': 'Rincian'})
        self.assertTrue(any('Revisi' in n['title'] for n in items(self.pic)))
        self.assertFalse(any('review' in n['title'] for n in items(self.coordinator)))
        self.submit(2); self.post(self.coordinator, {'action': 'accept', 'version': 3})
        self.assertTrue(any('siap clear' in n['title'] for n in items(self.coordinator)))

    def test_migration_repeated_and_cascade_private_photos(self):
        self.submit(photos=[{'kind': 'setup', 'image': PHOTO}], action='draft')
        with ops.get_db() as conn:
            ops.seed_reference_data(conn); ops.seed_reference_data(conn)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('SELECT version FROM event_closing_reports WHERE event_id=?', (self.event_id,)).fetchone()[0], 1)
            conn.execute('DELETE FROM events WHERE id=?', (self.event_id,))
            for table in ['event_closing_reports', 'event_closing_photos', 'event_closing_history']:
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table} WHERE event_id=?', (self.event_id,)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
