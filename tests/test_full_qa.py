"""Release QA: permissions, account lifecycle, input boundaries and deployment data."""
import base64
import csv
import http.client
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
import unittest.mock
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from test_workflows import Client, ops
from operations import WIB
from test_closing_reports import PHOTO
from test_operations_features import read_xlsx


class ReleaseQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        ops.DB_PATH = cls.root/'data'/'qa.sqlite3'
        ops.initialize(seed=True)
        cls.httpd = ops.ThreadingHTTPServer(('127.0.0.1', 0), ops.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'
        cls.counter = 0

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(); cls.temp.cleanup()

    def client(self, email='admin@captureit.local', password='demo1234'):
        c = Client(self.base); c.login(email, password); return c

    def create_person(self, role='crew', name='QA Account'):
        type(self).counter += 1
        email = f'release-qa-{self.counter}@captureit.local'
        status, _, body = self.client().request('/api/users', 'POST', {
            'full_name': name, 'email': email, 'role': role, 'password': 'ReleaseQA!2026'})
        self.assertEqual(status, 200, body.decode())
        return json.loads(body)['id'], email, self.client(email, 'ReleaseQA!2026')

    def mutate(self, client, path, payload, expected=200):
        status, _, body = client.request(path, 'POST', payload)
        self.assertEqual(status, expected, body.decode())
        return json.loads(body)

    def raw(self, path, data=b'{}', headers=None, method='POST'):
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        try:
            conn.request(method, path, body=data, headers=headers or {})
            response=conn.getresponse(); return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_all_14_roles_bootstrap_and_admin_boundaries(self):
        for role, _ in ops.ROLE_LIST:
            with self.subTest(role=role):
                uid, _, client = self.create_person(role)
                status, _, body = client.request('/api/bootstrap')
                self.assertEqual(status, 200, body.decode())
                result=json.loads(body)
                self.assertEqual(result['user']['id'], uid)
                self.assertEqual(result['user']['roles'][0]['code'], role)
                self.assertTrue(result['draft_namespace'])
                for private in ('password_hash', 'token_hash', 'GOOGLE_REFRESH_TOKEN', 'csrf_token'):
                    self.assertNotIn('"'+private+'"', body.decode())
                if role!='administrator':
                    self.assertIsNone(result['configure'])
                    self.assertEqual(client.request('/api/configure/appearance', 'POST', {'colors':{}})[0], 403)
                    self.assertEqual(client.request('/api/users', 'POST', {})[0], 403)
                if role in {'sales_staff','content_team','inhouse_employee'}:
                    self.assertEqual(result['events'], [])

    def test_private_endpoints_reject_anonymous_and_files_are_not_public(self):
        anonymous=Client(self.base)
        for url in ['/api/bootstrap','/api/events/1','/api/notifications','/api/payroll',
                    '/api/inhouse-payroll','/api/profile-file/1/ktp_front','/api/closing-photos/1',
                    '/api/attendance/1/photo/check_in','/api/advance-documents/1/download']:
            with self.subTest(url=url): self.assertEqual(anonymous.request(url)[0], 401)
        for url in ['/server.py','/schema.sql','/.env','/ops.sqlite3','/../server.py','/%2e%2e/server.py']:
            with self.subTest(url=url): self.assertEqual(anonymous.request(url)[0], 404)

    def test_csrf_and_login_input_boundaries(self):
        client=self.client()
        cookie='; '.join(f'{c.name}={c.value}' for c in client.jar)
        for token in ('','wrong'):
            headers={'Content-Type':'application/json','Cookie':cookie,'X-CSRF-Token':token}
            self.assertEqual(self.raw('/api/logout', headers=headers)[0],403)
        self.assertEqual(client.request('/api/me')[0],200)
        for payload in ([], 'invalid', None):
            self.assertEqual(self.raw('/api/login',json.dumps(payload).encode(),{'Content-Type':'application/json'})[0],400)
        for body in (b'{broken',b'\xff\xfe'):
            self.assertEqual(self.raw('/api/login',body,{'Content-Type':'application/json'})[0],400)
        self.assertEqual(client.request('/api/login','POST',{'email':"' OR 1=1 --",'password':'wrong'})[0],401)

    def test_login_rejects_cross_site_simple_post(self):
        status, _, _ = self.raw('/api/login', json.dumps({'email':'crew@captureit.local','password':'demo1234'}).encode(),
                                {'Content-Type':'text/plain','Origin':'https://unrelated.example'})
        self.assertEqual(status,415)

    def test_login_session_is_committed_before_success_response(self):
        client=self.client()
        token=next(c.value for c in client.jar if c.name=='ops_session')
        with ops.get_db() as conn:
            self.assertIsNotNone(conn.execute('SELECT 1 FROM sessions WHERE token_hash=?',
                (ops.hashlib.sha256(token.encode()).hexdigest(),)).fetchone())
        self.assertEqual(client.request('/api/login','POST',{'email':'admin@captureit.local','password':'x'*1025})[0],400)

    def test_role_revocation_denies_old_assignment_closing_and_photo_access(self):
        uid, _, client=self.create_person('crew')
        with ops.get_db() as conn:
            event=conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at) VALUES('QA-REVOKE','Private','2040-03-01T10:00:00+07:00','2040-03-01T18:00:00+07:00')").lastrowid
            conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type) VALUES(?,?,'pic')",(event,uid))
        self.mutate(client,f'/api/events/{event}/closing',{'action':'draft','version':0,'data':{},
            'photos':[{'kind':'setup','caption':'Private','image':PHOTO}]})
        detail=json.loads(client.request(f'/api/events/{event}')[2])
        photo=detail['closing_report']['photos'][0]['url']
        self.assertEqual(client.request(photo)[0],200)
        self.mutate(self.client(),f'/api/users/{uid}/update',{'action':'role','role':'content_team'})
        self.assertEqual(json.loads(client.request('/api/bootstrap')[2])['events'],[])
        self.assertEqual(client.request(f'/api/events/{event}')[0],403)
        self.assertEqual(client.request(photo)[0],403)
        self.mutate(client,f'/api/events/{event}/closing',{'action':'draft','version':1,'data':{}},403)

    def test_all_local_scripts_styles_and_logo_are_served(self):
        import re
        anonymous=Client(self.base)
        status, _, body=anonymous.request('/')
        self.assertEqual(status,200)
        assets=re.findall(r'(?:src|href)="(/[^"?]+)(?:\?[^" ]*)?"',body.decode())
        self.assertGreater(len(assets),5)
        for asset in assets:
            with self.subTest(asset=asset):
                status, _, content=anonymous.request(asset)
                self.assertEqual(status,200);self.assertGreater(len(content),0)

    def test_response_security_headers_and_cookie_flags(self):
        with patch.dict(os.environ,{'COOKIE_SECURE':'1'}):
            status, headers, body=self.raw('/api/login',json.dumps({'email':'crew@captureit.local','password':'demo1234'}).encode(),{'Content-Type':'application/json'})
        self.assertEqual(status,200,body.decode());self.assertIn('Secure',headers['Set-Cookie'])
        _, headers, _=Client(self.base).request('/')
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(headers['X-Frame-Options'],'DENY')
        self.assertEqual(headers['Cache-Control'],'no-store')

    def test_role_change_immediately_updates_employment_and_payroll_access(self):
        uid, _, client=self.create_person('inhouse_employee')
        admin=self.client()
        self.mutate(admin,f'/api/users/{uid}/update',{'action':'role','role':'crew'})
        person=json.loads(client.request('/api/me')[2])['user']
        self.assertEqual(person['employment_type'],'freelancer')
        self.assertEqual(client.request('/api/payroll/slips')[0],200)
        self.mutate(admin,f'/api/users/{uid}/update',{'action':'role','role':'content_team'})
        person=json.loads(client.request('/api/me')[2])['user']
        self.assertEqual(person['employment_type'],'inhouse');self.assertEqual(person['department'],'Content')
        self.assertEqual(client.request('/api/inhouse-payroll/slips')[0],200)

    def test_cli_created_crew_has_correct_employment_immediately(self):
        ops.add_user('qa-cli@captureit.local','QA CLI','crew','ReleaseQA!2026')
        person=json.loads(self.client('qa-cli@captureit.local','ReleaseQA!2026').request('/api/me')[2])['user']
        self.assertEqual(person['employment_type'],'freelancer')

    def test_password_reset_revokes_previous_sessions(self):
        _, email, old=self.create_person()
        ops.reset_user_password(email,'ResetPassword!2026')
        self.assertEqual(old.request('/api/me')[0],401)
        self.assertEqual(self.client(email,'ResetPassword!2026').request('/api/me')[0],200)

    def test_admin_password_reset_revokes_previous_sessions(self):
        uid, email, old = self.create_person()
        result = self.mutate(self.client(), f'/api/users/{uid}/update', {
            'action': 'password', 'password': 'AdminReset!2026', 'password_confirmation': 'AdminReset!2026',
        })
        self.assertTrue(result['ok'])
        self.assertEqual(old.request('/api/me')[0], 401)
        self.assertEqual(self.client(email, 'AdminReset!2026').request('/api/me')[0], 200)

    def test_user_can_change_own_password_and_keep_current_session(self):
        _, email, current = self.create_person()
        other_session = self.client(email, 'ReleaseQA!2026')
        status, _, body = current.request('/api/profile/password', 'POST', {
            'current_password': 'ReleaseQA!2026',
            'new_password': 'SelfChange!2026',
            'password_confirmation': 'SelfChange!2026',
        })
        self.assertEqual(status, 200, body.decode())
        result = json.loads(body)
        self.assertTrue(result['current_session_preserved'])
        self.assertEqual(result['sessions_revoked'], 1)
        self.assertEqual(current.request('/api/me')[0], 200)
        self.assertEqual(other_session.request('/api/me')[0], 401)
        old_login = Client(self.base)
        self.assertEqual(old_login.request('/api/login', 'POST', {'email': email, 'password': 'ReleaseQA!2026'})[0], 401)
        self.assertEqual(self.client(email, 'SelfChange!2026').request('/api/me')[0], 200)

    def test_user_password_change_rejects_wrong_current_password(self):
        _, email, current = self.create_person()
        status, _, body = current.request('/api/profile/password', 'POST', {
            'current_password': 'WrongPassword!2026',
            'new_password': 'SelfChange!2026',
            'password_confirmation': 'SelfChange!2026',
        })
        self.assertEqual(status, 400)
        self.assertIn('saat ini salah', body.decode())
        self.assertEqual(self.client(email, 'ReleaseQA!2026').request('/api/me')[0], 200)

    def test_admin_can_delete_unused_inactive_account(self):
        uid, email, old = self.create_person()
        admin = self.client()
        self.mutate(admin, f'/api/users/{uid}/update', {'action': 'active', 'active': False})
        result = self.mutate(admin, f'/api/users/{uid}/update', {'action': 'delete', 'confirm': 'HAPUS'})
        self.assertEqual(result['action'], 'delete')
        self.assertEqual(old.request('/api/me')[0], 401)
        deleted_login = Client(self.base)
        self.assertEqual(deleted_login.request('/api/login', 'POST', {'email': email, 'password': 'ReleaseQA!2026'})[0], 401)
        with ops.get_db() as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone())
            audit = conn.execute("SELECT details FROM audit_logs WHERE entity_type='user' AND entity_id=? AND action='deleted' ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
            self.assertIsNotNone(audit)

    def test_admin_delete_preserves_accounts_with_operational_history(self):
        uid, _, _ = self.create_person('crew')
        with ops.get_db() as conn:
            event = conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at) VALUES('QA-DELETE','Delete guard','2040-06-01T10:00:00+07:00','2040-06-01T18:00:00+07:00')").lastrowid
            conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type) VALUES(?,?,'crew')", (event, uid))
        admin = self.client()
        self.mutate(admin, f'/api/users/{uid}/update', {'action': 'active', 'active': False})
        status, _, body = admin.request(f'/api/users/{uid}/update', 'POST', {'action': 'delete', 'confirm': 'HAPUS'})
        self.assertEqual(status, 400)
        self.assertIn('penugasan event', body.decode())
        with ops.get_db() as conn:
            self.assertIsNotNone(conn.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone())

    def test_reactivation_does_not_restore_revoked_sessions(self):
        uid, email, old=self.create_person()
        admin=self.client()
        self.mutate(admin,f'/api/users/{uid}/update',{'action':'active','active':False})
        self.assertEqual(old.request('/api/me')[0],401)
        self.mutate(admin,f'/api/users/{uid}/update',{'action':'active','active':True})
        self.assertEqual(old.request('/api/me')[0],401)
        self.assertEqual(self.client(email,'ReleaseQA!2026').request('/api/me')[0],200)

    def test_bootstrap_admin_rejects_weak_password(self):
        previous=ops.DB_PATH
        try:
            ops.DB_PATH=self.root/'weak-bootstrap.sqlite3'
            with patch.dict(os.environ,{'BOOTSTRAP_ADMIN_EMAIL':'new-admin@example.test','BOOTSTRAP_ADMIN_PASSWORD':'123'}):
                with self.assertRaises(ValueError): ops.initialize(seed=False)
            with ops.get_db() as conn: self.assertEqual(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0],0)
        finally:
            ops.DB_PATH=previous

    def test_money_rejects_fractions_booleans_and_large_values(self):
        uid, _, _=self.create_person('crew')
        staff, _, _=self.create_person('inhouse_employee')
        admin=self.client()
        for value in (True,1.5,'1.5',10**30):
            with self.subTest(value=value):
                self.mutate(admin,'/api/rates',{'user_id':uid,'base_fee_rupiah':value,'effective_from':'2026-09-01'},400)
                self.mutate(admin,'/api/skills',{'name':f'QA skill {value}','extra_fee_rupiah':value},400)
                self.mutate(admin,'/api/inhouse-payroll/salaries',{'entries':[{'user_id':staff,'monthly_salary_rupiah':value}]},400)
                self.mutate(admin,'/api/inhouse-payroll',{'start':'2042-06-01','end':'2042-06-30','pay_date':'2042-07-25',
                    'entries':[{'user_id':staff,'allowance_rupiah':value}]},400)

    def test_payroll_rejects_malformed_or_unknown_entries(self):
        base={'start':'2043-06-01','end':'2043-06-30','pay_date':'2043-07-25'}
        for entries in (None,{},'text',[None],[{'user_id':99999999,'allowance_rupiah':25}]):
            with self.subTest(entries=entries): self.mutate(self.client(),'/api/inhouse-payroll',{**base,'entries':entries},400)

    def test_csv_spreadsheet_formulas_are_treated_as_text(self):
        uid, _, _=self.create_person('inhouse_employee','=1+2')
        with ops.get_db() as conn:
            conn.execute("INSERT INTO inhouse_attendance(user_id,work_date,status) VALUES(?,'2040-01-02','checked_out')",(uid,))
        status, _, content=self.client('finance@captureit.local').request('/api/inhouse-attendance/export','POST',
            {'start':'2040-01-02','end':'2040-01-02','format':'csv'})
        self.assertEqual(status,200)
        row=next(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
        self.assertEqual(row['Nama'],"'=1+2")
        crew_id, _, _=self.create_person('crew','=1+2')
        with ops.get_db() as conn:
            event=conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at) VALUES('QA-CSV','=1+2','2040-01-02T10:00:00+07:00','2040-01-02T18:00:00+07:00')").lastrowid
            assignment=conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type) VALUES(?,?,'crew')",(event,crew_id)).lastrowid
            conn.execute("INSERT INTO attendance(assignment_id,status) VALUES(?,'checked_out')",(assignment,))
        status, _, content=self.client('finance@captureit.local').request('/api/payroll/export','POST',
            {'start':'2040-01-02','end':'2040-01-02','format':'csv'})
        self.assertEqual(status,200)
        row=next(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
        self.assertEqual(row['Nama'],"'=1+2");self.assertEqual(row['Event'],"'=1+2")

    def test_staff_cannot_mark_themselves_absent_without_manager_permission(self):
        uid, _, crew=self.create_person()
        with ops.get_db() as conn:
            event=conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at) VALUES('QA-ABSENT','QA','2040-02-01T10:00:00+07:00','2040-02-01T18:00:00+07:00')").lastrowid
            assignment=conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type) VALUES(?,?,'crew')",(event,uid)).lastrowid
            conn.execute('INSERT INTO attendance(assignment_id) VALUES(?)',(assignment,))
        self.mutate(crew,f'/api/events/{event}/attendance',{'assignment_id':assignment,'action':'absent'},403)
        self.mutate(self.client('coordinator@captureit.local'),f'/api/events/{event}/attendance',{'assignment_id':assignment,'action':'absent'})

    def test_dashboard_and_payroll_dates_use_wib(self):
        class Frozen(datetime):
            @classmethod
            def now(cls,tz=None):
                value=cls(2026,9,29,18,tzinfo=timezone.utc)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)
        events=[{'status':'scheduled','starts_at':'2026-09-29T09:00:00+07:00'}]
        with patch.object(ops,'datetime',Frozen):
            self.assertEqual(ops.dashboard_data(None,{'permissions':set()},events)['total_upcoming'],0)
        uid, _, _=self.create_person()
        with ops.get_db() as conn:
            event=conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at) VALUES('QA-WIB','Midnight','2044-06-30T18:30:00Z','2044-06-30T20:00:00Z')").lastrowid
            assignment=conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type) VALUES(?,?,'crew')",(event,uid)).lastrowid
            conn.execute("INSERT INTO attendance(assignment_id,status) VALUES(?,'checked_out')",(assignment,))
            rows=ops.payroll_rows(conn,'2044-07-01','2044-07-01')
        self.assertTrue(any(r['assignment_id']==assignment for r in rows))
        workbook=ops.make_payroll_xlsx(rows,'2044-07-01')
        with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
            sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            self.assertIn('2044-07-01',''.join(sheet.itertext()))
            self.assertNotIn('2044-06-30',''.join(sheet.itertext()))

    def test_integrated_event_to_closing_export_and_paid_payslip(self):
        coordinator=self.client('coordinator@captureit.local');finance=self.client('finance@captureit.local')
        warehouse=self.client('warehouse@captureit.local');uid, _, pic=self.create_person('crew','QA End-to-End PIC')
        with ops.get_db() as conn:
            intake=conn.execute("INSERT INTO calendar_code_queue(google_event_id,title,event_type,starts_at,ends_at,location,is_full_day,status) VALUES('qa-full-flow','QA Full Flow','Corporate','2025-02-03T09:00:00+07:00','2025-02-03T15:00:00+07:00','Jakarta',0,'scheduled')").lastrowid
            position=conn.execute("SELECT id FROM positions WHERE name='PIC Event'").fetchone()[0]
        event=self.mutate(coordinator,f'/api/calendar-queue/{intake}/promote',{'project_code':'QA-FLOW-2025','is_full_day':False})['event_id']
        base=f'/api/events/{event}'
        self.mutate(self.client(),'/api/rates',{'user_id':uid,'base_fee_rupiah':125000,'effective_from':'2025-01-01'})
        self.mutate(coordinator,base+'/assignment',{'user_id':uid,'assignment_type':'pic','position_id':position})
        logistics=self.mutate(coordinator,base+'/logistics',{'transport_modes':['fleet','motorcycles','courier'],
            'vehicle_name':'Luxio QA Full Flow','courier_name':'Lalamove'})['logistics']
        self.assertEqual(logistics['vehicle_name'],'Luxio QA Full Flow')
        self.mutate(coordinator,base+'/group',{'status':'invites_sent','group_link':'https://chat.whatsapp.com/qa-example'})
        self.mutate(pic,base+'/advance',{'action':'submit'})
        for action in ('approve','transfer'):self.mutate(finance,base+'/advance',{'action':action})
        for action in ('start','ready','dispatch'):self.mutate(warehouse,base+'/warehouse',{'action':action})
        detail=json.loads(pic.request(base)[2]);assignment=detail['assignments'][0]['assignment_id']
        camera='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8\xff\xe0synthetic-camera-qa\xff\xd9').decode()
        with ops.get_db() as conn:   # crew may only check in on the event date: pretend today is that date
            event_day=ops.eventdays.event_dates(conn.execute('SELECT starts_at,ends_at FROM events WHERE id=?',(event,)).fetchone())[0]
        patcher=unittest.mock.patch.object(ops.eventdays,'today_wib',lambda:event_day);patcher.start();self.addCleanup(patcher.stop)
        for action in ('check_in','check_out'):
            self.mutate(pic,base+'/attendance',{'action':action,'assignment_id':assignment,'photo':camera,
                'location':{'latitude':-6.2,'longitude':106.8,'accuracy_m':10,'captured_at':ops.now_iso()}})
        data={'actual_start':'2025-02-03T09:00:00+07:00','actual_end':'2025-02-03T15:00:00+07:00',
            'service_result':'planned','ribbon_rolls':[{'start':400,'end':280}],'materials':{'frame':115,'magnet':0}}
        self.mutate(pic,base+'/closing',{'action':'submit','version':0,'data':data,'photos':[{'kind':'event','image':PHOTO}]})
        self.mutate(coordinator,base+'/closing',{'action':'accept','version':1})
        self.mutate(warehouse,base+'/warehouse',{'action':'returned'})
        self.mutate(coordinator,base+'/performance',{'action':'clear'})
        self.mutate(coordinator,base+'/performance',{'reviews':[{'assignment_id':assignment,
            'scores':{'work_quality':5,'punctuality':5,'teamwork':5,'sop_equipment':5},'note':'QA'}]})
        detail=json.loads(pic.request(base)[2]);self.assertEqual(detail['status'],'completed')
        self.assertEqual(detail['closing_report']['data']['ribbon_used'],120)
        self.assertEqual(detail['assignments'][0]['performance_review']['score_percent'],100)
        status, _, content=coordinator.request('/api/events/export?start=2025-02-03&end=2025-02-03')
        self.assertEqual(status,200);row=next(r for r in read_xlsx(content)[0] if r[0]=='QA Full Flow')
        self.assertEqual(len(row),21);self.assertIn('Frame: 115 pcs.',row[20]);self.assertIn('Lalamove',' '.join(str(v) for v in row))
        status, _, content=finance.request('/api/payroll/export','POST',{'start':'2025-02-03','end':'2025-02-03','format':'csv'})
        self.assertEqual(status,200);rows=list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['Fee Dasar (Rp)'],'125000')
        batch=json.loads(finance.request('/api/payroll?start=2025-02-03&end=2025-02-03')[2])['batch']
        self.mutate(finance,f"/api/payroll/batch/{batch['id']}/transfer",{'pay_date':'2025-02-04','transfer_reference':'QA-RECORDED-ONLY'})
        slips=json.loads(pic.request('/api/payroll/slips?year=2025')[2])['slips']
        self.assertEqual(len(slips),1);self.assertEqual(slips[0]['transfer_reference'],'QA-RECORDED-ONLY')
        self.mutate(finance,f"/api/payroll/batch/{batch['id']}/transfer",{'pay_date':'2025-02-04','transfer_reference':'DUPLICATE'},400)

    def test_backup_restore_preserves_relations_sessions_and_private_files(self):
        uid, _, client=self.create_person()
        private=ops.store_profile_file(uid,'photo',b'QA-PRIVATE-PHOTO','png')
        with ops.get_db() as conn:
            conn.execute('INSERT OR REPLACE INTO user_profiles(user_id,profile_photo_path,profile_photo_mime) VALUES(?,?,?)',(uid,private,'image/png'))
        original=ops.DB_PATH;restored=self.root/'restored';restored.mkdir()
        with ops.get_db() as source,sqlite3.connect(restored/'qa.sqlite3') as backup:
            source.backup(backup)
            count=source.execute('SELECT COUNT(*) FROM event_assignments').fetchone()[0]
        shutil.copytree(ops.profile_storage_dir(),restored/'private-profile-uploads')
        try:
            ops.DB_PATH=restored/'qa.sqlite3'
            ops.initialize(seed=False)
            with ops.get_db() as conn:
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_assignments').fetchone()[0],count)
            self.assertEqual(client.request('/api/me')[0],200)
            self.assertEqual(client.request(f'/api/profile-file/{uid}/photo')[2],b'QA-PRIVATE-PHOTO')
        finally:
            ops.DB_PATH=original

    def test_concurrent_bootstrap_reads_remain_valid(self):
        client=self.client('coordinator@captureit.local')
        cookie='; '.join(f'{c.name}={c.value}' for c in client.jar)
        def read(_):
            status,_,body=self.raw('/api/bootstrap',data=None,headers={'Cookie':cookie},method='GET')
            self.assertEqual(status,200);self.assertIn('events',json.loads(body))
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(read,range(30)))


if __name__=='__main__': unittest.main()
