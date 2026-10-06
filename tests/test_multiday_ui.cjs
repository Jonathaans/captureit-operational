/* Synthetic-DOM render check for the multi-day crew mapping UI (not a browser/layout test).
   Run: node --test tests/test_multiday_ui.cjs */
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const code=fs.readFileSync(path.resolve(__dirname,'../static/multiday-ui.js'),'utf8');

function load(permissions,userId=5){
  const c={Intl,Date,console,document:{addEventListener(){}},escapeHtml:s=>String(s),
    has:p=>permissions.includes(p),attendanceTimeLabel:v=>String(v),state:{data:{user:{id:userId}}}};
  vm.createContext(c);vm.runInContext(code,c);return c;
}
const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Jakarta'}).format(new Date());
const event={id:3,status:'scheduled',multi_day:true,event_days:['2040-06-01','2040-06-02']};
const mk=(user_id,days)=>({assignment_id:9,user_id,days});

test('day picker only exists for multi-day events and ticks every day',()=>{
  const c=load([]);
  assert.equal(c.renderDayPicker({multi_day:false,event_days:['x']}),'');
  const html=c.renderDayPicker(event);
  assert.equal((html.match(/name="work_days"/g)||[]).length,2);assert.equal((html.match(/checked/g)||[]).length,2);
});

test('manager sees day editor, locked days are disabled, absent can be marked',()=>{
  const c=load(['events.assign','attendance.manage']);
  const html=c.renderAssignmentDays(event,mk(7,[{work_date:'2040-06-01',status:'checked_out'},{work_date:'2040-06-02',status:'not_started'}]));
  assert.match(html,/data-days-form/);assert.match(html,/disabled/);assert.match(html,/data-action="mark-absent"[^>]*data-work-date="2040-06-02"/);
});

test('crew only gets check-in for their own mapped day today',()=>{
  const c=load(['attendance.self'],5);
  const mine=c.renderAssignmentDays(event,mk(5,[{work_date:today,status:'not_started'},{work_date:'2040-06-02',status:'not_started'}]));
  assert.equal((mine.match(/data-attendance-action="check_in"/g)||[]).length,1);assert.match(mine,new RegExp(`data-work-date="${today}"`));
  assert.doesNotMatch(mine,/data-days-form/);
  const other=c.renderAssignmentDays(event,mk(8,[{work_date:today,status:'not_started'}]));
  assert.doesNotMatch(other,/check_in/);
  assert.equal(c.renderAssignmentDays({...event,multi_day:false},mk(5,[])),'');
});

test('queue label shows the whole range for multi-day events only',()=>{
  const c=load([]);
  c.dateLong=v=>new Intl.DateTimeFormat('id-ID',{day:'numeric',month:'long',timeZone:'Asia/Jakarta'}).format(new Date(v));
  c.timePart=v=>new Intl.DateTimeFormat('id-ID',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Jakarta'}).format(new Date(v));
  const clayfest=c.eventRangeLabel('2026-10-03T10:30:00+07:00','2026-10-04T11:30:00+07:00');
  assert.match(clayfest,/3 Oktober/);assert.match(clayfest,/4 Oktober/);assert.match(clayfest,/2 hari/);
  assert.doesNotMatch(c.eventRangeLabel('2026-10-03T04:45:00+07:00','2026-10-03T05:45:00+07:00'),/hari/);
  assert.doesNotMatch(c.eventRangeLabel('2026-10-03T20:00:00+07:00','2026-10-04T04:00:00+07:00'),/hari/);   // overnight shift
  assert.match(c.eventRangeLabel('2026-10-03T00:00:00+07:00','2026-10-04T23:59:00+07:00'),/2 hari/);
});

test('correction form: only for others, past or today, unfinished days, with permission',()=>{
  const past='2020-01-01',future='2999-01-01';
  const ev={...event,id:3,status:'scheduled'};
  const mkDay=(work_date,status,extra={})=>({work_date,status,needs_action:false,...extra});
  const row=(uid,days)=>({assignment_id:9,user_id:uid,days});
  const coordinator=load(['attendance.correct','attendance.manage','events.assign'],1);
  const html=coordinator.renderAssignmentDays(ev,row(7,[mkDay(past,'not_started',{needs_action:true}),mkDay(past,'checked_out'),mkDay(future,'not_started')]));
  assert.equal((html.match(/data-correction-form/g)||[]).length,1);          // only the unfinished past day
  assert.match(html,/Perlu tindakan/);assert.match(html,/data-work-date="2020-01-01"/);
  assert.doesNotMatch(coordinator.renderAssignmentDays(ev,row(1,[mkDay(past,'not_started')])),/data-correction-form/);   // own attendance
  const pic=load(['attendance.manage'],2);                                       // no attendance.correct
  assert.doesNotMatch(pic.renderAssignmentDays(ev,row(7,[mkDay(past,'not_started')])),/data-correction-form/);
  assert.doesNotMatch(coordinator.renderAssignmentDays({...ev,status:'completed'},row(7,[mkDay(past,'not_started')])),/data-correction-form/);
});

test('single-day correction and the needs-action banner',()=>{
  const c=load(['attendance.correct','attendance.manage'],1);
  const single={id:4,status:'scheduled',multi_day:false,event_days:['2020-01-01'],attendance_needs_action:2};
  const html=c.renderSingleDayCorrection(single,{assignment_id:5,user_id:9,attendance_status:'not_started',needs_action:true});
  assert.match(html,/Perlu tindakan/);assert.match(html,/data-correction-form/);assert.doesNotMatch(html,/data-work-date/);
  assert.match(c.renderAttendanceBanner(single),/2 crew belum terselesaikan/);
  assert.equal(load(['events.assign']).renderAttendanceBanner(single),'');
  assert.match(c.renderSingleDayCorrection(single,{assignment_id:5,user_id:9,attendance_status:'absent',corrected_at:'x',correction_note:'lupa'}),/Dikoreksi pengelola · lupa/);
});

test('manual event panel: only for people who can schedule, defaults span three days',()=>{
  assert.equal(load([]).renderManualEventPanel(),'');
  const html=load(['events.assign']).renderManualEventPanel();
  assert.match(html,/data-manual-event-form/);assert.match(html,/Buat event manual/);
  const [a,b]=[...html.matchAll(/type="datetime-local" name="(?:starts_at|ends_at)" value="([^"]+)"/g)].map(m=>new Date(m[1]));
  assert.ok(b>a&&(b-a)/86400000>1.5);   // default range is long enough to try day mapping
});
