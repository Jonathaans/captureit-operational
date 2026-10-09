/* Synthetic-DOM checks for punctuality labels, crew event-date rules and the hours settings form (not a browser test).
   Run: node --test tests/test_attendance_ui.cjs */
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=f=>fs.readFileSync(path.resolve(__dirname,'../static/'+f),'utf8');

function load(){
  const state={data:{user:{id:1},inhouse_schedule:{work_start:'09:00',work_end:'18:00',late_grace_minutes:0,early_grace_minutes:0}}};
  const c={Intl,Date,console,state,document:{addEventListener(){}},escapeHtml:s=>String(s),has:()=>true};
  vm.createContext(c);vm.runInContext(read('multiday-ui.js'),c);vm.runInContext(read('attendance-ui.js'),c);return c;
}
const schedule={late_grace_minutes:0,early_grace_minutes:0};

test('late and early minutes show as badges, on time shows green, old records show nothing',()=>{
  const c=load();
  const late=c.punctualityBadges({late_minutes:60,early_leave_minutes:null},schedule);
  assert.match(late,/Terlambat 60 menit/);assert.match(late,/badge-red/);
  const early=c.punctualityBadges({late_minutes:0,early_leave_minutes:30},schedule);
  assert.match(early,/Pulang lebih awal 30 menit/);assert.doesNotMatch(early,/Terlambat/);
  assert.match(c.punctualityBadges({late_minutes:0,early_leave_minutes:0},schedule),/Tepat waktu/);
  assert.equal(c.punctualityBadges({late_minutes:null,early_leave_minutes:null},schedule),'');   // recorded before office hours existed
});

test('grace minutes hide small deviations',()=>{
  const c=load();
  assert.match(c.punctualityBadges({late_minutes:10},{late_grace_minutes:15,early_grace_minutes:0}),/Tepat waktu/);
  assert.match(c.punctualityBadges({late_minutes:16},{late_grace_minutes:15,early_grace_minutes:0}),/Terlambat 16 menit/);
});

test('crew event buttons: check-in only on the event date, check-out also the next day',()=>{
  const c=load();
  const ev={event_days:['2026-10-03']};
  assert.deepEqual(JSON.parse(JSON.stringify(c.eventDayRule(ev,'2026-10-03'))),{canCheckIn:true,canCheckOut:true,window:'2026-10-03'});
  assert.equal(c.eventDayRule(ev,'2026-10-02').canCheckIn,false);
  assert.equal(c.eventDayRule(ev,'2026-10-04').canCheckIn,false);
  assert.equal(c.eventDayRule(ev,'2026-10-04').canCheckOut,true);     // shift that ended past midnight
  assert.equal(c.eventDayRule(ev,'2026-10-05').canCheckOut,false);
  assert.equal(c.eventDayRule({event_days:['2026-10-03','2026-10-04']},'2026-10-04').window,'2026-10-03 s/d 2026-10-04');
});

test('hours settings form shows the saved schedule',()=>{
  const c=load();
  const html=c.renderInhouseScheduleSettings();
  assert.match(html,/data-inhouse-schedule-form/);assert.match(html,/name="work_start" value="09:00"/);assert.match(html,/name="work_end" value="18:00"/);
  c.state.data.inhouse_schedule={work_start:'08:30',work_end:'17:30',late_grace_minutes:10,early_grace_minutes:5};
  assert.match(c.renderInhouseScheduleSettings(),/value="08:30"/);assert.match(c.renderInhouseScheduleSettings(),/value="10"/);
});

test('design: staff only see status badges for their own cards',()=>{
  const c=load();vm.runInContext(read('design-ui.js'),c);
  c.has=p=>p==='design.read';                      // Team Design: no design.read_all
  assert.equal(c.canSeeDesignStatus({design_assignee_id:1}),true);
  assert.equal(c.canSeeDesignStatus({design_assignee_id:2}),false);
  assert.equal(c.canSeeDesignStatus({design_assignee_id:null}),false);
  c.has=p=>['design.read','design.read_all'].includes(p);   // Head Design / managers
  assert.equal(c.canSeeDesignStatus({design_assignee_id:2}),true);
});
