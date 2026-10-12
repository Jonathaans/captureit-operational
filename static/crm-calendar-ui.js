function crmCalendarEntries() {
  return [...(state.data.calendar_code_queue || []).map(row=>({...row,queue:true})),...(state.data.events || []).filter(row=>row.status!=='cancelled').map(row=>({...row,queue:false}))];
}
function crmLastDate(item) { return wibDateISO(new Date(new Date(item.ends_at).valueOf()-1).toISOString()); }
function crmEventButton(e) { return `<button type="button" class="crm-event ${e.queue || e.schedule_pending ? 'pending' : 'mapped'}" data-action="${e.queue ? 'crm-intake' : 'open-event'}" data-id="${e.id}" title="${escapeHtml(e.title)}"><b>${escapeHtml(e.title)}</b><small>${e.queue ? 'Belum dijadwalkan' : e.schedule_pending ? 'Isi jam manual' : escapeHtml(e.project_code)}</small></button>`; }
function renderCrmCalendar() {
  if(!state.queueMonth || state.queueMonth==='all') state.queueMonth=localDateISO().slice(0,7);
  const [year,month]=state.queueMonth.split('-').map(Number),first=new Date(Date.UTC(year,month-1,1,12)),offset=(first.getUTCDay()+6)%7;
  const start=new Date(first);start.setUTCDate(1-offset);
  const count=Math.ceil((offset+new Date(Date.UTC(year,month,0)).getUTCDate())/7)*7,query=(state.crmSearch || '').toLowerCase();
  const entries=crmCalendarEntries().filter(e=>`${e.title} ${e.project_code || e.suggested_project_code || ''} ${e.location || ''}`.toLowerCase().includes(query));
  const days=Array.from({length:count},(_,i)=>{const date=new Date(start);date.setUTCDate(start.getUTCDate()+i);return date.toISOString().slice(0,10);});
  return `<section class="crm-calendar"><div class="crm-calendar-toolbar"><div class="sales-row-actions"><button class="button button-light" data-action="crm-month-prev" aria-label="Bulan sebelumnya">&lsaquo;</button><input id="crm-calendar-month" class="field-input" type="month" aria-label="Bulan kalender" value="${state.queueMonth}"><button class="button button-light" data-action="crm-month-next" aria-label="Bulan berikutnya">&rsaquo;</button><button class="button button-light" data-action="crm-month-today">Hari ini</button></div><form id="crm-search-form" class="sales-row-actions"><input class="field-input" name="query" aria-label="Cari event Calendar" placeholder="Cari event, kode, lokasi" value="${escapeHtml(state.crmSearch || '')}"><button class="button button-light" type="submit">Cari</button></form></div><div class="crm-calendar-legend"><span>Menunggu kode / jam manual</span><span>Sudah dijadwalkan</span></div><div class="crm-calendar-grid">${['Sen','Sel','Rab','Kam','Jum','Sab','Min'].map(day=>`<div class="crm-weekday">${day}</div>`).join('')}${days.map(day=>`<section class="crm-day ${day.slice(0,7)!==state.queueMonth ? 'outside' : ''} ${day===localDateISO() ? 'today' : ''}"><button class="crm-day-number" data-action="crm-day" data-date="${day}" aria-label="Event ${day}">${Number(day.slice(-2))}</button>${entries.filter(e=>wibDateISO(e.starts_at)<=day && crmLastDate(e)>=day).map(crmEventButton).join('')}</section>`).join('')}</div></section>`;
}
function openCrmIntake(id) {
  const item=(state.data.calendar_code_queue || []).find(row=>row.id===Number(id));if(!item)return toast('Event sudah diproses. Muat ulang kalender.','error');
  const schedule=has('events.assign');
  salesDrawer(item.title,`<form id="crm-schedule-form" class="sales-grid-form"><input name="id" type="hidden" value="${item.id}"><p class="sales-full">${escapeHtml(item.location || 'Lokasi belum diisi')}</p>${salesInput('Kode project CRM','project_code',item.suggested_project_code || '','text',`maxlength="50" pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,49}" ${schedule ? 'placeholder="Opsional; kode sementara akan dibuat"' : 'required'}`)}${schedule ? `<label class="field-label">Durasi<select class="field-input" name="is_full_day"><option value="0">Non-full day</option><option value="1">Full day</option></select></label>${salesInput('Tanggal mulai','start_date',wibDateISO(item.starts_at),'date','required')}${salesInput('Jam mulai (WIB)','start_time','','time','required')}${salesInput('Tanggal selesai','end_date',crmLastDate(item),'date','required')}${salesInput('Jam selesai (WIB)','end_time','','time','required')}` : ''}<p class="inline-form-error sales-full" role="alert" hidden></p><div class="sales-full drawer-actions"><button class="button button-primary" type="submit">${schedule ? 'Simpan & jadwalkan' : 'Pasangkan kode'}</button></div></form>`);
}
function eventScheduleForm(d) {
  if(!has('events.assign') || d.status!=='scheduled')return '';
  const locked=d.assignments.some(a=>a.attendance_locked) || ['submitted','accepted'].includes(d.closing_report?.status),localValue=v=>new Date(new Date(v).valueOf()+7*3600000).toISOString().slice(0,16);
  return `<form id="event-schedule-form" data-event-id="${d.id}" class="sales-grid-form event-schedule-form"><fieldset class="sales-grid-form sales-full" ${locked ? 'disabled' : ''}>${salesInput('Mulai event (WIB)','starts_at',d.schedule_pending ? '' : localValue(d.starts_at),'datetime-local','required')}${salesInput('Selesai event (WIB)','ends_at',d.schedule_pending ? '' : localValue(d.ends_at),'datetime-local','required')}${!locked ? '<button class="button button-light" type="submit">Simpan jam manual</button>' : '<p>Jadwal terkunci: absensi atau laporan sudah tercatat.</p>'}</fieldset><p class="inline-form-error sales-full" role="alert" hidden></p></form>`;
}
async function handleCrmCalendarAction(action,element) {
  if(action==='crm-intake'){openCrmIntake(element.dataset.id);return true;}
  if(action==='crm-day'){const day=element.dataset.date,rows=crmCalendarEntries().filter(e=>wibDateISO(e.starts_at)<=day && crmLastDate(e)>=day);salesDrawer(`Event ${day}`,rows.map(crmEventButton).join('') || '<p>Tidak ada event pada tanggal ini.</p>');return true;}
  if(['crm-month-prev','crm-month-next','crm-month-today'].includes(action)){const date=new Date(`${state.queueMonth}-01T12:00:00Z`);date.setUTCMonth(date.getUTCMonth()+(action==='crm-month-prev' ? -1 : 1));state.queueMonth=action==='crm-month-today' ? localDateISO().slice(0,7) : date.toISOString().slice(0,7);renderPage();return true;}
  return false;
}
document.addEventListener('change',event=>{if(event.target.id==='crm-calendar-month' && /^\d{4}-\d{2}$/.test(event.target.value)){state.queueMonth=event.target.value;renderPage();}});
document.addEventListener('submit',async event=>{
  const form=event.target;if(form.id==='crm-search-form'){event.preventDefault();state.crmSearch=new FormData(form).get('query');renderPage();return;}
  if(!['crm-schedule-form','event-schedule-form'].includes(form.id))return;event.preventDefault();if(form.dataset.busy==='1')return;form.dataset.busy='1';
  const button=form.querySelector('button[type=submit]'),errorNode=form.querySelector('.inline-form-error');button.disabled=true;errorNode.hidden=true;
  try {const p=Object.fromEntries(new FormData(form));let result;
    if(form.id==='event-schedule-form')result=await api(`/api/events/${form.dataset.eventId}/schedule`,{method:'POST',body:JSON.stringify(p)});
    else {const schedule=has('events.assign');if(schedule){p.starts_at=`${p.start_date}T${p.start_time}`;p.ends_at=`${p.end_date}T${p.end_time}`;p.is_full_day=p.is_full_day==='1';}result=await api(schedule ? `/api/calendar-queue/${p.id}/promote` : `/api/calendar-code/${p.id}`,{method:'POST',body:JSON.stringify(p)});}
    $('#modal-root').innerHTML='';state.drawer=null;await refreshData();if(has('events.read_all'))await openEvent(result.event_id);toast('Jadwal tersimpan.');
  }catch(error){errorNode.textContent=error.message;errorNode.hidden=false;}finally{form.dataset.busy='0';button.disabled=false;}
});
