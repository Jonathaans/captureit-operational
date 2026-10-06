"""HTTP recipient/ACL tests and real SQLite outbox tests; FCM transport is simulated."""
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from test_workflows import Client, ops
import notification_center as notices
import push_delivery


class NotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        ops.DB_PATH=cls.root/'seed.sqlite3';ops.initialize(seed=True)
        ops.add_user('notice-inhouse@qa.test','Notification Employee','inhouse_employee','Notifications!2026')
        ops.add_user('notice-other@qa.test','Other Crew','crew','Notifications!2026')
        source=ops.get_db();snapshot=sqlite3.connect(cls.root/'baseline.sqlite3');source.backup(snapshot);snapshot.close();source.close()
        cls.httpd=ops.ThreadingHTTPServer(('127.0.0.1',0),ops.Handler)
        cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.httpd.server_port}'
        cls.counter=0

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close();cls.thread.join();cls.temp.cleanup()

    def setUp(self):
        type(self).counter+=1;ops.DB_PATH=self.root/f'test-{self.counter}.sqlite3'
        source=sqlite3.connect(self.root/'baseline.sqlite3');target=sqlite3.connect(ops.DB_PATH);source.backup(target);target.close();source.close()
        self.admin=self.client('admin@captureit.local')
        self.crew=self.client('crew@captureit.local')
        self.finance=self.client('finance@captureit.local')
        self.coord=self.client('coordinator@captureit.local')
        with ops.get_db() as conn:
            self.ids={r['email']:r['id'] for r in conn.execute('SELECT id,email FROM users')}

    def client(self,email):
        c=Client(self.base);c.login(email,'Notifications!2026' if email.endswith('@qa.test') else 'demo1234');return c

    def post(self,c,path,data,expected=200):
        status,_,body=c.request(path,'POST',data);self.assertEqual(status,expected,body.decode());return json.loads(body)

    def get(self,c,path,expected=200):
        status,_,body=c.request(path);self.assertEqual(status,expected,body.decode());return json.loads(body)

    def event(self,day='2090-03-12'):
        with ops.get_db() as conn:
            eid=conn.execute('INSERT INTO events(project_code,title,starts_at,ends_at,location,coordinator_id,google_event_id) VALUES(?,?,?,?,?,?,?)',
                (f'NOTICE-{self.counter}', 'Event QA',day+'T10:00:00+07:00',day+'T16:00:00+07:00','Jakarta',self.ids['coordinator@captureit.local'],f'google-{self.counter}')).lastrowid
            task=conn.execute('INSERT INTO design_tasks(event_id) VALUES(?)',(eid,)).lastrowid
        return eid,task

    def assign(self,eid,uid=None):
        return self.post(self.coord,f'/api/events/{eid}/assignment',{'user_id':uid or self.ids['crew@captureit.local'],'assignment_type':'crew'})['assignment_id']

    def user(self,client):
        token=next(c.value for c in client.jar if c.name=='ops_session');digest=hashlib.sha256(token.encode()).hexdigest()
        with ops.get_db() as conn:
            uid=conn.execute('SELECT user_id FROM sessions WHERE token_hash=?',(digest,)).fetchone()[0]
            return {**ops.notification_user(conn,uid),'session_hash':digest}

    def register(self,client=None,key='a',token=None):
        client=client or self.crew
        with patch.object(push_delivery,'config',return_value={'ready':True}):
            return self.post(client,'/api/push/register',{'token':token or key*80,'device_key':key*64,'label':'QA device','platform':'web'})['device_id']

    def queue_notice(self,client=None):
        user=self.user(client or self.crew)
        with ops.get_db() as conn:
            rows=notices.emit(conn,[user['id']],key='queue-test',category='operations',kind='push_test',title='Tes',body='Pesan privat',target_kind='inbox')
            return rows[0] if rows else None

    def drain(self,sender,**kwargs):
        return push_delivery.process_once(ops.get_db,ops.notification_user,ops.live_notifications,sender=sender,**kwargs)

    def test_assignment_is_persisted_and_removed_staff_receives_private_notice(self):
        eid,_=self.event();aid=self.assign(eid)
        items=self.get(self.crew,'/api/notifications')['items'];new=[n for n in items if n['event_id']==eid]
        self.assertEqual(len(new),1);self.assertEqual(new[0]['title'],'Penugasan event baru')
        other=self.client('notice-other@qa.test');public=new[0]['key'].split(':')[1]
        self.get(other,'/api/notifications/item?notice='+public,403)
        self.post(self.crew,'/api/notifications/read',{'keys':[new[0]['key']]})
        self.assertTrue(self.get(self.crew,'/api/notifications/item?notice='+public)['item']['read'])
        self.post(self.coord,f'/api/events/{eid}/assignment',{'action':'remove','assignment_id':aid})
        remaining=[n for n in self.get(self.crew,'/api/notifications')['items'] if n['event_id']==eid]
        self.assertEqual(len(remaining),1);self.assertEqual(remaining[0]['target_kind'],'inbox')
        self.get(self.crew,f'/api/events/{eid}',403)

    def test_invalid_assignment_rolls_back_notifications_and_queue(self):
        eid,_=self.event();self.register()
        self.post(self.coord,f'/api/events/{eid}/assignment',{'user_id':self.ids['crew@captureit.local'],'assignment_type':'crew','skill_ids':[999999]},400)
        with ops.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM notifications WHERE event_id=?',(eid,)).fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM push_deliveries').fetchone()[0],0)

    def test_inhouse_release_recipient_without_event_permission_and_no_draft_notice(self):
        person=self.client('notice-inhouse@qa.test');uid=self.ids['notice-inhouse@qa.test'];self.register(person)
        self.post(self.finance,'/api/inhouse-payroll/salaries',{'entries':[{'user_id':uid,'monthly_salary_rupiah':7654321}],'effective_from':'2090-01-01'})
        payroll=self.post(self.finance,'/api/inhouse-payroll',{'start':'2090-01-01','end':'2090-01-31','pay_date':'2090-02-25','entries':[]})
        row=next(r for r in payroll['rows'] if r['user_id']==uid)
        self.assertEqual(self.get(person,'/api/notifications')['items'],[])
        self.post(self.finance,f"/api/inhouse-payroll/payout/{row['payout_id']}/transfer",{'transfer_reference':'PRIVATE-BANK-REF'})
        item=self.get(person,'/api/notifications')['items'][0]
        self.assertEqual(item['target_id'],row['payout_id']);self.assertEqual(item['category'],'payroll')
        self.assertNotIn('7654321',json.dumps(item));self.assertNotIn('PRIVATE-BANK-REF',json.dumps(item))
        self.get(self.crew,'/api/notifications/item?notice='+item['key'].split(':')[1],403)
        self.post(self.finance,f"/api/inhouse-payroll/payout/{row['payout_id']}/transfer",{},400)
        self.assertEqual(len(self.get(person,'/api/notifications')['items']),1)
        safe=self.get(person,'/api/push/context?notice='+item['key'].split(':')[1]);self.assertNotIn('2090',json.dumps(safe))

    def test_freelancer_release_once_per_account_in_batch(self):
        eid,_=self.event();aid=self.assign(eid)
        with ops.get_db() as conn:
            bid=conn.execute("INSERT INTO payroll_batches(period,status) VALUES('2090-03-12','exported')").lastrowid
            conn.execute('INSERT INTO payroll_lines(batch_id,assignment_id,total_rupiah) VALUES(?,?,?)',(bid,aid,123456))
        self.post(self.finance,f'/api/payroll/batch/{bid}/transfer',{'pay_date':'2090-03-13'})
        rows=[n for n in self.get(self.crew,'/api/notifications')['items'] if n.get('category')=='payroll']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['target_id'],bid)
        self.assertFalse(any(n.get('category')=='payroll' for n in self.get(self.client('notice-other@qa.test'),'/api/notifications')['items']))
        self.post(self.finance,f'/api/payroll/batch/{bid}/transfer',{},400)

    def test_brief_assignee_revision_optimistic_concurrency_and_noop(self):
        eid,task=self.event();designer=self.client('design@captureit.local')
        data={'brief_text':'Tema biru, referensi internal','assignee_id':self.ids['design@captureit.local'],'version':0}
        self.post(self.coord,f'/api/design/{task}/brief',data)
        self.post(self.coord,f'/api/design/{task}/brief',data)  # Network retry is idempotent.
        rows=[n for n in self.get(designer,'/api/notifications')['items'] if n['event_id']==eid]
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['tab'],'design')
        self.assertFalse(any(n['event_id']==eid for n in self.get(self.crew,'/api/notifications')['items']))
        self.post(self.coord,f'/api/design/{task}/brief',{**data,'brief_text':'Stale changed'},409)
        self.post(self.crew,f'/api/design/{task}/brief',data,403)
        self.post(self.coord,f'/api/design/{task}/brief',{**data,'version':1,'assignee_id':self.ids['crew@captureit.local']},400)
        self.post(designer,f'/api/design/{task}/status',{'status':'revision','note':'Logo perlu diperbesar'})
        self.post(designer,f'/api/design/{task}/status',{'status':'revision','note':'Logo perlu diperbesar'})
        self.assertEqual(len([n for n in self.get(designer,'/api/notifications')['items'] if n['event_id']==eid]),2)
        self.post(designer,f'/api/design/{task}/status',{'status':'approved','final_url':'javascript:alert(1)'},400)
        self.post(designer,f'/api/design/{task}/status',{'status':'approved','final_url':'https://example.test/design'})
        self.assertTrue(any(n['title']=='Desain disetujui' for n in self.get(self.coord,'/api/notifications')['items']))

    def test_calendar_tombstone_preserves_schedule_and_notifies_only_affected(self):
        eid,_=self.event();self.assign(eid)
        class Reply:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(self.data).encode()
        env={'GOOGLE_CLIENT_ID':'qa','GOOGLE_CLIENT_SECRET':'qa','GOOGLE_REFRESH_TOKEN':'qa','GOOGLE_CALENDAR_ID':'qa'}
        for _ in range(2):
            with patch.dict(os.environ,env),patch.object(ops,'urlopen',side_effect=[Reply({'access_token':'qa'}),Reply({'items':[{'id':f'google-{self.counter}','status':'cancelled'}]})]),ops.get_db() as conn:
                self.assertTrue(ops.google_sync(conn)['ok'])
        with ops.get_db() as conn:
            event=conn.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
            self.assertEqual(event['starts_at'],'2090-03-12T10:00:00+07:00');self.assertEqual(event['title'],'Event QA')
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notifications WHERE kind='event_cancelled' AND user_id=?",(self.ids['crew@captureit.local'],)).fetchone()[0],1)

    def test_dedupe_and_queue_are_atomic(self):
        self.register();user=self.user(self.crew)
        with self.assertRaises(RuntimeError),ops.get_db() as conn:
            notices.emit(conn,[user['id']],key='rollback',category='operations',kind='push_test',title='QA',body='QA',target_kind='inbox')
            raise RuntimeError('Rollback transaction')
        with ops.get_db() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM push_deliveries').fetchone()[0],0)
        self.queue_notice();self.queue_notice()
        with ops.get_db() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM push_deliveries').fetchone()[0],1)

    def test_retry_eventually_succeeds_and_sanitizes_error(self):
        self.register();self.queue_notice();now=datetime.now(timezone.utc)+timedelta(seconds=1)
        def failing(device,n):raise TimeoutError('SENSITIVE TOKEN must never be persisted')
        self.drain(failing,now=now)
        with ops.get_db() as conn:
            row=conn.execute('SELECT * FROM push_deliveries').fetchone();self.assertEqual(row['status'],'pending');self.assertEqual(row['last_error'],'TimeoutError');self.assertEqual(row['attempts'],1)
        calls=[];self.drain(lambda d,n:calls.append(n['id']) or 'accepted-by-provider',now=now+timedelta(minutes=2))
        self.drain(lambda d,n:self.fail('Already sent must not resend'),now=now+timedelta(minutes=3))
        self.assertEqual(len(calls),1)

    def test_parallel_workers_lease_one_delivery(self):
        self.register();self.queue_notice();calls=[];lock=threading.Lock()
        def sender(d,n):
            with lock:calls.append(n['id'])
            return 'accepted'
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:self.drain(sender),range(2)))
        self.assertEqual(len(calls),1)

    def test_expired_lease_recovers_after_worker_restart(self):
        self.register();self.queue_notice()
        with ops.get_db() as conn:conn.execute("UPDATE push_deliveries SET status='sending',lease_until='2000-01-01T00:00:00+00:00',attempts=1")
        self.assertEqual(self.drain(lambda d,n:'recovered'),1)
        with ops.get_db() as conn:self.assertEqual(conn.execute('SELECT attempts FROM push_deliveries').fetchone()[0],2)

    def test_unregistered_token_is_disabled_without_losing_inbox(self):
        self.register();self.queue_notice()
        class UnregisteredError(Exception):pass
        def invalid(d,n):raise UnregisteredError('gone')
        self.drain(invalid)
        with ops.get_db() as conn:
            self.assertEqual(conn.execute('SELECT active FROM push_devices').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT status FROM push_deliveries').fetchone()[0],'failed')
        self.assertTrue(any(n['title']=='Tes' for n in self.get(self.crew,'/api/notifications')['items']))

    def test_logout_and_rebinding_cancel_old_account_deliveries(self):
        device=self.register();self.queue_notice()
        other=self.client('notice-other@qa.test');self.register(other)
        self.assertEqual(self.drain(lambda d,n:self.fail('Wrong account')),0)
        self.queue_notice(other);self.post(other,'/api/logout',{})
        self.assertEqual(self.drain(lambda d,n:self.fail('Logged out')),0)
        self.post(self.crew,'/api/push/unregister',{'device_id':device},403)

    def test_session_expiry_and_access_revocation_cancel_push(self):
        self.register();eid,_=self.event();self.assign(eid)
        with ops.get_db() as conn:conn.execute('DELETE FROM user_roles WHERE user_id=?',(self.ids['crew@captureit.local'],))
        self.assertEqual(self.drain(lambda d,n:self.fail('Revoked role')),0)
        with ops.get_db() as conn:conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
        self.get(self.crew,'/api/notifications/settings',401)

    def test_preferences_and_quiet_hours_preserve_inbox(self):
        self.register();eid,_=self.event();self.assign(eid)
        prefs={'categories':{k:True for k in notices.CATEGORIES},'quiet_start':'22:00','quiet_end':'07:00'}
        self.post(self.crew,'/api/notifications/preferences',prefs)
        # Test time functions directly; worker uses the actual occurrence timestamp.
        deferred=notices.quiet_until(prefs,datetime(2090,1,1,17,tzinfo=timezone.utc))
        self.assertEqual(datetime.fromisoformat(deferred).astimezone(notices.WIB).strftime('%H:%M'),'07:00')
        self.assertIsNone(notices.quiet_until(prefs,datetime(2090,1,1,3,tzinfo=timezone.utc)))
        prefs['categories']['schedule']=False;self.post(self.crew,'/api/notifications/preferences',prefs)
        self.assertEqual(self.drain(lambda d,n:self.fail('Opted out')),0)
        self.assertTrue(any(n['event_id']==eid for n in self.get(self.crew,'/api/notifications')['items']))
        self.post(self.crew,'/api/notifications/preferences',{**prefs,'quiet_end':'22:00'},400)

    def test_read_expired_and_resolved_reminders_are_not_sent(self):
        self.register();nid=self.queue_notice()
        with ops.get_db() as conn:conn.execute("UPDATE notifications SET read_at='2026-01-01' WHERE id=?",(nid,))
        self.assertEqual(self.drain(lambda d,n:self.fail('Read')),0)
        with ops.get_db() as conn:
            notices.emit(conn,[self.ids['crew@captureit.local']],key='stale',category='reminders',kind='push_test',title='Stale',body='x',target_kind='inbox',live_key='not-current')
        self.assertEqual(self.drain(lambda d,n:self.fail('Resolved')),0)

    def test_pagination_read_all_and_device_data_privacy(self):
        self.register();uid=self.ids['crew@captureit.local']
        with ops.get_db() as conn:
            for i in range(106):notices.emit(conn,[uid],key=f'page-{i}',category='operations',kind='push_test',title=f'Row {i}',body='x',target_kind='inbox',push=False)
        first=self.get(self.crew,'/api/notifications');next_page=self.get(self.crew,'/api/notifications?before='+str(first['next_before']))
        durable1=[n for n in first['items'] if n['key'].startswith('notice:')]
        durable2=[n for n in next_page['items'] if n['key'].startswith('notice:')]
        self.assertEqual(len(durable1)+len(durable2),106);self.assertFalse(set(n['key'] for n in durable1)&set(n['key'] for n in durable2))
        self.post(self.crew,'/api/notifications/read',{'all':True})
        self.assertEqual(self.get(self.crew,'/api/notifications')['unread_count'],0)
        settings=json.dumps(self.get(self.crew,'/api/notifications/settings'))
        self.assertNotIn('a'*80,settings);self.assertNotIn('session_hash',settings);self.assertNotIn('device_key',settings)
        self.get(self.crew,'/api/notifications?before='+str(2**70),400)

    def test_disabled_config_anonymous_and_csrf_are_explicit(self):
        with patch.dict(os.environ,{'PUSH_ENABLED':'false'}):
            self.assertFalse(self.get(self.crew,'/api/push/config')['ready'])
            self.post(self.crew,'/api/push/register',{'token':'x'*80,'device_key':'a'*64},400)
        anonymous=Client(self.base)
        self.get(anonymous,'/api/notifications/settings',401)
        self.post(anonymous,'/api/push/register',{},403)
        with ops.get_db() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM push_devices').fetchone()[0],0)

    def test_initialization_does_not_repeat_backlog_push(self):
        self.register()
        with ops.get_db() as conn:before=conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]
        ops.initialize(seed=True);ops.initialize(seed=True)
        with ops.get_db() as conn:
            self.assertEqual(before,conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM push_deliveries').fetchone()[0],0)

    def test_quiet_worker_defers_normal_but_allows_urgent_schedule(self):
        self.register();eid,_=self.event();self.assign(eid)
        now=datetime.now(timezone.utc)+timedelta(seconds=1);local=now.astimezone(notices.WIB)
        prefs={'categories':{k:True for k in notices.CATEGORIES},'quiet_start':(local-timedelta(hours=1)).strftime('%H:%M'),'quiet_end':(local+timedelta(hours=1)).strftime('%H:%M')}
        self.post(self.crew,'/api/notifications/preferences',prefs)
        self.assertEqual(self.drain(lambda d,n:self.fail('Quiet'),now=now),0)
        with ops.get_db() as conn:
            before=dict(conn.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone())
            conn.execute("UPDATE events SET location='Bekasi' WHERE id=?",(eid,))
            after=dict(conn.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone());notices.event_changed(conn,before,after)
        calls=[];self.drain(lambda d,n:calls.append(n['kind']) or 'accepted',now=now+timedelta(seconds=1))
        self.assertEqual(calls,['event_changed'])

    def test_retry_budget_and_expiry_stop_repeated_sends(self):
        self.register();self.queue_notice();now=datetime.now(timezone.utc)+timedelta(seconds=1)
        def failing(d,n):raise TimeoutError('QA')
        for i in range(7):self.drain(failing,now=now+timedelta(hours=i*2))
        with ops.get_db() as conn:
            row=conn.execute('SELECT status,attempts FROM push_deliveries').fetchone()
            self.assertEqual(tuple(row),('failed',6))
            nid=notices.emit(conn,[self.ids['crew@captureit.local']],key='expired',category='operations',kind='push_test',title='Expired',body='x',target_kind='inbox')[0]
            conn.execute("UPDATE notifications SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(nid,))
        self.assertEqual(self.drain(lambda d,n:self.fail('Expired')),0)

    def test_h1_reminder_once_and_cancelled_after_event_finishes(self):
        other=self.client('notice-other@qa.test');uid=self.ids['notice-other@qa.test'];self.register(other)
        tomorrow=(datetime.now(notices.WIB)+timedelta(days=1)).date().isoformat();eid,_=self.event(tomorrow);self.assign(eid,uid)
        assignment=[n for n in self.get(other,'/api/notifications')['items'] if n['title']=='Penugasan event baru']
        self.post(other,'/api/notifications/read',{'keys':[n['key'] for n in assignment]})
        with ops.get_db() as conn:
            user=ops.notification_user(conn,uid)
            for _ in range(2):notices.sync_live(conn,user,ops.live_notifications(conn,user))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND kind='event-tomorrow'",(uid,)).fetchone()[0],1)
            conn.execute("UPDATE events SET status='completed' WHERE id=?",(eid,))
        self.assertEqual(self.drain(lambda d,n:self.fail('Finished event')),0)

    def test_push_registration_requires_csrf_and_cannot_revoke_other_device(self):
        import urllib.request
        import urllib.error
        request=urllib.request.Request(self.base+'/api/push/register',data=b'{}',headers={'Content-Type':'application/json'},method='POST')
        with self.assertRaises(urllib.error.HTTPError) as error:self.crew.opener.open(request)
        self.assertEqual(error.exception.code,403)
        device=self.register();other=self.client('notice-other@qa.test')
        self.post(other,'/api/push/unregister',{'device_id':device},403)
        with patch.object(push_delivery,'config',return_value={'ready':True}):
            for data in ({'token':'short','device_key':'a'*64},{'token':'x'*80,'device_key':'x'}):
                self.post(self.crew,'/api/push/register',data,400)

    def test_fcm_adapter_only_sends_opaque_reference_and_generic_text(self):
        import sys
        from types import SimpleNamespace
        messages=[]
        construct=lambda **kwargs:kwargs
        messaging=SimpleNamespace(Message=construct,WebpushConfig=construct,Notification=construct,
            AndroidConfig=construct,AndroidNotification=construct,send=lambda value,app:messages.append(value) or 'accepted')
        sdk=SimpleNamespace(credentials=SimpleNamespace(),messaging=messaging)
        notification={'public_id':'a'*32,'expires_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
            'urgent':1,'title':'PRIVATE EVENT','body':'Salary 7654321, private bank details'}
        with patch.dict(sys.modules,{'firebase_admin':sdk}),patch.object(push_delivery,'_firebase_app',object()),patch.object(push_delivery,'config',return_value={'origin':'https://ops.example.test'}):
            for platform in ('web','android'):push_delivery.send_fcm({'token':'device-token','platform':platform},notification)
        self.assertNotIn('notification',messages[0]);self.assertEqual(set(messages[0]['data']),{'notice','url'})
        self.assertEqual(messages[1]['android']['notification']['tag'],'a'*32)
        self.assertNotIn('7654321',str(messages));self.assertNotIn('PRIVATE EVENT',str(messages))


if __name__=='__main__':unittest.main()
