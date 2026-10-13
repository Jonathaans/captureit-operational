from concurrent.futures import ThreadPoolExecutor
from test_design_attendance import Base
from test_workflows import ops, Client
import sales_inventory


class SalesInventoryTests(Base):
    def setUp(self):
        self.aid,self.a=self.person('sales_staff','Sales A')
        self.bid,self.b=self.person('sales_staff','Sales B')
        self.warehouse=self.login('warehouse.head@captureit.local')
        self.staff=self.login('warehouse@captureit.local')
        data=self.json(self.a,'/api/bootstrap')['sales']
        self.pipeline=data['pipelines'][0]['id']
        self.stages={r['code']:r['id'] for r in data['stages'] if r['pipeline_id']==self.pipeline}
        self.lead=self.json(self.a,'/api/sales/leads','POST',{'pipeline_id':self.pipeline,'stage_id':self.stages['lead'],'owner_id':self.bid,
            'client_name':'Client <script>alert(1)</script>','event_name':'QA Corporate','event_date':'2041-07-01','venue':'Jakarta'})['id']

    def doc(self,kind='quote',source=None,client=None,expected=200,**extra):
        payload={'lead_id':self.lead,'kind':kind,'status':'sent','source_document_id':source,'issued_at':'2041-06-01',
                 'items':[{'description':'Photobooth','quantity':2,'days':3,'unit_price_rupiah':100000}],**extra}
        return self.json(client or self.a,'/api/sales/documents','POST',payload,expected)

    def chain(self):
        docs=[];source=None
        for kind in ('quote','proforma','invoice','spk'):
            doc=self.doc(kind,source);docs.append(doc['id']);source=doc['id']
        return docs

    def item(self,kind='inventory',qty=3):
        return self.json(self.warehouse,'/api/inventory/items','POST',{'sku':f'QA-{self.lead}-{kind}','name':'QA equipment','kind':kind,'unit':'pcs','initial_quantity':qty})['id']

    def delivery(self,spk,item,qty=1,release=True,expected=200):
        return self.json(self.warehouse,'/api/delivery-orders','POST',{'spk_document_id':spk,'release':release,'items':[{'item_id':item,'planned_quantity':qty}]},expected)

    def scan(self,code,order=None,direction='out',qty=1,expected=200,client=None,**extra):
        return self.json(client or self.staff,'/api/inventory/scan','POST',{'scan_code':code,'delivery_order_id':order,'direction':direction,'quantity':qty,**extra},expected)

    def test_owner_forcing_read_write_and_print_scope(self):
        quote=self.doc()
        data=self.json(self.a,'/api/bootstrap')['sales']
        self.assertEqual(next(l for l in data['leads'] if l['id']==self.lead)['owner_id'],self.aid)
        self.assertNotIn(self.lead,{l['id'] for l in self.json(self.b,'/api/bootstrap')['sales']['leads']})
        self.json(self.b,f'/api/sales/leads/{self.lead}/stage','POST',{'stage_id':self.stages['quote']},403)
        self.doc(client=self.b,expected=403)
        self.assertEqual(self.b.request(f"/api/sales/documents/{quote['id']}/print")[0],403)

    def test_linked_chain_totals_immutable_print_and_snapshot(self):
        quote=self.doc(discount_rupiah=10000,tax_rupiah=5000)
        self.assertEqual(quote['amount_rupiah'],595000)
        proforma=self.doc('proforma',quote['id'],items=None)
        self.assertEqual(proforma['amount_rupiah'],595000)
        self.doc(id=quote['id'],expected=400)
        with ops.get_db() as conn:conn.execute("UPDATE sales_leads SET event_name='CHANGED LATER' WHERE id=?",(self.lead,))
        status,_,body=self.a.request(f"/api/sales/documents/{quote['id']}/print")
        self.assertEqual(status,200);html=body.decode()
        self.assertIn('QA Corporate',html);self.assertNotIn('CHANGED LATER',html);self.assertNotIn('<script>alert(1)</script>',html)
        self.json(self.a,'/api/sales/documents','POST',{'id':quote['id'],'lead_id':self.lead,'kind':'quote','status':'cancelled'},400)

    def test_draft_wrong_kind_and_wrong_owner_source_rejected(self):
        draft=self.doc(status='draft');self.doc('proforma',draft['id'],expected=400)
        quote=self.doc();self.doc('invoice',quote['id'],expected=400)
        with ops.get_db() as conn:
            other=conn.execute("INSERT INTO sales_documents(lead_id,kind,document_no,status,created_by) SELECT id,'quote',?,'sent',? FROM sales_leads WHERE owner_id!=? LIMIT 1",(f'OTHER-{self.lead}',self.bid,self.aid)).lastrowid
        self.doc('proforma',other,expected=400)

    def test_multiple_pipelines_and_drag_do_not_issue_spk(self):
        pipeline=self.json(self.admin,'/api/sales/pipelines','POST',{'name':f'Enterprise {self.lead}'})['id']
        data=self.json(self.a,'/api/bootstrap')['sales'];self.assertIn(pipeline,{p['id'] for p in data['pipelines']})
        self.json(self.a,'/api/sales/pipelines','POST',{'name':'Forbidden'},403)
        foreign=next(s['id'] for s in data['stages'] if s['pipeline_id']==pipeline)
        self.json(self.a,f'/api/sales/leads/{self.lead}/stage','POST',{'stage_id':foreign},400)
        self.json(self.a,f'/api/sales/leads/{self.lead}/stage','POST',{'stage_id':self.stages['spk']})
        lead=next(l for l in self.json(self.a,'/api/bootstrap')['sales']['leads'] if l['id']==self.lead)
        self.assertEqual(lead['status'],'open');self.assertEqual(lead['document_count'],0)

    def test_spk_event_is_idempotent_and_links_delivery(self):
        spk=self.chain()[-1];item=self.item();order=self.delivery(spk,item)
        payload={'starts_at':'2041-07-01T12:00','ends_at':'2041-07-01T18:00'}
        self.json(self.b,f'/api/sales/spk/{spk}/event','POST',payload,403)
        a=self.json(self.a,f'/api/sales/spk/{spk}/event','POST',payload)
        b=self.json(self.a,f'/api/sales/spk/{spk}/event','POST',payload)
        self.assertEqual(a['event_id'],b['event_id'])
        orders=self.json(self.a,'/api/bootstrap')['delivery_orders']['orders']
        self.assertEqual(next(o for o in orders if o['id']==order['id'])['event_id'],a['event_id'])
        self.assertNotIn(order['id'],{o['id'] for o in self.json(self.b,'/api/bootstrap')['delivery_orders']['orders']})
        self.assertEqual(self.b.request(f"/api/delivery-orders/{order['id']}/print")[0],403)

    def test_release_reserves_stock_and_draft_cannot_scan(self):
        spk=self.chain()[-1];item=self.item(qty=1);draft=self.delivery(spk,item,release=False)
        code=f'QA-{self.lead}-inventory'
        self.scan(code,draft['id'],expected=400)
        self.json(self.warehouse,'/api/delivery-orders','POST',{'id':draft['id'],'release':True})
        self.delivery(spk,item,expected=400)
        self.json(self.warehouse,'/api/delivery-orders','POST',{'id':draft['id'],'items':[]},400)

    def test_inventory_movement_limits_and_idempotent_retry(self):
        spk=self.chain()[-1];item=self.item();order=self.delivery(spk,item);code=f'QA-{self.lead}-inventory'
        self.scan(code,expected=400);self.scan(code,order['id'],'return',expected=400)
        result=self.scan(code,order['id'],request_id=f'qa-{self.lead}')
        duplicate=self.scan(code,order['id'],request_id=f'qa-{self.lead}')
        self.assertEqual(result['id'],duplicate['id']);self.assertTrue(duplicate['duplicate'])
        self.scan(code,order['id'],qty=2,expected=400)
        self.scan(code,order['id'],'return');self.scan(code,order['id'],'return',expected=400)

    def test_asset_unit_out_return_and_wrong_surat_jalan(self):
        spk=self.chain()[-1];item=self.item('asset',2);one=self.delivery(spk,item);two=self.delivery(spk,item)
        base=f'QA-{self.lead}-ASSET';code=base+'-0001'
        self.scan(base,one['id'],expected=400)
        self.scan(code,one['id']);self.scan(code,two['id'],expected=400)
        self.scan(code,two['id'],'return',expected=400);self.scan(code,one['id'],'return')
        assets=self.json(self.warehouse,'/api/bootstrap')['inventory']['assets']
        self.assertEqual(next(a for a in assets if a['asset_code']==code)['status'],'available')
        self.assertEqual(self.warehouse.request(f'/api/inventory/items/{item}/labels')[0],200)

    def test_concurrent_asset_scans_only_one_succeeds(self):
        spk=self.chain()[-1];item=self.item('asset',1);order=self.delivery(spk,item);code=f'QA-{self.lead}-ASSET-0001'
        clients=[self.login('warehouse@captureit.local'),self.login('warehouse@captureit.local')]
        def send(client):return client.request('/api/inventory/scan','POST',{'scan_code':code,'direction':'out','quantity':1,'delivery_order_id':order['id']})[0]
        with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(send,clients))
        self.assertEqual(sorted(statuses),[200,400])

    def test_role_boundaries_and_admin_sales_defaults(self):
        _,admin_sales=self.person('admin_sales','Admin Sales QA')
        data=self.json(admin_sales,'/api/bootstrap');permissions=data['user']['permissions']
        self.assertIn('sales.manage_all',permissions);self.assertIn('delivery_orders.manage',permissions)
        self.assertNotIn('payroll.read',permissions);self.assertNotIn('inventory.scan',permissions)
        self.scan('NO-CODE',direction='in',client=self.staff,expected=403)
        self.scan('NO-CODE',client=self.a,expected=403)

    def test_bad_amounts_do_not_leave_partial_document(self):
        for bad in (-1,1.5,True,'1.5'):
            self.doc(items=[{'description':'x','quantity':bad,'days':1,'unit_price_rupiah':100}],expected=400)
        with ops.get_db() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM sales_documents WHERE lead_id=?',(self.lead,)).fetchone()[0],0)

    def test_migration_keeps_existing_records_and_constraints(self):
        self.chain()
        with ops.get_db() as conn:
            sales_inventory.migrate(conn);sales_inventory.migrate(conn)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sales_documents WHERE lead_id=?',(self.lead,)).fetchone()[0],4)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
