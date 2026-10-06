import csv
import io
import json
import tempfile
import threading
import unittest
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from test_workflows import Client, ops
from operations import EXPORT_HEADERS, WIB


def read_xlsx(content):
    ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        strings=[''.join(node.itertext()) for node in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        rows=[]
        for row in sheet.findall('m:sheetData/m:row',ns):
            values=[]
            for cell in row:
                value=cell.find('m:v',ns)
                text=cell.find('m:is',ns)
                if cell.get('t')=='s': result=strings[int(value.text)]
                elif text is not None: result=''.join(text.itertext())
                elif value is not None: result=float(value.text)
                else: result=None
                values.append(result)
            rows.append(values)
        return rows,sheet,ns


class OperationsFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        ops.DB_PATH=Path(cls.temp.name)/'ops.sqlite3'
        ops.initialize(seed=True)
        cls.httpd=ops.ThreadingHTTPServer(('127.0.0.1',0),ops.Handler)
        cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(); cls.temp.cleanup()

    def client(self,email='coordinator@captureit.local'):
        client=Client(self.base); client.login(email); return client

    def post(self,client,path,payload,expected=200):
        status,_,body=client.request(path,'POST',payload)
        self.assertEqual(status,expected,body.decode())
        return json.loads(body)

    def event(self,code,start='2027-02-12T10:00:00+07:00',end='2027-02-12T16:00:00+07:00',owner='coordinator@captureit.local'):
        with ops.get_db() as conn:
            owner_id=conn.execute('SELECT id FROM users WHERE email=?',(owner,)).fetchone()[0]
            return conn.execute('INSERT INTO events(project_code,title,starts_at,ends_at,coordinator_id) VALUES(?,?,?,?,?)',
                                (code,code,start,end,owner_id)).lastrowid

    def vehicle(self,client,name):
        return self.post(client,'/api/vehicles',{'name':name,'plate_number':name,'ownership':'internal'})['id']

    def test_transport_first_save_returns_and_persists_vehicle_without_second_save(self):
        c=self.client();e=self.event('ONE-SAVE-TRANSPORT')
        saved=self.post(c,f'/api/events/{e}/logistics',{'transport_modes':['fleet'],'vehicle_name':'Luxio sekali simpan'})['logistics']
        self.assertEqual(saved['vehicle_name'],'Luxio sekali simpan')
        self.assertIsNotNone(saved['vehicle_id'])
        self.assertEqual(saved['transport_modes'],['fleet'])
        detail=json.loads(c.request(f'/api/events/{e}')[2])
        self.assertEqual(detail['logistics'],saved)
        reloaded=json.loads(self.client().request(f'/api/events/{e}')[2])['logistics']
        self.assertEqual(reloaded['vehicle_id'],saved['vehicle_id'])
        with ops.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM vehicles WHERE name='Luxio sekali simpan'").fetchone()[0],1)

    def test_mixed_motorcycles_courier_no_fleet_conflicts_or_driver_requirement(self):
        c=self.client();a=self.event('MOTOR-COURIER-A');b=self.event('MOTOR-COURIER-B')
        for e in (a,b):
            saved=self.post(c,f'/api/events/{e}/logistics',{'transport_modes':['motorcycles','courier'],
                'courier_name':'Lalamove','courier_note':'Pesanan LL-100','transport_note':'Crew naik motor.'})['logistics']
            self.assertIsNone(saved['vehicle_id']);self.assertEqual(saved['driver_name'],'')
            self.assertEqual(saved['transport_label'],'Motor masing-masing + Lalamove')
        with ops.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM vehicles WHERE name IN ('Lalamove','Motor masing-masing')").fetchone()[0],0)
        rows=read_xlsx(c.request('/api/events/export?start=2027-02-12&end=2027-02-12')[2])[0]
        row=next(row for row in rows if row[0]=='MOTOR-COURIER-A')
        self.assertEqual(row[14],'Motor masing-masing + Lalamove');self.assertIsNone(row[15])
        self.assertIn('LL-100',row[20]);self.assertIn('Crew naik motor',row[20])

    def test_switch_to_motorcycles_releases_vehicle_and_invalid_save_preserves_it(self):
        c=self.client();a=self.event('TRANSPORT-SWITCH-A');b=self.event('TRANSPORT-SWITCH-B')
        first=self.post(c,f'/api/events/{a}/logistics',{'transport_modes':['fleet','motorcycles'],'vehicle_name':'Luxio switch','driver_name':'Aldy'})['logistics']
        self.post(c,f'/api/events/{b}/logistics',{'transport_modes':['fleet'],'vehicle_name':'Luxio switch'},400)
        self.post(c,f'/api/events/{a}/logistics',{'transport_modes':['fleet'],'vehicle_name':'Luxio gagal','loading_date':'2027-02-15'},400)
        self.assertEqual(json.loads(c.request(f'/api/events/{a}')[2])['logistics']['vehicle_id'],first['vehicle_id'])
        with ops.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM vehicles WHERE name='Luxio gagal'").fetchone()[0],0)
        saved=self.post(c,f'/api/events/{a}/logistics',{'transport_modes':['motorcycles'],'vehicle_name':'Luxio switch','driver_name':'Aldy'})['logistics']
        self.assertIsNone(saved['vehicle_id']);self.assertEqual(saved['driver_name'],'')
        self.post(c,f'/api/events/{b}/logistics',{'transport_modes':['fleet'],'vehicle_name':'Luxio switch'})
        self.post(c,f'/api/events/{a}/logistics',{'transport_modes':['unknown']},400)
        self.post(c,f'/api/events/{a}/logistics',{'transport_modes':['other'],'other_transport':'Taksi online'})

    def test_fleet_validation_and_permissions(self):
        c=self.client(); crew=self.client('crew@captureit.local')
        self.post(crew,'/api/vehicles',{'name':'Blocked'},403)
        self.assertEqual(crew.request('/api/vehicles')[0],403)
        self.post(c,'/api/vehicles',{'name':'Rental','ownership':'rental'},400)
        v=self.post(c,'/api/vehicles',{'name':'Rental','ownership':'rental','vendor_name':'Vendor Test','plate_number':'b 100 qa'})['id']
        self.post(c,'/api/vehicles',{'name':'Duplicate','plate_number':'B100QA'},400)
        self.post(c,'/api/vehicles',{'id':v,'name':'Rental','active':False})
        e=self.event('FLEET-INACTIVE')
        self.post(c,f'/api/events/{e}/logistics',{'vehicle_id':v},400)

    def test_conflicts_loading_return_ownership_and_atomicity(self):
        c=self.client(); v=self.vehicle(c,'B200QA')
        e1=self.event('CONFLICT-A'); e2=self.event('CONFLICT-B',start='2027-02-12T17:00:00+07:00',end='2027-02-12T21:00:00+07:00')
        self.post(c,f'/api/events/{e1}/logistics',{'vehicle_id':v,'loading_date':'2027-02-11','loading_time':'23:00',
                  'vehicle_return_at':'2027-02-12T18:00','driver_name':'Aldy','client_phone':'081234'})
        self.post(c,f'/api/events/{e2}/logistics',{'vehicle_id':v},400)
        with ops.get_db() as conn:
            self.assertIsNone(conn.execute('SELECT * FROM event_operations WHERE event_id=?',(e2,)).fetchone())
        self.post(c,f'/api/events/{e1}/logistics',{'vehicle_return_at':''})
        self.post(c,f'/api/events/{e2}/logistics',{'vehicle_id':v,'loading_date':'2027-02-12','loading_time':'16:00'})
        self.post(c,f'/api/events/{e2}/logistics',{'loading_time':'15:59'},400)
        self.post(c,f'/api/events/{e2}/logistics',{'loading_time':'18:00'},400)
        self.post(c,f'/api/events/{e1}/logistics',{'ribbon_end':'31'},400)
        self.post(c,f'/api/events/{e1}/logistics',{'ribbon_start':False},400)
        detail=json.loads(c.request(f'/api/events/{e1}')[2])
        self.assertIsNone(detail['logistics']['ribbon_used'])
        self.assertEqual(detail['logistics']['client_phone'],'081234')
        other=self.event('OTHER-OWNER',owner='head.ops@captureit.local')
        self.post(c,f'/api/events/{other}/logistics',{'driver_name':'Blocked'},403)
        self.post(self.client('head.ops@captureit.local'),f'/api/events/{e1}/logistics',{'driver_name':'Head Ops edit'})
        self.post(self.client('crew@captureit.local'),f'/api/events/{e1}/logistics',{'driver_name':'Blocked'},403)
        # Removing a mapping frees the vehicle, without erasing other event details.
        self.post(c,f'/api/events/{e1}/logistics',{'vehicle_id':''})
        self.post(c,f'/api/events/{e2}/logistics',{'loading_time':'15:59'})

    def test_export_template_types_dates_zero_and_scope(self):
        c=self.client()
        # UTC February 28 is March 1 in WIB. Both date filter edges are tested.
        e=self.event('EXPORT-EXAMPLE',start='2027-02-28T18:00:00Z',end='2027-02-28T22:00:00Z')
        self.event('EXPORT-OTHER',start='2027-03-01T02:00:00+07:00',end='2027-03-01T08:00:00+07:00',owner='head.ops@captureit.local')
        v=self.post(c,'/api/vehicles',{'name':'Luxio Test','plate_number':'B300QA','ownership':'rental','vendor_name':'Vendor Test'})['id']
        self.post(c,f'/api/events/{e}/logistics',{'vehicle_id':v,'driver_name':'Aldy','loading_date':'2027-02-28','loading_time':'23:00',
            'client_name':'Client Test','client_phone':'081234567890','service_type':'Classic','equipment_setup':'Probooth','sales_name':'Nadia',
            'notes':'=HYPERLINK("https://example.invalid")'})
        with ops.get_db() as conn:
            # Preserved historical ribbon values from the earlier app. New writes use Closing.
            conn.execute('UPDATE event_operations SET ribbon_start=0,ribbon_end=0 WHERE event_id=?',(e,))
            uid=conn.execute("SELECT id FROM users WHERE email='pic@captureit.local'").fetchone()[0]
            conn.execute("INSERT INTO event_assignments(event_id,user_id,assignment_type) VALUES(?,?,'pic')",(e,uid))
            conn.execute("UPDATE events SET status='completed' WHERE id=?",(e,))
        status,_,body=c.request('/api/events/export?start=2027-03-01&end=2027-03-01&format=xlsx')
        self.assertEqual(status,200)
        rows,sheet,ns=read_xlsx(body)
        self.assertEqual(rows[0],EXPORT_HEADERS)
        self.assertEqual(len(rows),2)
        r=rows[1]
        self.assertEqual(r[:6],['EXPORT-EXAMPLE','Client Test','081234567890','Classic','Probooth','Nadia'])
        self.assertIn('(PIC)',r[6]);self.assertEqual(r[8:10],['01:00','05:00'])
        self.assertEqual(r[13:20],['PENDING_EVALUATION','Luxio Test','Aldy','Vendor Test',0,0,0])
        self.assertEqual(sheet.find(".//m:c[@r='T2']/m:f",ns).text,'R2-S2')
        self.assertIsNone(sheet.find(".//m:c[@r='U2']/m:f",ns))
        self.assertIsNotNone(sheet.find(".//m:pane[@state='frozen']",ns))
        self.assertEqual(sheet.find('m:autoFilter',ns).get('ref'),'A1:U2')
        self.assertEqual(sheet.find(".//m:c[@r='H2']",ns).get('s'),'5')
        empty=read_xlsx(c.request('/api/events/export?start=2027-02-28&end=2027-02-28')[2])[0]
        self.assertEqual(len(empty),1)
        csv_rows=list(csv.reader(io.StringIO(c.request('/api/events/export?start=2027-03-01&end=2027-03-01&format=csv')[2].decode('utf-8-sig'))))
        self.assertEqual(csv_rows[0],EXPORT_HEADERS);self.assertEqual(csv_rows[1][7],'2027-03-01')
        self.assertTrue(csv_rows[1][20].startswith("'="))

    def test_notifications_scope_persistent_read_and_new_revision(self):
        c=self.client(); crew=self.client('crew@captureit.local')
        start=(datetime.now(WIB)+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        e=self.event('NOTIFICATION-TEST',start.isoformat(),(start+timedelta(hours=5)).isoformat())
        self.event('NOTIFICATION-OTHER',start.isoformat(),(start+timedelta(hours=5)).isoformat(),owner='head.ops@captureit.local')
        data=json.loads(c.request('/api/notifications')[2]); items=[i for i in data['items'] if i['event_id']==e]
        self.assertTrue(any(i['tab']=='logistics' for i in items))
        self.assertFalse(any(i['event_title']=='NOTIFICATION-OTHER' for i in data['items']))
        item=next(i for i in items if i['tab']=='logistics')
        self.post(crew,'/api/notifications/read',{'keys':[item['key']]},400)
        self.post(c,'/api/notifications/read',{'keys':[item['key']]})
        self.post(c,'/api/notifications/read',{'keys':[item['key']]})  # Idempotent
        reopened=json.loads(self.client().request('/api/notifications')[2])
        self.assertTrue(next(i for i in reopened['items'] if i['key']==item['key'])['read'])
        v=self.vehicle(c,'B400QA')
        self.post(c,f'/api/events/{e}/logistics',{'vehicle_id':v})
        # Driver is optional; a selected transport resolves the reminder.
        self.assertFalse(any(i['event_id']==e and i['tab']=='logistics' for i in json.loads(c.request('/api/notifications')[2])['items']))
        self.post(c,f'/api/events/{e}/logistics',{'transport_modes':[]})
        changed=[i for i in json.loads(c.request('/api/notifications')[2])['items'] if i['event_id']==e and i['tab']=='logistics']
        self.assertEqual(len(changed),1);self.assertFalse(changed[0]['read'])
        self.post(c,f'/api/events/{e}/logistics',{'vehicle_id':v,'driver_name':'Aldy'})
        self.assertFalse(any(i['event_id']==e and i['tab']=='logistics' for i in json.loads(c.request('/api/notifications')[2])['items']))
        self.assertEqual(Client(self.base).request('/api/notifications')[0],401)

    def test_calendar_schedule_changes_surface_vehicle_conflicts(self):
        c=self.client(); v=self.vehicle(c,'B500QA')
        starts=(datetime.now(WIB)+timedelta(days=2)).replace(hour=10,minute=0,second=0,microsecond=0)
        e1=self.event('SYNC-CONFLICT-A',starts.isoformat(),(starts+timedelta(hours=4)).isoformat())
        e2=self.event('SYNC-CONFLICT-B',(starts+timedelta(days=1)).isoformat(),(starts+timedelta(days=1,hours=4)).isoformat())
        for e in (e1,e2): self.post(c,f'/api/events/{e}/logistics',{'vehicle_id':v,'driver_name':'Driver'})
        with ops.get_db() as conn:
            conn.execute('UPDATE events SET starts_at=?,ends_at=? WHERE id=?',(starts.isoformat(),(starts+timedelta(hours=4)).isoformat(),e2))
        notifications=json.loads(c.request('/api/notifications')[2])['items']
        self.assertEqual(len([i for i in notifications if i['title']=='Jadwal kendaraan bentrok' and i['event_id'] in (e1,e2)]),2)
        detail=json.loads(c.request(f'/api/events/{e1}')[2])
        self.assertEqual(detail['vehicle_conflicts'][0]['id'],e2)

    def test_typed_vehicle_creation_reuse_conflict_export_and_clear(self):
        c=self.client()
        e1=self.event('TYPE-VEHICLE-A',start='2027-04-05T10:00:00+07:00',end='2027-04-05T15:00:00+07:00')
        e2=self.event('TYPE-VEHICLE-B',start='2027-04-05T11:00:00+07:00',end='2027-04-05T14:00:00+07:00')
        e3=self.event('TYPE-VEHICLE-C',start='2027-04-05T15:00:00+07:00',end='2027-04-05T18:00:00+07:00')
        self.post(c,f'/api/events/{e1}/logistics',{'vehicle_name':'  Luxio   Putih  ','driver_name':'Aldy'})
        detail=json.loads(c.request(f'/api/events/{e1}')[2])['logistics']
        self.assertEqual(detail['vehicle_name'],'Luxio Putih')
        self.assertIsNotNone(detail['vehicle_id'])
        self.post(c,f'/api/events/{e2}/logistics',{'vehicle_name':'luxio putih'},400)
        self.post(c,f'/api/events/{e3}/logistics',{'vehicle_name':'LUXIO PUTIH'})
        self.assertEqual(json.loads(c.request(f'/api/events/{e3}')[2])['logistics']['vehicle_id'],detail['vehicle_id'])
        rows=read_xlsx(c.request('/api/events/export?start=2027-04-05&end=2027-04-05')[2])[0]
        exported=next(r for r in rows if r[0]=='TYPE-VEHICLE-A')
        self.assertEqual(exported[14:16],['Luxio Putih','Aldy'])
        self.post(c,f'/api/events/{e1}/logistics',{'vehicle_name':'   '})
        self.assertIsNone(json.loads(c.request(f'/api/events/{e1}')[2])['logistics']['vehicle_id'])
        self.post(c,f'/api/events/{e2}/logistics',{'vehicle_name':'Luxio Putih'})
        # A rejected form must not create an unused fleet record.
        self.post(c,f'/api/events/{e3}/logistics',{'vehicle_name':'Rollback Vehicle','loading_date':'2027-04-05'},400)
        with ops.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM vehicles WHERE lower(name)='luxio putih'").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM vehicles WHERE name='Rollback Vehicle'").fetchone()[0],0)

    def test_typed_vehicle_plate_disambiguation_and_existing_mapping(self):
        c=self.client();e=self.event('TYPE-PLATE')
        first=self.post(c,'/api/vehicles',{'name':'Hiace','plate_number':'B7711QA','ownership':'rental','vendor_name':'Sewa Test'})['id']
        self.post(c,'/api/vehicles',{'name':'Hiace','plate_number':'B7712QA'})
        self.post(c,f'/api/events/{e}/logistics',{'vehicle_name':'Hiace'},400)
        self.post(c,f'/api/events/{e}/logistics',{'vehicle_name':'b 7711 qa'})
        detail=json.loads(c.request(f'/api/events/{e}')[2])['logistics']
        self.assertEqual(detail['vehicle_id'],first)
        self.assertEqual(detail['vendor_name'],'Sewa Test')
        self.post(c,f'/api/events/{e}/logistics',{'vehicle_name':'Hiace · B7711QA'})
        self.post(c,'/api/vehicles',{'id':first,'name':'Hiace','plate_number':'B7711QA','active':False})
        # An existing inactive assignment is preserved; a new one is refused.
        self.post(c,f'/api/events/{e}/logistics',{'vehicle_name':'Hiace · B7711QA','notes':'Existing mapping'})
        other=self.event('TYPE-INACTIVE',start='2027-04-06T10:00:00+07:00',end='2027-04-06T18:00:00+07:00')
        self.post(c,f'/api/events/{other}/logistics',{'vehicle_name':'B7711QA'},400)
        self.post(c,f'/api/events/{other}/logistics',{'vehicle_name':{'name':'Invalid'}},400)
        self.post(c,f'/api/events/{other}/logistics',{'vehicle_name':'x'*141},400)
        self.post(self.client('crew@captureit.local'),f'/api/events/{other}/logistics',{'vehicle_name':'Not allowed'},403)

    def test_upgrade_is_idempotent_and_logout_revokes_session(self):
        c=self.client();v=self.vehicle(c,'B600QA');e=self.event('MIGRATION-KEEP')
        self.post(c,f'/api/events/{e}/logistics',{'vehicle_id':v,'notes':'Keep this data'})
        ops.initialize(seed=False);ops.initialize(seed=False)
        self.assertEqual(json.loads(c.request(f'/api/events/{e}')[2])['logistics']['notes'],'Keep this data')
        self.post(c,'/api/logout',{})
        self.assertEqual(c.request('/api/bootstrap')[0],401)


if __name__=='__main__': unittest.main()
