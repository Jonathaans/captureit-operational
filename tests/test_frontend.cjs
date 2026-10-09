/* Pure rendering/logic QA with synthetic DOM: NOT a browser/layout test.
   Run from app folder: node --test tests/test_frontend.cjs */
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const fixtures=JSON.parse(execFileSync(process.env.PYTHON||'python3',[path.join(__dirname,'frontend_fixtures.py')],{cwd:root,encoding:'utf8'}));

function runtime(instant='2026-09-30T05:00:00Z') {
  const nodes=new Map(),storage=new Map();
  const node=()=>({innerHTML:'',textContent:'',value:'',hidden:true,dataset:{},style:{setProperty(){}},
    classList:{add(){},remove(){},toggle(){}},setAttribute(){},addEventListener(){},focus(){},
    showModal(){this.open=true;},close(){this.open=false;},querySelector:()=>null,querySelectorAll:()=>[]});
  const document={cookie:'',addEventListener(){},querySelectorAll:()=>[],documentElement:node(),getElementById(id){return this.querySelector('#'+id);},
    querySelector(selector){
      if(selector==='#event-logistics-form'||selector.startsWith('[data-filter'))return null;
      if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);
    }};
  const RealDate=Date;
  class Clock extends RealDate {constructor(...args){super(...(args.length?args:[instant]));}static now(){return +new RealDate(instant);}}
  const c={console,Intl,Date:Clock,document,navigator:{onLine:true},Headers,URL,URLSearchParams,
    setTimeout,clearTimeout,setInterval:()=>1,clearInterval(){},structuredClone,addEventListener(){},
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  };
  c.window=c;vm.createContext(c);
  for(const file of ['draft-store.js','closing-ui.js','multiday-ui.js','attendance-ui.js','staffing-ui.js','compensation-ui.js','push-ui.js','design-ui.js','operations-ui.js','app.js']) {
    let source=fs.readFileSync(path.join(root,'static',file),'utf8');
    if(file==='app.js')source=source.replace(/api\("\/api\/branding"\)\.then\(applyBranding\)\.catch\(\(\) => \{\}\);\s*loadApp\(\);\s*$/,'');
    vm.runInContext(source,c,{filename:file});
  }
  return {c,nodes,state:vm.runInContext('state',c)};
}

const pages={dashboard:'renderDashboard',events:'renderEventsPage','crm-queue':'renderCrmQueuePage',vehicles:'renderVehiclesPage',design:'renderDesignPage',
  warehouse:'renderWarehousePage',advances:'renderAdvancesPage',payroll:'renderPayrollPage','inhouse-payroll':'renderInhousePayrollPage',
  'inhouse-payslips':'renderInhousePayslipsPage','inhouse-attendance':'renderInhouseAttendancePage',rates:'renderRatesPage',
  kpi:'renderKpiPage',staff:'renderStaffDirectoryPage',profile:'renderProfilePage',configure:'renderConfigurePage'};

for(const fixture of fixtures) test(`real HTTP data renders every visible page and event tab: ${fixture.role}`,async()=>{
  const {c,state}=runtime();state.data=structuredClone(fixture.bootstrap);
  const failures=[];
  c.api=async url=>{
    const route=url.split('?')[0];
    if(!(route in fixture.extra)){failures.push(route);throw new Error('Unexpected API '+route);}
    return structuredClone(fixture.extra[route]);
  };
  c.renderShell();
  const visible=c.navGroups().flatMap(group=>group.items.map(item=>item.id));
  assert.equal(visible.includes('configure'),fixture.role==='administrator');
  for(const page of visible) {
    assert(pages[page],`Missing renderer for ${page}`);
    state.page=page;const html=await c[pages[page]]();
    assert.equal(typeof html,'string');assert(html.length>50,`${fixture.role}/${page}`);
    assert(!html.includes('Unexpected API'),html);
    if(page==='configure')for(const tab of ['appearance','accounts','roles']) {
      state.configureTab=tab;assert(c.renderConfigurePage().length>100);
    }
  }
  for(const detail of fixture.details) {
    state.drawer=structuredClone(detail);
    for(const [tab] of c.drawerTabs()) {
      state.drawerTab=tab;assert.equal(typeof c.renderDrawerTab(state.drawer),'string',`${fixture.role}/${tab}`);
    }
  }
  c.renderNotifications();c.renderNotificationBadge();
  assert.deepEqual(failures,[]);
});

test('business dates, times and month filters use WIB around UTC midnight',()=>{
  const {c,state}=runtime('2026-09-30T18:30:00Z');
  assert.equal(c.localDateISO(),'2026-10-01');
  assert.equal(c.wibDateISO('2026-09-30T18:30:00Z'),'2026-10-01');
  assert.equal(c.timePart('2026-09-30T18:30:00Z'),'01.30');
  assert.equal(c.relativeLabel('2026-10-01T09:00:00+07:00'),'Hari ini');
  assert.equal(JSON.stringify(c.previousInhousePayrollMonth()),JSON.stringify({start:'2026-09-01',end:'2026-09-30',payDate:'2026-10-25'}));
  assert.equal(JSON.stringify(c.currentPayrollWeek()),JSON.stringify({start:'2026-09-26',end:'2026-10-02'}));
  state.data={events:[{title:'Midnight',project_code:'QA',location:'Jakarta',starts_at:'2026-09-30T18:30:00Z',status:'scheduled'}]};
  state.eventMonth='2026-10';assert.equal(c.filteredEvents().length,1);
  assert.equal(JSON.stringify(c.eventMonths(state.data.events)),'["2026-10"]');
});

test('Saturday payroll rollover and January previous-month boundary use WIB',()=>{
  const saturday=runtime('2026-10-02T18:00:00Z').c;
  assert.equal(JSON.stringify(saturday.currentPayrollWeek()),JSON.stringify({start:'2026-10-03',end:'2026-10-09'}));
  const january=runtime('2026-12-31T18:00:00Z').c;
  assert.equal(JSON.stringify(january.previousInhousePayrollMonth()),JSON.stringify({start:'2026-12-01',end:'2026-12-31',payDate:'2027-01-25'}));
});

test('event names and notification text are escaped in HTML output',()=>{
  const fixture=fixtures.find(f=>f.role==='administrator'),{c,state,nodes}=runtime();
  state.data=structuredClone(fixture.bootstrap);state.eventMonth='all';
  const injection='<img src=x onerror=alert(1)>';
  state.data.events[0].title=injection;
  assert(!c.renderEventsPage().includes(injection));assert(c.renderEventsPage().includes('&lt;img'));
  state.data.notifications={unread_count:1,items:[{key:'qa',title:injection,event_title:injection,body:injection,event_at:'2026-09-30T00:00:00Z'}]};
  c.renderNotifications();assert(!nodes.get('#notification-panel').innerHTML.includes(injection));
});
