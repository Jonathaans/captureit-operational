"""Compensation history, effective dates, migration, ACL and payroll snapshots."""
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from pathlib import Path

from test_workflows import Client,ops
from compensation import WIB,append_change,history_payload,migrate_history,salary_at


class CompensationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        ops.DB_PATH=cls.root/'compensation.sqlite3';ops.initialize(seed=True)
        cls.httpd=ops.ThreadingHTTPServer(('127.0.0.1',0),ops.Handler)
        cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.httpd.server_port}';cls.number=0

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close();cls.thread.join();cls.temp.cleanup()

    def client(self,email='admin@captureit.local',password='demo1234'):
        client=Client(self.base);client.login(email,password);return client

    def person(self,role='crew'):
        type(self).number+=1;email=f'history-{self.number}@qa.test'
        ops.add_user(email,'History QA '+str(self.number),role,'HistoryTests!2026')
        with ops.get_db() as conn:uid=conn.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()[0]
        return uid,self.client(email,'HistoryTests!2026')

    def post(self,path,payload,expected=200,client=None):
        status,_,body=(client or self.client()).request(path,'POST',payload)
        self.assertEqual(status,expected,body.decode());return json.loads(body)

    def fee(self,uid,amount,day='2000-01-01',**extra):
        return self.post('/api/rates',{'user_id':uid,'base_fee_rupiah':amount,'effective_from':day,**extra})

    def salary(self,uid,amount,day='2000-01-01',**extra):
        return self.post('/api/inhouse-payroll/salaries',{'entries':[{'user_id':uid,'monthly_salary_rupiah':amount}],
                                                      'effective_from':day,**extra})

    def history(self,uid,kind='fee',client=None):
        status,_,body=(client or self.client()).request(f'/api/compensation-history?kind={kind}&user_id={uid}')
        self.assertEqual(status,200,body.decode());return json.loads(body)

    def test_fee_corrections_append_old_new_delta_reason_and_actor(self):
        uid,_=self.person()
        self.fee(uid,100000,reason='Awal');self.fee(uid,150000,reason='Evaluasi');self.fee(uid,125000,reason='Koreksi')
        result=self.history(uid);rows=result['rows']
        self.assertEqual([r['new_amount_rupiah'] for r in rows],[125000,150000,100000])
        self.assertEqual([r['old_amount_rupiah'] for r in rows],[150000,100000,None])
        self.assertEqual([r['delta_rupiah'] for r in rows],[-25000,50000,None])
        self.assertEqual(rows[0]['reason'],'Koreksi');self.assertTrue(rows[0]['created_by']);self.assertTrue(rows[0]['actor_name'])
        self.assertTrue(all(r['created_at'] for r in rows));self.assertFalse(rows[0]['superseded']);self.assertTrue(rows[1]['superseded'])
        self.fee(uid,125000,reason='Retry request')
        self.assertEqual(len(self.history(uid)['rows']),3)

    def test_effective_dates_future_and_backdated_changes_resolve_consistently(self):
        uid,_=self.person();future=(datetime.now(WIB)+timedelta(days=4)).date().isoformat()
        self.fee(uid,100000);self.fee(uid,200000,future);self.fee(uid,50000,'1999-01-01')
        result=self.history(uid);self.assertEqual(result['current']['new_amount_rupiah'],100000)
        bootstrap=json.loads(self.client().request('/api/bootstrap')[2])
        self.assertEqual(next(r for r in bootstrap['rates'] if r['user_id']==uid)['base_fee_rupiah'],100000)
        self.assertEqual(result['rows'][0]['effective_from'],'1999-01-01')
        self.assertEqual(result['rows'][1]['new_amount_rupiah'],200000)

    def test_salary_period_start_and_future_rate_do_not_rewrite_paid_snapshot(self):
        uid,worker=self.person('inhouse_employee')
        self.salary(uid,5000000,'2040-01-01');self.salary(uid,6000000,'2040-02-15',reason='Naik gaji')
        with ops.get_db() as conn:
            self.assertEqual(salary_at(conn,uid,'2040-02-01'),5000000)
            self.assertEqual(salary_at(conn,uid,'2040-03-01'),6000000)
        period={'start':'2040-02-01','end':'2040-02-28','pay_date':'2040-03-25','entries':[]}
        payroll=self.post('/api/inhouse-payroll',period)
        row=next(r for r in payroll['rows'] if r['user_id']==uid)
        self.assertEqual(row['monthly_salary_rupiah'],5000000)
        self.post(f"/api/inhouse-payroll/payout/{row['payout_id']}/transfer",{'transfer_reference':'QA-SNAPSHOT'})
        self.salary(uid,7000000,'2040-01-01',reason='Koreksi berlaku surut')
        self.post('/api/inhouse-payroll',period)
        slip=json.loads(worker.request('/api/inhouse-payroll/slips')[2])['slips'][0]
        self.assertEqual(slip['monthly_salary_rupiah'],5000000)
        history=self.history(uid,'salary');self.assertEqual(len(history['rows']),3)
        self.assertIsNone(history['current'])

    def test_saved_draft_does_not_change_until_explicit_payroll_save(self):
        uid,_=self.person('inhouse_employee');self.salary(uid,4000000,'2041-01-01')
        period={'start':'2041-01-01','end':'2041-01-31','pay_date':'2041-02-25','entries':[]}
        self.post('/api/inhouse-payroll',period);self.salary(uid,4500000,'2041-01-01')
        data=json.loads(self.client().request('/api/inhouse-payroll?start=2041-01-01&end=2041-01-31')[2])
        self.assertEqual(next(r for r in data['rows'] if r['user_id']==uid)['monthly_salary_rupiah'],4000000)
        data=self.post('/api/inhouse-payroll',period)
        self.assertEqual(next(r for r in data['rows'] if r['user_id']==uid)['monthly_salary_rupiah'],4500000)

    def test_new_assignment_fee_snapshot_and_zero_remain_fixed_after_correction(self):
        uid,_=self.person();coordinator=self.client('coordinator@captureit.local')
        with ops.get_db() as conn:
            event=conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at) VALUES('HISTORY-SNAPSHOT','Fee QA','2045-01-01T09:00:00+07:00','2045-01-01T15:00:00+07:00')").lastrowid
        self.post(f'/api/events/{event}/assignment',{'user_id':uid,'assignment_type':'crew'},client=coordinator)
        self.fee(uid,200000,'2045-01-01')
        with ops.get_db() as conn:
            self.assertEqual(conn.execute('SELECT base_fee_snapshot_rupiah FROM event_assignments WHERE event_id=?',(event,)).fetchone()[0],0)
        # Subsequent assignments snapshot the newly effective rate.
        with ops.get_db() as conn:
            later=conn.execute("INSERT INTO events(project_code,title,starts_at,ends_at) VALUES('HISTORY-SNAPSHOT-LATER','Fee QA','2045-01-02T09:00:00+07:00','2045-01-02T15:00:00+07:00')").lastrowid
        self.post(f'/api/events/{later}/assignment',{'user_id':uid,'assignment_type':'crew'},client=coordinator)
        self.fee(uid,300000,'2045-01-01')
        with ops.get_db() as conn:
            self.assertEqual(conn.execute('SELECT base_fee_snapshot_rupiah FROM event_assignments WHERE event_id=?',(later,)).fetchone()[0],200000)

    def test_salary_history_acl_does_not_leak_to_coordinator_or_admin_finance(self):
        uid,_=self.person('inhouse_employee');self.salary(uid,6500000)
        for email in ('coordinator@captureit.local','crew@captureit.local','admin.finance@captureit.local','head.ops@captureit.local'):
            client=self.client(email)
            self.assertEqual(client.request(f'/api/compensation-history?kind=salary&user_id={uid}')[0],403)
        self.assertEqual(len(self.history(uid,'salary',self.client('finance@captureit.local'))['rows']),1)
        crew,_=self.person();self.fee(crew,250000)
        self.assertEqual(self.client('finance@captureit.local').request(f'/api/compensation-history?kind=fee&user_id={crew}')[0],403)
        self.assertEqual(len(self.history(crew,'fee',self.client('head.ops@captureit.local'))['rows']),1)
        self.assertEqual(Client(self.base).request(f'/api/compensation-history?kind=fee&user_id={crew}')[0],401)
        self.assertEqual(self.client().request('/api/compensation-history?kind=fee&user_id=99999999999999999999999999999')[0],400)
        self.assertEqual(self.client().request(f'/api/compensation-history?kind=fee&user_id={crew}&before=99999999999999999999999999')[0],400)

    def test_bad_date_reason_duplicate_account_and_partial_batch_rollback(self):
        uid,_=self.person('inhouse_employee');self.salary(uid,1000000)
        base={'entries':[{'user_id':uid,'monthly_salary_rupiah':2000000}],'effective_from':'2000-01-01'}
        for value in ('2040-02-30','20400101',True,123):
            self.post('/api/inhouse-payroll/salaries',{**base,'effective_from':value},400)
        for reason in (None,True,'x'*1001):self.post('/api/inhouse-payroll/salaries',{**base,'reason':reason},400)
        self.post('/api/inhouse-payroll/salaries',{**base,'entries':base['entries']*2},400)
        self.post('/api/inhouse-payroll/salaries',{**base,'entries':base['entries']+[{'user_id':9999999,'monthly_salary_rupiah':1}]},400)
        self.assertEqual(len(self.history(uid,'salary')['rows']),1)
        with ops.get_db() as conn:self.assertEqual(salary_at(conn,uid,'2040-01-01'),1000000)

    def test_immutable_history_database_guards_and_reset_keeps_history(self):
        uid,_=self.person();self.fee(uid,100000)
        with ops.get_db() as conn:
            with self.assertRaises(sqlite3.IntegrityError):conn.execute('UPDATE compensation_history SET new_amount_rupiah=1 WHERE user_id=?',(uid,))
            with self.assertRaises(sqlite3.IntegrityError):conn.execute('DELETE FROM compensation_history WHERE user_id=?',(uid,))
        import reset_operational_data as reset
        self.assertIn('compensation_history',reset.TABLES_TO_KEEP);self.assertNotIn('compensation_history',reset.TABLES_TO_CLEAR)

    def test_migration_is_idempotent_and_does_not_invent_salary_effective_date(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        with ops.get_db() as source:source.backup(conn)
        try:
            conn.execute('DROP TABLE compensation_history')
            conn.execute("DELETE FROM schema_migrations WHERE version='compensation_history_v1'")
            conn.executescript((ops.ROOT/'schema.sql').read_text())
            person=conn.execute("SELECT id FROM users WHERE email='finance@captureit.local'").fetchone()[0]
            conn.execute('INSERT OR REPLACE INTO inhouse_salary_rates(user_id,monthly_salary_rupiah) VALUES(?,7500000)',(person,))
            migrate_history(conn);count=conn.execute('SELECT COUNT(*) FROM compensation_history').fetchone()[0]
            migrate_history(conn);self.assertEqual(count,conn.execute('SELECT COUNT(*) FROM compensation_history').fetchone()[0])
            row=conn.execute("SELECT * FROM compensation_history WHERE user_id=? AND kind='salary'",(person,)).fetchone()
            self.assertIsNone(row['effective_from']);self.assertIsNone(row['old_amount_rupiah']);self.assertEqual(row['source'],'migration')
            self.assertEqual(salary_at(conn,person,'1990-01-01'),7500000)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        finally:conn.close()

    def test_concurrent_corrections_capture_actual_previous_amount(self):
        uid,_=self.person();self.fee(uid,100000)
        first,second=self.client(),self.client();barrier=threading.Barrier(2)
        def change(client,amount):
            barrier.wait()
            return client.request('/api/rates','POST',{'user_id':uid,'base_fee_rupiah':amount,'effective_from':'2000-01-01'})[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(change,first,150000);b=pool.submit(change,second,200000)
            self.assertEqual((a.result(),b.result()),(200,200))
        rows=list(reversed(self.history(uid)['rows']))
        self.assertEqual(len(rows),3)
        for previous,current in zip(rows,rows[1:]):self.assertEqual(current['old_amount_rupiah'],previous['new_amount_rupiah'])

    def test_history_pagination_has_no_duplicates_or_missing_records(self):
        uid,_=self.person()
        with ops.get_db() as conn:
            actor=dict(conn.execute("SELECT id,full_name FROM users WHERE email='admin@captureit.local'").fetchone())
            for amount in range(105):append_change(conn,uid,'fee',amount,'2000-01-01','QA page',actor)
        first=self.history(uid);self.assertEqual(len(first['rows']),100);self.assertTrue(first['next_cursor'])
        status,_,body=self.client().request(f"/api/compensation-history?kind=fee&user_id={uid}&before={first['next_cursor']}")
        self.assertEqual(status,200);second=json.loads(body);self.assertEqual(len(second['rows']),5);self.assertIsNone(second['next_cursor'])
        self.assertEqual(len({r['id'] for r in first['rows']+second['rows']}),105)

    def test_actor_name_snapshot_survives_rename_and_history_survives_deactivation(self):
        uid,_=self.person();actor_id,actor=self.person('head_operations')
        self.post('/api/rates',{'user_id':uid,'base_fee_rupiah':50000,'effective_from':'2000-01-01'},client=actor)
        original=self.history(uid)['rows'][0]['actor_name']
        with ops.get_db() as conn:conn.execute("UPDATE users SET full_name='Renamed actor' WHERE id=?",(actor_id,))
        self.post(f'/api/users/{uid}/update',{'action':'active','active':False})
        row=self.history(uid)['rows'][0];self.assertEqual(row['actor_name'],original);self.assertEqual(row['created_by'],actor_id)


if __name__=='__main__':unittest.main()
