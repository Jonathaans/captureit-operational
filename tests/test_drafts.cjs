/* Run: node --test tests/test_drafts.cjs. No browser dependencies required. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=name=>fs.readFileSync(path.join(__dirname,'../static',name),'utf8');
const plain=value=>JSON.parse(JSON.stringify(value));

function memoryStorage(){const data=new Map();return {getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};}
function photoStorage(){const data=new Map();return {data,put:async(key,value)=>data.set(key,value),get:async key=>data.get(key),remove:async key=>data.delete(key)};}
function runtime(storage=memoryStorage(),photos=photoStorage()) {
  const documentListeners={},windowListeners={};
  const c={console,structuredClone,setTimeout,clearTimeout,localStorage:storage,navigator:{onLine:true},photoAdapter:photos,
    window:{addEventListener(type,fn){(windowListeners[type]||=[]).push(fn);}},
    document:{addEventListener(type,fn){(documentListeners[type]||=[]).push(fn);}},
    state:{data:{draft_namespace:'workspace-A',user:{id:7}},drawer:null,drawerTab:'closing'},
    escapeHtml:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
    dateLong:s=>s,timePart:s=>s.slice(11,16),badge:()=>'',renderClearEventAction:()=>'',renderDrawer(){},toast(){},$:()=>null,
  };
  vm.createContext(c);vm.runInContext(source('draft-store.js'),c);vm.runInContext('deviceDraftStore.photos=photoAdapter',c);
  vm.runInContext(source('closing-ui.js'),c);
  return {c,store:vm.runInContext('deviceDraftStore',c),Store:vm.runInContext('DeviceDraftStore',c),documentListeners,windowListeners,storage,photos};
}
function event(version=0){return {id:12,title:'Event <script>alert(1)</script>',project_code:'OPS-12',starts_at:'2026-09-30T09:00:00+07:00',ends_at:'2026-09-30T16:00:00+07:00',status:'scheduled',assignments:[],logistics:{},closing_report:{version,status:version?'draft':'not_started',can_edit:true,data:{materials:{}},photos:[],history:[],context:{}}};}
function draft(version=0){return {version,data:{issues:'Printer macet',resolution:'Ganti printer',materials:{frame:10,magnet:0},ribbon_rolls:[{start:100,end:70}]},photos:[]};}

test('text, zeros and ribbon survive a fresh page instance; account and workspace keys are isolated',async()=>{
  const a=runtime(),key=a.store.key('workspace-A',7,12),d=draft();
  const saved=a.store.write(key,d,null);await saved.ready;
  const b=runtime(a.storage,a.photos);
  const recovered=await b.store.hydrate(key,b.store.read(key));
  assert.deepEqual(plain(recovered.data),d.data);assert.equal(recovered.dirty,true);
  assert.equal(b.store.read(b.store.key('workspace-A',8,12)),null);
  assert.equal(b.store.read(b.store.key('workspace-B',7,12)),null);
});

test('photo bytes persist separately; missing photos stay visible and are not silently omitted',async()=>{
  const a=runtime(),key=a.store.key('workspace-A',7,12),d=draft();d.photos=[{kind:'setup',image:'data:image/jpeg;base64,PHOTO'}];
  const saved=a.store.write(key,d,null);await saved.ready;
  assert(!a.storage.getItem(key).includes('base64'));
  const b=runtime(a.storage,a.photos),recovered=await b.store.hydrate(key,b.store.read(key));
  assert.equal(recovered.photos[0].image,d.photos[0].image);
  a.photos.data.clear();const missing=await b.store.hydrate(key,b.store.read(key));
  assert.equal(missing.photos.length,1);assert.equal(missing.photos[0].missing,true);assert(missing.storageError);
});

test('stale tab cannot overwrite or clear a newer draft',async()=>{
  const a=runtime(),key=a.store.key('workspace-A',7,12),initial=a.store.write(key,draft(),null);await initial.ready;
  const b=runtime(a.storage,a.photos),newer=draft();newer.data.issues='Catatan dari tab B';
  const updated=b.store.write(key,newer,initial.record.token);await updated.ready;
  assert.throws(()=>a.store.write(key,draft(),initial.record.token),error=>error.code==='draft_conflict');
  assert.equal(a.store.clear(key,initial.record.token),false);
  assert.equal(a.store.read(key).data.issues,'Catatan dari tab B');
  assert.equal(b.store.clear(key,updated.record.token),true);assert.equal(b.store.read(key),null);
});

test('photo storage failure still retains text and surfaces a failure status',async()=>{
  const photos=photoStorage();photos.put=async()=>{throw new Error('Quota exceeded');};
  const a=runtime(memoryStorage(),photos),d=event();a.c.state.drawer=d;
  const local=a.c.closingDraft(d);local.data.issues='Tetap tersimpan';local.photos=[{kind:'event',image:'data:image/jpeg;base64,PHOTO'}];
  a.c.persistClosingDraft(local);await local.persistPromise;
  assert.match(a.c.closingDraftStatus(local),/foto belum tersimpan/);
  assert.equal(a.store.read(a.c.closingDraftKey(d)).data.issues,'Tetap tersimpan');
});

test('full text storage reports failure without losing in-memory input',()=>{
  const storage=memoryStorage();storage.setItem=()=>{throw new Error('Quota');};
  const a=runtime(storage),d=event();a.c.state.drawer=d;
  const local=a.c.closingDraft(d);local.data.issues='Jangan hilang';a.c.persistClosingDraft(local);
  assert.match(a.c.closingDraftStatus(local),/belum tersimpan di perangkat/);assert.equal(local.data.issues,'Jangan hilang');
});

test('draft restores automatically at same version, but newer server data requires an explicit choice',async()=>{
  const a=runtime(),key=a.store.key('workspace-A',7,12),saved=a.store.write(key,draft(2),null);await saved.ready;
  const b=runtime(a.storage,a.photos),same=event(2);b.c.state.drawer=same;
  await b.c.prepareClosingDraft(same,b.c.state.data);
  const local=b.c.closingDraft(same);assert.equal(local.data.issues,'Printer macet');assert(!local.conflict);
  const latest=event(3);latest.closing_report.data.issues='Sudah diperbarui di server';b.c.state.drawer=latest;
  assert.equal(b.c.closingDraft(latest).conflict,'server');
  const html=b.c.renderClosingTab(latest);
  assert(html.includes('Pulihkan draft ke form'));assert(html.includes('Printer macet'));assert(html.includes('Sudah diperbarui di server'));
  assert(html.includes('closing-fieldset" disabled'));assert(!html.includes('value="submit"'));
  assert(!html.includes('<script>'));assert.equal(a.store.read(key).version,2);
});

test('locked reports keep local recovery visible without enabling submit',async()=>{
  const a=runtime(),d=event(1),key=a.c.closingDraftKey(d),saved=a.store.write(key,draft(),null);await saved.ready;
  d.closing_report.can_edit=false;d.closing_report.status='submitted';a.c.state.drawer=d;
  await a.c.prepareClosingDraft(d,a.c.state.data);
  const html=a.c.renderClosingTab(d);
  assert(html.includes('Hapus draft perangkat'));assert(!html.includes('Pulihkan draft ke form'));assert(!html.includes('Kirim untuk review'));
});

test('explicit recovery rebases to the displayed server version while keeping local fields',async()=>{
  const a=runtime(),d=event(3),key=a.c.closingDraftKey(d),saved=a.store.write(key,draft(2),null);await saved.ready;
  a.c.state.drawer=d;await a.c.prepareClosingDraft(d,a.c.state.data);a.c.closingDraft(d);
  const button={dataset:{action:'closing-recover'}};
  await a.documentListeners.click[0]({target:{closest:()=>button}});
  const restored=a.c.closingDraft(d);await restored.persistPromise;
  assert.equal(restored.version,3);assert(!restored.conflict);assert.equal(restored.data.issues,'Printer macet');
  assert.equal(a.store.read(key).version,3);
});

test('storage event flags a cross-tab conflict; offline status never says saved to server',async()=>{
  const a=runtime(),d=event();a.c.state.drawer=d;const local=a.c.closingDraft(d);
  local.data.issues='Tab A';a.c.persistClosingDraft(local);await local.persistPromise;
  const b=runtime(a.storage,a.photos),key=a.c.closingDraftKey(d),saved=b.store.write(key,draft(),local.token);await saved.ready;
  a.windowListeners.storage[0]({key});assert.equal(local.conflict,'tab');assert.equal(local.data.issues,'Tab A');
  a.c.navigator.onLine=false;assert.match(a.c.closingDraftStatus(local),/^Offline/);assert.match(a.c.closingDraftStatus(local),/belum disimpan ke server/);
});

test('submission failure keeps the local draft and re-enables the form',async()=>{
  const a=runtime(),d=event();a.c.state.drawer=d;
  const local=a.c.closingDraft(d);Object.assign(local,draft());
  const error={hidden:true,scrollIntoView(){}},fieldset={},buttons=[{},{}];
  const form={id:'closing-form',dataset:{eventId:'12'},isConnected:true,querySelector:s=>s==='fieldset'?fieldset:error,querySelectorAll:()=>buttons};
  a.c.captureClosingDraft=()=>local;a.c.api=async()=>{throw new Error('Koneksi terputus');};
  await a.documentListeners.submit[0]({target:form,preventDefault(){},submitter:{value:'draft'}});
  assert.equal(a.store.read(a.c.closingDraftKey(d)).data.issues,'Printer macet');
  assert.equal(error.hidden,false);assert.equal(fieldset.disabled,false);assert.equal(local.saving,false);
});

test('successful server save clears only the submitted draft and its photos',async()=>{
  const a=runtime(),d=event();a.c.state.drawer=d;
  const local=a.c.closingDraft(d);Object.assign(local,draft());
  local.photos=[{kind:'setup',image:'data:image/jpeg;base64,PHOTO'}];
  const form={id:'closing-form',dataset:{eventId:'12'},isConnected:true,querySelector:()=>({scrollIntoView(){}}),querySelectorAll:()=>[]};
  a.c.captureClosingDraft=()=>local;a.c.api=async()=>({ok:true,version:1});a.c.refreshData=async()=>{};
  await a.documentListeners.submit[0]({target:form,preventDefault(){},submitter:{value:'draft'}});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(a.store.read(a.c.closingDraftKey(d)),null);assert.equal(a.photos.data.size,0);
});

test('server completion does not erase a draft written in another tab during the request',async()=>{
  const a=runtime(),d=event();a.c.state.drawer=d;const key=a.c.closingDraftKey(d);
  const local=a.c.closingDraft(d);Object.assign(local,draft());
  const form={id:'closing-form',dataset:{eventId:'12'},isConnected:true,querySelector:()=>({scrollIntoView(){}}),querySelectorAll:()=>[]};
  a.c.captureClosingDraft=()=>local;a.c.refreshData=async()=>{};
  a.c.api=async()=>{
    const b=runtime(a.storage,a.photos),newer=draft();newer.data.issues='Catatan tambahan tab B';
    await b.store.write(key,newer,local.token).ready;return {ok:true};
  };
  await a.documentListeners.submit[0]({target:form,preventDefault(){},submitter:{value:'draft'}});
  assert.equal(a.store.read(key).data.issues,'Catatan tambahan tab B');
});

test('late availability response cannot replace the currently selected staff',async()=>{
  const a=runtime(),c=a.c;vm.runInContext(source('staffing-ui.js'),c);
  const panel={},form={dataset:{eventId:'12'},isConnected:true,elements:{user_id:{value:'7'},travel_minutes:{value:'60'}},querySelector:()=>panel};
  let first,second;c.api=()=>new Promise(resolve=>{if(!first) first=resolve;else second=resolve;});
  const pending=c.checkStaffAvailability(form);form.elements.user_id.value='8';
  const current=c.checkStaffAvailability(form);
  const result={starts_at:'2026-09-30T09:00+07:00',ends_at:'2026-09-30T17:00+07:00',conflicts:[],signature:'new'};
  second(result);await current;const html=panel.innerHTML;
  first({...result,blocked:true});await pending;
  assert.equal(panel.innerHTML,html);assert.match(html,/Tidak ada event lain/);
});

test('logout waits for draft work, retains saved drafts, and blocks when device storage failed',async()=>{
  function logoutRuntime() {
    const a=runtime(),nodes=new Map();
    a.c.document.getElementById=()=>({addEventListener(){}});
    a.c.$=selector=>{
      if(!nodes.has(selector)) nodes.set(selector,{classList:{remove(){}},close(){},focus(){}});
      return nodes.get(selector);
    };
    vm.runInContext(source('push-ui.js'),a.c);
    vm.runInContext(source('operations-ui.js'),a.c);a.c.stopNotifications=()=>{};a.c.showLogin=()=>{};a.c.closeCompensationHistory=()=>{};
    return {...a,nodes};
  }
  const a=logoutRuntime(),d=event();a.c.state.drawer=d;const key=a.c.closingDraftKey(d);
  const local=a.c.closingDraft(d);a.c.persistClosingDraft(local);await local.persistPromise;
  let calls=0,finish;a.c.api=async()=>{calls++;return {ok:true};};
  local.photosBusy=true;local.photoWork=new Promise(resolve=>finish=resolve);
  const pending=a.c.confirmLogout();assert.equal(calls,0);local.photosBusy=false;finish();await pending;
  assert.equal(calls,1);assert.equal(a.c.state.data,null);assert(a.store.read(key));
  const b=logoutRuntime();b.c.state.drawer=event();const unsaved=b.c.closingDraft(b.c.state.drawer);
  unsaved.dirty=true;unsaved.storageError='Disk full';let blockedCalls=0;b.c.api=async()=>{blockedCalls++;};
  await b.c.confirmLogout();assert.equal(blockedCalls,0);assert(b.c.state.data);
  assert.match(b.nodes.get('#logout-error').textContent,/draft/);
});
