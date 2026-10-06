/* Actual JS functions in a VM/fake DOM. Not a real-browser or real-FCM test. */
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=name=>fs.readFileSync(path.join(__dirname,'../static',name),'utf8');
const notice='a'.repeat(32);
function ui(){
  const nodes=new Map(),storage=new Map(),listeners={};
  const node=()=>({open:false,hidden:false,innerHTML:'',textContent:'',showModal(){this.open=true;},close(){this.open=false;}});
  const c={console,URL,URLSearchParams,Intl,Date,Uint8Array,
    state:{data:{draft_namespace:'qa',user:{id:1,roles:[]},designers:[]}},notificationEpoch:0,
    escapeHtml:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
    $:s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s);},
    document:{addEventListener:(type,fn)=>(listeners[type]||=[]).push(fn),querySelector:()=>null},
    navigator:{},location:{href:'https://ops.example.test/'},isSecureContext:true,
    history:{replaceState(){}},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    has:()=>true,designStages:[['brief_needed','Brief'],['revision','Revisi'],['approved','Disetujui']],attendanceTimeLabel:v=>v,
    closeNotifications(){},toast(){},refreshNotifications:async()=>{},renderNotifications(){},renderShell(){},renderPage(){},
    markNotificationsRead:async()=>{},openEvent:async()=>{},renderInhousePayslipsPage:async()=>'<div>Slip</div>',
  };
  c.window=c;vm.createContext(c);vm.runInContext(source('push-ui.js'),c);vm.runInContext(source('design-ui.js'),c);
  return {c,nodes,storage,listeners};
}
const settings=()=>({preferences:{categories:{payroll:true,schedule:true,design:true,operations:true,reminders:true},quiet_start:'22:00',quiet_end:'07:00'},
  categories:{payroll:'Slip gaji',schedule:'Penjadwalan',design:'Desain',operations:'Operasional',reminders:'Pengingat'},
  devices:[],push:{ready:false,message:'Push belum aktif'},delivery_counts:{}});

test('settings preserve inbox explanation and do not imply push is active before configuration',()=>{
  const {c}=ui(),html=c.pushSettingsHTML(settings());assert(html.includes('Push belum aktif'));assert(html.includes('disabled'));
  assert(html.includes('catatannya tetap tersedia'));assert(html.includes('bukan bukti notifikasi tampil'));
});
test('device names and brief text are escaped; unsafe links are not rendered',()=>{
  const {c}=ui(),data=settings();data.devices=[{id:1,label:'<img onerror=bad>',platform:'web',this_session:true}];
  assert(!c.pushSettingsHTML(data).includes('<img onerror'));
  const d={id:1,status:'scheduled',coordinator_id:1,design_task:{id:2,brief_text:'<script>bad</script>',brief_version:1,final_url:'javascript:alert(1)'}};
  const html=c.renderDesignForms(d);assert(html.includes('&lt;script&gt;'));assert(!html.includes('href="javascript:'));
  assert.equal(c.approvedDesignLink({approved_design_url:'javascript:alert(1)'}),'');
  assert(c.approvedDesignLink({approved_design_url:'https://example.test/design'}).includes('noopener noreferrer'));
});
test('brief includes explicit recipient/version/send and WIB due time',()=>{
  const {c}=ui();c.state.data.designers=[{id:5,full_name:'Designer'}];
  const html=c.renderDesignForms({id:1,status:'scheduled',coordinator_id:1,design_task:{id:2,brief_text:'Brief',brief_version:3,assignee_id:5,due_at:'2090-01-01T03:00:00Z'}});
  assert(html.includes('data-version="3"'));assert(html.includes('Kirim brief'));assert(html.includes('Antrean tim desain'));assert(html.includes('2090-01-01T10:00'));
});
test('settings reply is discarded when account changes or dialog closes',async()=>{
  const {c,nodes}=ui();let resolve;c.api=()=>new Promise(r=>resolve=r);const pending=c.openPushSettings();
  c.state.data.user.id=2;resolve(settings());await pending;assert(!nodes.get('#push-settings-body').innerHTML.includes('Perangkat ini'));
  c.state.data.user.id=1;const again=c.openPushSettings();nodes.get('#push-settings-dialog').close();resolve(settings());await again;
  assert(!nodes.get('#push-settings-body').innerHTML.includes('Perangkat ini'));
});
test('opening settings never requests permission; unsupported registration reports a clear error',async()=>{
  const {c}=ui();let requests=0;c.Notification={permission:'default',requestPermission(){requests++;}};c.api=async()=>settings();
  await c.openPushSettings();assert.equal(requests,0);
  await assert.rejects(c.registerPush({ready:true}),/HTTPS/);assert.equal(requests,0);
});
test('payslip click selects all years and the correct target without opening an event',async()=>{
  const {c}=ui();let marked=[],rendered=0;c.markNotificationsRead=async keys=>marked=keys;c.openEvent=()=>assert.fail('Wrong destination');
  c.renderInhousePayslipsPage=async()=>{rendered++;return 'Payslip';};
  await c.navigateNotification({key:'notice:'+notice,read:false,target_kind:'payslip',target_id:8});
  assert.equal(c.state.page,'inhouse-payslips');assert.equal(c.state.payslipYear,'all');assert.equal(c.state.payslipTarget,8);assert.equal(rendered,1);assert.equal(marked.length,1);
});
test('event click uses event/tab; removed assignment opens inbox',async()=>{
  const {c}=ui();let target;c.openEvent=async(...args)=>target=args;
  await c.navigateNotification({read:true,event_id:12,tab:'design',target_kind:'event'});assert.deepEqual(target,[12,'design']);
  target=null;await c.navigateNotification({read:true,event_id:12,target_kind:'inbox'});assert.equal(target,null);assert.equal(c.$('#notification-panel').hidden,false);
});
test('cold-start deep link waits for login and checks API ownership',async()=>{
  const {c}=ui();c.location.href='https://ops.example.test/?notice='+notice;let requests=0;c.api=async()=>{requests++;throw new Error('Forbidden');};
  const original=c.state.data;c.state.data=null;await c.resumeNoticeLink();assert.equal(requests,0);
  c.state.data=original;let cleaned;c.history.replaceState=(a,b,url)=>cleaned=url;await c.resumeNoticeLink();assert.equal(requests,1);assert.equal(cleaned,'/');
});
test('pagination appends unique records and keeps badge total',async()=>{
  const {c}=ui();c.notificationLoading=false;c.state.data.notifications={items:[{key:'notice:1'}],next_before:10,unread_count:4};
  c.api=async()=>({items:[{key:'notice:1'},{key:'notice:2'}],next_before:null,unread_count:4});
  await c.loadOlderNotifications();assert.equal(c.state.data.notifications.items.length,2);assert.equal(c.state.data.notifications.unread_count,4);
});

function worker(){
  const listeners={},shown=[],opened=[],closed=[];let background;
  const self={location:{origin:'https://ops.example.test'},addEventListener:(name,fn)=>listeners[name]=fn,
    skipWaiting(){},clients:{claim(){},matchAll:async()=>[],openWindow:async url=>opened.push(url)},
    registration:{showNotification:async(title,options)=>shown.push({title,options}),getNotifications:async()=>[{close:()=>closed.push(true)}]}};
  const c={self,URL,console,importScripts(url){if(url==='/push-config.js')self.CAPTUREIT_PUSH_CONFIG={ready:true,firebase:{}};},
    firebase:{initializeApp(){},messaging:()=>({onBackgroundMessage:fn=>background=fn})},
    fetch:async()=>({ok:true,json:async()=>({title:'Capture It Ops',body:'Pembaruan akun',notice})})};
  vm.createContext(c);vm.runInContext(source('firebase-messaging-sw.js'),c);
  return {c,listeners,shown,opened,closed,background};
}
test('service worker authenticates before showing generic notification and ignores untrusted URL',async()=>{
  const w=worker();let requested;w.c.fetch=async(url,opts)=>{requested={url,opts};return {ok:true,json:async()=>({title:'Capture It Ops',body:'Pembaruan akun'})};};
  await w.background({data:{notice,url:'https://evil.test'}});assert.equal(w.shown.length,1);assert.equal(requested.opts.credentials,'include');assert.equal(requested.opts.cache,'no-store');
  assert.equal(w.shown[0].options.tag,notice);assert.equal(w.shown[0].options.renotify,false);
  let done;w.listeners.notificationclick({notification:{data:{notice},close(){}},waitUntil:p=>done=p});await done;
  assert.deepEqual(w.opened,['https://ops.example.test/?notice='+notice]);
});
test('service worker suppresses expired session, network failure and malformed payload',async()=>{
  const w=worker();w.c.fetch=async()=>({ok:false});await w.background({data:{notice}});
  w.c.fetch=async()=>{throw Error('offline');};await w.background({data:{notice}});await w.background({data:{notice:'../evil'}});
  assert.equal(w.shown.length,0);
});
test('logout closes notifications and discards an in-flight context response',async()=>{
  const w=worker();let resolve;w.c.fetch=()=>new Promise(r=>resolve=r);const pending=w.background({data:{notice}});
  let done;w.listeners.message({data:{type:'CAPTUREIT_LOGOUT'},waitUntil:p=>done=p});await done;
  resolve({ok:true,json:async()=>({title:'Old account',body:'Should be suppressed'})});await pending;
  assert.equal(w.shown.length,0);assert.equal(w.closed.length,1);
});
