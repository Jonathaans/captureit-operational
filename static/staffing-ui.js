/* Advisory same-day warnings. A visible warning can be accepted with one submit. */
const staffPreviews = new WeakMap();
let staffPreviewSequence = 0;

function staffingTime(value) {
  return new Intl.DateTimeFormat('id-ID',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Jakarta'}).format(new Date(value));
}

function staffConflictRows(conflicts) {
  return conflicts.map(c=>`<li><b>${escapeHtml(c.title)}</b><span>${escapeHtml(c.project_code)} · ${escapeHtml(c.location||'Lokasi belum diisi')}</span><span>${staffingTime(c.starts_at)} – ${staffingTime(c.ends_at)} WIB</span><small>${c.kind==='overlap'?'Jam event beririsan':(c.shared_days&&c.shared_days.length?`Bentrok di hari kerja: ${c.shared_days.map(d=>typeof dayLabel==='function'?dayLabel(d):d).join(', ')}`:`Event di hari yang sama · selisih waktu ${c.gap_minutes} menit`)} · ${escapeHtml(c.roles.map(r=>r==='pic'?'PIC':'Crew').join(', '))}</small></li>`).join('');
}

function renderStaffConflicts(d) {
  if(!d.staff_conflicts?.length) return '';
  const overlap=d.staff_conflicts.some(p=>p.severity==='red');
  return `<section class="staff-availability ${overlap?'is-blocked':'is-warning'}" role="status"><b>${overlap?'Jam event tim beririsan':'Tim memiliki event lain pada hari yang sama'}</b><p>Peringatan untuk koordinasi, bukan larangan penugasan. Loading tidak dihitung.</p>${d.staff_conflicts.map(p=>`<h4>${escapeHtml(p.full_name)}</h4><ul>${staffConflictRows(p.conflicts)}</ul>`).join('')}</section>`;
}

function hasDayPicker(form) { return typeof form.querySelectorAll==='function'&&form.querySelectorAll('input[name="work_days"]').length>0; }
function staffDays(form) { return hasDayPicker(form)?[...form.querySelectorAll('input[name="work_days"]:checked')].map(el=>el.value):[]; }
function staffPreviewKey(form) { return `${form.dataset.eventId}:${form.elements.user_id.value}:${staffDays(form).join(',')}`; }

function showStaffAvailability(form,result) {
  const panel=form.querySelector('[data-staff-availability]');
  staffPreviews.set(form,{key:staffPreviewKey(form),result});
  panel.className=`staff-availability ${result.severity==='red'?'is-blocked':result.needs_confirmation?'is-warning':'is-ready'}`;
  panel.innerHTML=`<b>${result.severity==='red'?'Jam event beririsan':result.needs_confirmation?'Ada event lain pada hari yang sama':'Tidak ada event lain pada hari yang sama'}</b><p>Event ini: ${staffingTime(result.starts_at)} – ${staffingTime(result.ends_at)} WIB · ${escapeHtml(result.location||'Lokasi belum diisi')}. Loading tidak dihitung.</p>${result.conflicts.length?`<ul>${staffConflictRows(result.conflicts)}</ul><p>Pastikan pembagian tugas memungkinkan. Pilih <b>Tetap Jadwalkan</b> untuk mengonfirmasi sekaligus menyimpan.</p><button type="button" class="button button-light button-small" data-action="change-staff-choice">Ganti Orang</button>`:''}`;
  form.querySelector('button[type="submit"]').textContent=result.needs_confirmation?'Tetap Jadwalkan':'＋ Simpan penugasan';
}

async function checkStaffAvailability(form) {
  const panel=form.querySelector('[data-staff-availability]');
  const key=staffPreviewKey(form),sequence=++staffPreviewSequence;
  staffPreviews.set(form,{key,sequence});
  panel.className='staff-availability';
  form.querySelector('button[type="submit"]').textContent='＋ Simpan penugasan';
  if(!form.elements.user_id.value) {panel.textContent='Pilih staff untuk memeriksa jadwal.';return null;}
  panel.textContent='Memeriksa event lain pada hari yang sama…';
  try {
    const result=await api(`/api/events/${form.dataset.eventId}/staff-availability?user_id=${encodeURIComponent(form.elements.user_id.value)}${hasDayPicker(form)?`&days=${encodeURIComponent(staffDays(form).join(','))}`:''}`);
    if(!form.isConnected||staffPreviews.get(form)?.sequence!==sequence||staffPreviewKey(form)!==key) return null;
    showStaffAvailability(form,result);return result;
  } catch(error) {
    if(form.isConnected&&staffPreviews.get(form)?.sequence===sequence) {
      panel.className='staff-availability is-warning';
      panel.textContent=`Jadwal belum terverifikasi. ${error.message} Tekan Simpan untuk mencoba kembali.`;
    }
    return null;
  }
}

async function saveStaffAssignment(form) {
  if(form.dataset.busy) return;
  const cached=staffPreviews.get(form),key=staffPreviewKey(form);
  const seen=cached?.key===key&&cached.result;
  const data=new FormData(form);
  form.dataset.busy='1';
  const button=form.querySelector('button[type="submit"]');button.disabled=true;
  const errorNode=form.querySelector('[data-assignment-error]');errorNode.hidden=true;
  const disabled=[...form.querySelectorAll('input,select,button')].filter(el=>!el.disabled);
  disabled.forEach(el=>el.disabled=true);
  try {
    const result=seen||await checkStaffAvailability(form);
    if(!result) return;
    if(result.needs_confirmation&&!seen) {
      errorNode.textContent='Periksa peringatan di atas, lalu pilih Tetap Jadwalkan atau Ganti Orang.';errorNode.hidden=false;return;
    }
    const payload={user_id:Number(data.get('user_id')),assignment_type:data.get('assignment_type'),
      position_id:Number(data.get('position_id'))||null,skill_ids:data.getAll('skill_ids').map(Number),...(hasDayPicker(form)?{days:staffDays(form)}:{}),
      schedule_confirmed:result.needs_confirmation,schedule_ack_signature:result.needs_confirmation?result.signature:''};
    await api(`/api/events/${form.dataset.eventId}/assignment`,{method:'POST',body:JSON.stringify(payload)});
    toast('Penugasan berhasil disimpan.');await refreshData();
  } catch(error) {
    if(error.details?.availability) showStaffAvailability(form,error.details.availability);
    errorNode.textContent=error.message;errorNode.hidden=false;
  } finally {
    delete form.dataset.busy;button.disabled=false;disabled.forEach(el=>el.disabled=false);
  }
}

document.addEventListener('change',event=>{
  const form=event.target.closest('#assignment-form');
  if(form&&['user_id','work_days'].includes(event.target.name)&&!form.dataset.busy) {
    if(event.target.name==='work_days'&&!staffDays(form).length) {event.target.checked=true;return;}
    checkStaffAvailability(form);
  }
});
document.addEventListener('click',event=>{
  if(!event.target.closest('[data-action="change-staff-choice"]')) return;
  const form=event.target.closest('#assignment-form');
  if(!form||form.dataset.busy) return;
  form.elements.user_id.value='';staffPreviews.delete(form);
  form.querySelector('[data-assignment-error]').hidden=true;
  checkStaffAvailability(form);form.elements.user_id.focus();
});
