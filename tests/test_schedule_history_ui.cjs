/* Component logic with a fake DOM; no claims of real browser/mobile rendering. */
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const code=name=>fs.readFileSync(path.join(__dirname,'../static',name),'utf8');

function runtime() {
  const nodes=new Map(),listeners={};
  const node=()=>({innerHTML:'',textContent:'',hidden:false,disabled:false,open:false,
    addEventListener(){},showModal(){this.open=true;},close(){this.open=false;},focus(){}});
  const document={addEventListener(type,fn){(listeners[type]||=[]).push(fn);},
    getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);}};
  const c={console,document,Intl,state:{data:{user:{id:1}}},has:()=>true,
    escapeHtml:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
    idr:value=>'Rp'+value,dateLong:value=>value,timePart:value=>value,
    toast(){},refreshData:async()=>{},FormData:class {
      constructor(form){this.values=Object.fromEntries(Object.entries(form.elements).map(([k,v])=>[k,v.value]));}
      get(key){return this.values[key]??null;}getAll(key){return key==='skill_ids'?['1']:[];}
    }};
  vm.createContext(c);vm.runInContext(code('staffing-ui.js'),c);vm.runInContext(code('compensation-ui.js'),c);
  const panel=node(),button=node(),error=node();
  const elements={user_id:{value:'7',focus(){}},assignment_type:{value:'crew'},position_id:{value:'1'}};
  const form={dataset:{eventId:'12'},isConnected:true,elements,
    querySelector:s=>s==='button[type="submit"]'?button:s==='[data-assignment-error]'?error:panel,
    querySelectorAll:()=>[...Object.values(elements),button]};
  return {c,nodes,listeners,form,panel,button,error};
}
const warning=(severity='yellow',signature='v1')=>({severity,signature,needs_confirmation:severity!=='green',blocked:false,
  starts_at:'2040-06-01T09:00:00+07:00',ends_at:'2040-06-01T15:00:00+07:00',location:'Jakarta',
  conflicts:severity==='green'?[]:[{kind:severity==='red'?'overlap':'same_day',title:'Event lain',project_code:'QA',location:'Bekasi',
    starts_at:'2040-06-01T16:00:00+07:00',ends_at:'2040-06-01T20:00:00+07:00',roles:['pic'],gap_minutes:60}]});

for(const severity of ['yellow','red'])test(`${severity} warning saves with one explicit click and no extra checkbox`,async()=>{
  const a=runtime(),calls=[];a.c.showStaffAvailability(a.form,warning(severity));
  assert.equal(a.button.textContent,'Tetap Jadwalkan');assert(!a.panel.innerHTML.includes('checkbox'));
  a.c.api=async(url,options)=>{calls.push({url,payload:JSON.parse(options.body)});return {ok:true};};
  await a.c.saveStaffAssignment(a.form);
  assert.equal(calls.length,1);assert.equal(calls[0].payload.schedule_confirmed,true);
  assert.equal(calls[0].payload.schedule_ack_signature,'v1');assert.equal(calls[0].payload.user_id,7);
  assert.equal(a.form.elements.user_id.disabled,false);
});

test('warning discovered only during submit must be shown before it can be confirmed',async()=>{
  const a=runtime();let posts=0;
  a.c.api=async(url,options)=>{if(options){posts++;return {ok:true};}return warning();};
  await a.c.saveStaffAssignment(a.form);
  assert.equal(posts,0);assert.equal(a.button.textContent,'Tetap Jadwalkan');assert(!a.error.hidden);
  await a.c.saveStaffAssignment(a.form);assert.equal(posts,1);
});

test('available staff saves once with no confirmation',async()=>{
  const a=runtime();let payload;
  a.c.api=async(url,options)=>options?(payload=JSON.parse(options.body),{ok:true}):warning('green');
  await a.c.saveStaffAssignment(a.form);assert.equal(payload.schedule_confirmed,false);assert.equal(payload.schedule_ack_signature,'');
});

test('duplicate submit is ignored while first save is pending',async()=>{
  const a=runtime();a.c.showStaffAvailability(a.form,warning());let finish,calls=0;
  a.c.api=()=>{calls++;return new Promise(resolve=>{finish=resolve;});};
  const first=a.c.saveStaffAssignment(a.form);await a.c.saveStaffAssignment(a.form);assert.equal(calls,1);
  finish({ok:true});await first;assert.equal(a.button.disabled,false);
});

test('server schedule change displays fresh warning, then accepts its new confirmation',async()=>{
  const a=runtime();a.c.showStaffAvailability(a.form,warning());let calls=0;
  a.c.api=async(url,options)=>{
    calls++;const payload=JSON.parse(options.body);
    if(calls===1){const error=new Error('Jadwal berubah');error.details={availability:warning('red','v2')};throw error;}
    assert.equal(payload.schedule_ack_signature,'v2');return {ok:true};
  };
  await a.c.saveStaffAssignment(a.form);assert.equal(calls,1);assert.equal(a.form.elements.user_id.value,'7');
  assert.equal(a.error.textContent,'Jadwal berubah');assert(a.panel.innerHTML.includes('Jam event beririsan'));
  await a.c.saveStaffAssignment(a.form);assert.equal(calls,2);
});

test('changing staff clears the previous confirmation and selected person',()=>{
  const a=runtime();a.c.showStaffAvailability(a.form,warning());
  const target={closest:selector=>selector==='#assignment-form'?a.form:selector.includes('change-staff-choice')?{}:null};
  for(const handler of a.listeners.click)handler({target});
  assert.equal(a.form.elements.user_id.value,'');assert.equal(a.button.textContent,'＋ Simpan penugasan');
  assert.equal(a.panel.textContent,'Pilih staff untuk memeriksa jadwal.');
});

function history() {return {kind:'salary',user:{id:7,full_name:'Crew <script>alert(1)</script>'},today:'2040-01-01',
  current:{new_amount_rupiah:5000000},next_cursor:null,rows:[{id:1,old_amount_rupiah:5500000,new_amount_rupiah:5000000,
    delta_rupiah:-500000,effective_from:'2040-01-01',reason:'Koreksi <script>alert(1)</script>',actor_name:'<img onerror=x>',
    created_at:'2039-12-31 18:30:00',source:'change',superseded:false}]};}

test('history escapes private user content and shows old/new, delta and UTC record time correctly',()=>{
  const a=runtime(),html=a.c.renderCompensationHistory(history());
  assert(!html.includes('<script>'));assert(!html.includes('<img onerror'));assert(html.includes('&lt;script&gt;'));
  assert(html.includes('Rp5500000'));assert(html.includes('Rp5000000'));assert(html.includes('−Rp500000'));
  assert(html.includes('2039-12-31T18:30:00Z'));
});

test('migration has no invented effective date; superseded future corrections are labeled',()=>{
  const a=runtime(),data=history();data.rows[0]={...data.rows[0],effective_from:null,old_amount_rupiah:null,delta_rupiah:null,source:'migration'};
  let html=a.c.renderCompensationHistory(data);assert(html.includes('Tanggal berlaku awal tidak diketahui'));assert(html.includes('Saldo awal migrasi'));
  data.rows[0]={...data.rows[0],effective_from:'2041-01-01',source:'change',superseded:true};
  html=a.c.renderCompensationHistory(data);assert(html.includes('Dikoreksi'));assert(!html.includes('Terjadwal'));
});

test('closing history or changing account discards late private API responses',async()=>{
  const a=runtime();let finish;a.c.api=()=>new Promise(resolve=>{finish=resolve;});
  const request=a.c.openCompensationHistory('salary',7);a.c.closeCompensationHistory();finish(history());await request;
  assert.equal(a.nodes.get('compensation-body').innerHTML,'');assert.equal(a.nodes.get('compensation-dialog').open,false);
  const second=a.c.openCompensationHistory('salary',7);a.c.state.data.user.id=2;finish(history());await second;
  assert(!a.nodes.get('compensation-body').innerHTML.includes('5000000'));
});

test('history pagination preserves previous entries on error and loads next cursor',async()=>{
  const a=runtime();let calls=0;const data=history();data.next_cursor=50;
  a.c.api=async url=>{
    calls++;if(calls===1)return structuredClone(data);
    assert(url.includes('before=50'));
    if(calls===2)throw new Error('Jaringan putus');
    const older=history();older.rows[0].id=2;older.rows[0].reason='Riwayat lebih lama';return older;
  };
  await a.c.openCompensationHistory('salary',7);await a.c.loadCompensationHistory(true);
  assert.equal(a.nodes.get('compensation-error').textContent,'Jaringan putus');assert(a.nodes.get('compensation-body').innerHTML.includes('Rp5000000'));
  await a.c.loadCompensationHistory(true);assert(a.nodes.get('compensation-body').innerHTML.includes('Riwayat lebih lama'));
});

test('unauthorized history has no dialog or network access',async()=>{
  const a=runtime();a.c.has=()=>false;a.c.api=()=>{throw new Error('Must not request');};
  await a.c.openCompensationHistory('salary',7);assert(!a.nodes.has('compensation-body'));
});
