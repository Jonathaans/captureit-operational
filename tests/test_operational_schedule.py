import io
import json
import os
from datetime import datetime, timedelta
from unittest.mock import patch
from test_design_attendance import Base
from test_workflows import ops
from operations import WIB
import operational_schedule as schedule


class OperationalScheduleTests(Base):
    def setUp(self):
        self.coordinator = self.login('coordinator@captureit.local')
        self.pic_id,self.pic = self.person('pic_event','PIC Schedule')
        self.crew_id,self.crew = self.person('crew','Crew Schedule')
        end=datetime.now(WIB)-timedelta(hours=1)
        self.starts,self.ends=(end-timedelta(hours=6)).isoformat(),end.isoformat()
        self.event=self.json(self.coordinator,'/api/events/manual','POST',{'title':'Schedule regression','starts_at':self.starts,'ends_at':self.ends})['event_id']
        with ops.get_db() as conn:
            for uid,kind in ((self.pic_id,'pic'),(self.crew_id,'crew')):
                assignment=conn.execute('INSERT INTO event_assignments(event_id,user_id,assignment_type) VALUES(?,?,?)',(self.event,uid,kind)).lastrowid
                conn.execute('INSERT INTO attendance(assignment_id) VALUES(?)',(assignment,))
                if kind=='crew':self.assignment=assignment

    def report(self,client=None,expected=200,**changes):
        return self.json(client or self.pic,f'/api/events/{self.event}/closing','POST',{'action':'submit','version':0,'data':{'actual_start':self.starts,'actual_end':self.ends,'service_result':'planned'},'photos':[],**changes},expected)

    def test_pic_submits_after_end_without_clear(self):
        detail=self.json(self.pic,f'/api/events/{self.event}')
        self.assertEqual(detail['status'],'scheduled');self.assertTrue(detail['closing_report']['can_submit'])
        self.report();self.assertEqual(self.json(self.pic,f'/api/events/{self.event}')['closing_report']['status'],'submitted')

    def test_early_report_can_be_draft_but_not_submitted(self):
        with ops.get_db() as conn:conn.execute('UPDATE events SET ends_at=? WHERE id=?',((datetime.now(WIB)+timedelta(hours=1)).isoformat(),self.event))
        self.report(expected=400);self.report(action='draft')

    def test_completed_legacy_event_can_recover_missing_report(self):
        with ops.get_db() as conn:conn.execute("UPDATE events SET status='completed' WHERE id=?",(self.event,))
        self.report();self.report(self.coordinator,action='accept',version=1);self.report(expected=400,version=2)

    def test_finish_saves_reviews_and_clears_in_one_transaction(self):
        self.report();self.report(self.coordinator,action='accept',version=1)
        with ops.get_db() as conn:
            conn.execute("UPDATE attendance SET status='checked_out',check_in_at=?,check_out_at=? WHERE assignment_id IN (SELECT id FROM event_assignments WHERE event_id=?)",(self.starts,self.ends,self.event))
            ids=[r[0] for r in conn.execute('SELECT id FROM event_assignments WHERE event_id=?',(self.event,))]
        reviews=[{'assignment_id':aid,'scores':{code:4 for code,_ in ops.PERFORMANCE_CRITERIA}} for aid in ids]
        self.json(self.coordinator,f'/api/events/{self.event}/performance','POST',{'reviews':reviews})
        self.assertEqual(self.json(self.pic,f'/api/events/{self.event}')['status'],'completed')

    def test_single_day_and_multiday_attendance_lock_removal(self):
        for table in ('attendance','attendance_days'):
            with self.subTest(table=table):
                with ops.get_db() as conn:
                    if table=='attendance':conn.execute("UPDATE attendance SET status='checked_in',check_in_at=? WHERE assignment_id=?",(self.starts,self.assignment))
                    else:
                        conn.execute("UPDATE attendance SET status='not_started',check_in_at=NULL WHERE assignment_id=?",(self.assignment,))
                        conn.execute("INSERT INTO attendance_days(assignment_id,work_date,status,check_in_at) VALUES(?,?,'checked_out',?)",(self.assignment,self.starts[:10],self.starts))
                self.json(self.coordinator,f'/api/events/{self.event}/assignment','POST',{'action':'remove','assignment_id':self.assignment},400)
                detail=self.json(self.coordinator,f'/api/events/{self.event}')
                self.assertTrue(next(a for a in detail['assignments'] if a['assignment_id']==self.assignment)['attendance_locked'])

    def test_unattended_crew_remains_removable(self):
        self.json(self.coordinator,f'/api/events/{self.event}/assignment','POST',{'action':'remove','assignment_id':self.assignment})

    def test_attended_event_cannot_be_deleted_to_bypass_lock(self):
        with ops.get_db() as conn:conn.execute("UPDATE attendance SET status='checked_in' WHERE assignment_id=?",(self.assignment,))
        self.json(self.admin,f'/api/events/{self.event}/delete-manual','POST',{'confirm':True},400)

    def test_schedule_permissions_validation_and_attendance_lock(self):
        path=f'/api/events/{self.event}/schedule';body={'starts_at':'2041-06-01T10:00','ends_at':'2041-06-01T18:00'}
        self.json(self.pic,path,'POST',body,403)
        self.json(self.coordinator,path,'POST',{**body,'ends_at':body['starts_at']},400)
        self.json(self.coordinator,path,'POST',body)
        self.assertEqual(self.json(self.coordinator,f'/api/events/{self.event}')['starts_at'],'2041-06-01T10:00:00+07:00')
        with ops.get_db() as conn:conn.execute("UPDATE attendance SET status='checked_in' WHERE assignment_id=?",(self.assignment,))
        self.json(self.coordinator,path,'POST',body,400)

    def test_sync_uses_dates_and_preserves_manual_schedule(self):
        gid=f'google-regression-{self.event}'
        with ops.get_db() as conn:conn.execute('UPDATE events SET google_event_id=? WHERE id=?',(gid,self.event))
        items=[{'id':gid,'summary':'Changed title','start':{'dateTime':'2041-07-01T03:00:00+07:00'},'end':{'dateTime':'2041-07-01T04:00:00+07:00'}},
               {'id':gid+'-new','summary':'New event','start':{'dateTime':'2041-07-02T03:00:00+07:00'},'end':{'dateTime':'2041-07-02T04:00:00+07:00'}}]
        env={key:'test' for key in ('GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','GOOGLE_REFRESH_TOKEN','GOOGLE_CALENDAR_ID')}
        with patch.dict(os.environ,env),patch.object(ops,'urlopen',side_effect=[io.BytesIO(b'{"access_token":"token"}'),io.BytesIO(json.dumps({'items':items}).encode())]),ops.get_db() as conn:
            self.assertTrue(ops.google_sync(conn)['ok'])
        with ops.get_db() as conn:
            row=conn.execute('SELECT * FROM events WHERE id=?',(self.event,)).fetchone()
            self.assertEqual(row['starts_at'],self.starts);self.assertEqual(row['ends_at'],self.ends)
            intake=conn.execute('SELECT * FROM calendar_code_queue WHERE google_event_id=?',(gid+'-new',)).fetchone()
            self.assertEqual(intake['starts_at'],'2041-07-02T00:00:00+07:00');iid=intake['id']
        result=self.json(self.coordinator,f'/api/calendar-queue/{iid}/promote','POST',{'starts_at':'2041-07-02T12:00','ends_at':'2041-07-02T20:00'})
        self.assertEqual(self.json(self.coordinator,f"/api/events/{result['event_id']}")['starts_at'],'2041-07-02T12:00:00+07:00')

    def test_exclusive_multiday_end_and_idempotent_migration(self):
        self.assertEqual(schedule.calendar_dates('2041-07-01T00:00:00+07:00','2041-07-03T00:00:00+07:00'),('2041-07-01T00:00:00+07:00','2041-07-03T00:00:00+07:00'))
        with ops.get_db() as conn:
            schedule.migrate(conn);schedule.migrate(conn);self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
