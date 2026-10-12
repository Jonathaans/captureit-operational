"""HTTP regression tests for shift dates, persisted schedules, and role boundaries."""
import csv
import io
import json
import unittest
from datetime import datetime

from test_design_attendance import Base, PHOTO, fixed_clock
from test_workflows import Client, ops


class ShiftTests(Base):
    def setUp(self):
        self.uid, self.staff = self.person('inhouse_employee', 'Shift Tester')
        with ops.get_db() as conn:
            conn.execute("DELETE FROM app_settings WHERE setting_key LIKE 'inhouse_%'")

    def schedule(self, client=None, expected=200, **changes):
        data = dict(user_id=self.uid, start='2041-05-12', end='2041-05-12', shift_name='Malam',
                    work_start='22:00', work_end='06:00', late_grace_minutes=10, early_grace_minutes=5)
        data.update(changes)
        return self.json(client or self.admin, '/api/inhouse-shifts', 'POST', data, expected)

    def at(self, value):
        return fixed_clock(datetime.fromisoformat(value + '+07:00'))

    def punch(self, action, at, expected=200, client=None, **changes):
        body = {'action':action, 'photo':PHOTO, 'location':{'latitude':-6.2, 'longitude':106.8,
                'accuracy_m':20, 'captured_at':ops.now_iso()}, **changes}
        with self.at(at):
            return self.json(client or self.staff, '/api/inhouse-attendance', 'POST', body, expected)

    def test_overnight_checkout_uses_previous_work_date_and_unlocks_next_shift(self):
        self.schedule()
        first = self.punch('check_in','2041-05-12T22:05:00',work_date='2041-05-12')
        self.assertEqual(first['late_minutes'],5); self.assertEqual(first['punctuality'],'')
        with self.at('2041-05-13T05:30:00'):
            listing = self.json(self.staff,'/api/inhouse-attendance')
        self.assertEqual(listing['own_today']['status'],'checked_in')
        self.assertEqual(listing['own_today']['work_date'],'2041-05-12')
        self.assertTrue(listing['own_schedule']['overnight'])
        self.punch('check_in','2041-05-13T05:30:00',expected=400)
        self.punch('check_out','2041-05-13T05:30:00',expected=400,work_date='2041-05-13')
        out = self.punch('check_out','2041-05-13T05:30:00',work_date='2041-05-12')
        self.assertEqual(out['early_leave_minutes'],30); self.assertEqual(out['work_date'],'2041-05-12')
        self.punch('check_out','2041-05-13T05:31:00',expected=400)
        self.punch('check_in','2041-05-13T09:00:00',work_date='2041-05-13')
        with ops.get_db() as conn:
            rows = conn.execute('SELECT work_date,status FROM inhouse_attendance WHERE user_id=? ORDER BY work_date',(self.uid,)).fetchall()
        self.assertEqual([tuple(r) for r in rows],[('2041-05-12','checked_out'),('2041-05-13','checked_in')])

    def test_late_after_midnight_checkin_and_date_tampering(self):
        self.schedule()
        with self.at('2041-05-13T01:00:00'):
            data = self.json(self.staff,'/api/inhouse-attendance')
        self.assertEqual([s['work_date'] for s in data['checkin_options']],['2041-05-12','2041-05-13'])
        self.punch('check_in','2041-05-13T01:00:00',expected=400,work_date='2041-05-10')
        self.punch('check_in','2041-05-13T01:00:00',expected=400,work_date='2041-05-14')
        result = self.punch('check_in','2041-05-13T01:00:00',work_date='2041-05-12')
        self.assertEqual(result['late_minutes'],180)
        self.punch('check_out','2041-05-13T06:00:00')
        self.punch('check_in','2041-05-13T07:00:00',expected=400,work_date='2041-05-12')

    def test_schedule_and_grace_are_frozen_at_checkin_including_export(self):
        self.schedule()
        self.punch('check_in','2041-05-12T22:05:00')
        self.schedule(work_end='07:00',expected=400)
        self.schedule(reset=True,expected=400)
        self.json(self.admin,'/api/settings/inhouse-schedule','POST',{'work_start':'08:00','work_end':'17:00',
            'late_grace_minutes':0,'early_grace_minutes':0})
        out = self.punch('check_out','2041-05-13T05:57:00')
        self.assertEqual(out['early_leave_minutes'],3); self.assertEqual(out['punctuality'],'')
        status,_,raw = self.admin.request('/api/inhouse-attendance/export','POST',{'start':'2041-05-12','end':'2041-05-12','format':'csv'})
        self.assertEqual(status,200)
        email = self.json(self.staff,'/api/me')['user']['email']
        row = next(r for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))) if r['Email']==email)
        self.assertEqual(row['Shift'],'Malam'); self.assertEqual(row['Jadwal pulang'],'06:00')
        self.assertEqual(row['Keterangan'],'Tepat waktu'); self.assertEqual(row['Jadwal selesai (WIB)'],'2041-05-13 06:00:00')
        self.assertEqual(row['Check-in (WIB)'],'2041-05-12 22:05:00')

    def test_default_schedule_also_snapshots_checkout_time(self):
        self.punch('check_in','2041-05-12T09:00:00')
        self.json(self.admin,'/api/settings/inhouse-schedule','POST',{'work_start':'10:00','work_end':'20:00'})
        out = self.punch('check_out','2041-05-12T18:00:00')
        self.assertEqual(out['early_leave_minutes'],0); self.assertEqual(out['scheduled'],'18:00')

    def test_role_matrix_attendance_self_read_and_shift_write(self):
        for role,_ in ops.ROLE_LIST:
            with self.subTest(role=role):
                uid,client = self.person(role,'Role '+role)
                is_inhouse = role not in {'crew','pic_event'}
                data = self.json(client,'/api/inhouse-attendance',expected=200 if is_inhouse else 403)
                can_schedule = role in {'administrator','head_operations'}
                if is_inhouse:
                    self.assertEqual(data['can_schedule'],can_schedule)
                    self.assertEqual(data['is_manager'],role in {'administrator','head_operations','head_finance','finance','admin_finance'})
                    if not data['is_manager']: self.assertEqual(data['records'],[])
                    if not can_schedule: self.assertEqual(data['shift_roster'],[])
                self.schedule(client,expected=200 if can_schedule else 403)
                self.punch('check_in','2041-05-12T09:00:00',client=client,expected=200 if is_inhouse else 403)
                self.punch('check_out','2041-05-12T18:00:00',client=client,expected=200 if is_inhouse else 403)
                if is_inhouse:
                    self.punch('check_in','2041-05-12T19:00:00',client=client,expected=400)

    def test_cannot_submit_as_other_employee_or_read_their_photo(self):
        other_id,other = self.person('content_team','Other employee')
        self.punch('check_in','2041-05-12T09:00:00',user_id=other_id)
        with ops.get_db() as conn:
            self.assertIsNone(conn.execute('SELECT id FROM inhouse_attendance WHERE user_id=?',(other_id,)).fetchone())
            record_id = conn.execute('SELECT id FROM inhouse_attendance WHERE user_id=?',(self.uid,)).fetchone()[0]
        self.json(other,f'/api/inhouse-attendance/{record_id}/photo/check_in',expected=403)
        self.assertEqual(self.staff.request(f'/api/inhouse-attendance/{record_id}/photo/check_in')[0],200)
        self.json(self.staff,'/api/inhouse-attendance/export','POST',{'start':'2041-05-12','end':'2041-05-12'},403)
        self.assertIn(Client(self.base).request('/api/inhouse-shifts','POST',{})[0],(401,403))

    def test_range_validation_reset_and_overlap_are_atomic(self):
        self.schedule(end='2041-05-14')
        self.schedule(start='2041-05-13',end='2041-05-13',work_start='05:00',work_end='14:00',expected=400)
        self.schedule(start='2041-05-13',end='2041-05-12',expected=400)
        self.schedule(start='2041-05-12',end='2042-05-12',expected=400)
        self.schedule(work_end='22:00',expected=400)
        self.schedule(user_id=True,expected=400)
        self.schedule(start='bad',expected=400)
        self.schedule(start='2000-01-01',end='2000-01-01',expected=400)
        crew_id,_ = self.person('crew','Crew no office shift')
        self.schedule(user_id=crew_id,expected=400)
        self.schedule(start='2041-05-13',end='2041-05-14',reset=True)
        with ops.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM inhouse_shifts WHERE user_id=?',(self.uid,)).fetchone()[0],1)

    def test_custom_acl_and_role_change_take_effect_in_existing_session(self):
        uid,client = self.person('warehouse_staff','Scheduler')
        self.schedule(client,expected=403)
        perms = ['attendance.inhouse.schedule']
        self.json(self.admin,'/api/configure/roles','POST',{'role':'warehouse_staff','permissions':perms})
        self.schedule(client)
        listing = self.json(client,'/api/inhouse-attendance')
        self.assertTrue(listing['can_schedule']); self.assertFalse(listing['is_manager']); self.assertIsNone(listing['own_today'])
        self.json(self.admin,'/api/configure/roles','POST',{'role':'warehouse_staff','reset_default':True})
        self.schedule(client,expected=403)
        self.json(self.admin,f'/api/users/{uid}/update','POST',{'action':'role','role':'crew'})
        self.json(client,'/api/inhouse-attendance',expected=403)

    def test_migration_preserves_legacy_rows_and_is_repeatable(self):
        with ops.get_db() as conn:
            conn.execute("INSERT INTO inhouse_attendance(user_id,work_date,status,check_in_at) VALUES(?,'2040-01-01','checked_in','2040-01-01T02:00:00Z')",(self.uid,))
            for _ in range(2): ops.shifts.migrate(conn)
        self.punch('check_out','2040-01-01T18:00:00')
        with ops.get_db() as conn:
            row = conn.execute('SELECT * FROM inhouse_attendance WHERE user_id=?',(self.uid,)).fetchone()
        self.assertEqual(row['work_date'],'2040-01-01'); self.assertEqual(row['status'],'checked_out')
        self.assertIsNone(row['late_minutes'])


if __name__ == '__main__': unittest.main()
