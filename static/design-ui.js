/* Explicit brief submission. Merely opening/editing the form never sends a notice. */
function designLocalInput(value) {
  if(!value)return '';
  const d=new Date(value);if(Number.isNaN(+d))return '';
  return new Date(+d+7*3600000).toISOString().slice(0,16);
}
function approvedDesignLink(d) {
  try {const url=new URL(d.approved_design_url);if(url.protocol==='https:'&&!url.username&&!url.password)return `<section class="design-brief-card"><h3>Desain sudah disetujui</h3><a class="button button-light" href="${escapeHtml(url.href)}" target="_blank" rel="noopener noreferrer">Buka hasil desain ↗</a></section>`;}catch{}
  return '';
}
function renderDesignForms(d) {
  const task=d.design_task||(state.data.design_tasks||[]).find(t=>t.event_id===d.id);
  if(!task)return d.design_restricted?'<p class="push-help">Tugas desain event ini ditangani designer lain.</p>':'<p class="push-help">Tugas desain belum tersedia.</p>';
  // Only Head Design / Admin / Head Ops / the responsible Coordinator brief and pick the designer; Team Design just works the card.
  const editor=has('design.assign')&&(has('design.update')||(has('events.assign')&&(d.coordinator_id===state.data.user.id||state.data.user.roles.some(r=>['administrator','head_operations'].includes(r.code)))));
  const editable=editor&&d.status==='scheduled';
  const notes=(d.design_notes||[]).map(n=>`<div class="design-note"><p>${escapeHtml(n.body)}</p><small>${escapeHtml(n.author_name||'Pengguna')} · ${escapeHtml(attendanceTimeLabel(n.created_at))}</small></div>`).join('');
  const brief=`<section class="design-brief-card"><h3>Brief desain</h3><p class="push-help">${task.brief_sent_at?`Terkirim · versi ${Number(task.brief_version)} · ${escapeHtml(attendanceTimeLabel(task.brief_sent_at))}`:'Isi kebutuhan desain, pilih designer, lalu kirim. Designer hanya melihat kartu yang ditugaskan kepadanya.'}</p>
    ${editable?`<form id="design-brief-form" data-task-id="${task.id}" data-version="${Number(task.brief_version||0)}"><label class="field-label">Kebutuhan desain<textarea class="field-input" name="brief_text" rows="6" maxlength="10000" required placeholder="Tema, teks, ukuran, referensi, serta tautan bahan…">${escapeHtml(task.brief_text||'')}</textarea></label>
    <label class="field-label">Designer<select class="field-input" name="assignee_id"><option value="">Belum dipilih (hanya Head Design yang melihat)</option>${(state.data.designers||[]).map(u=>`<option value="${u.id}" ${u.id===task.assignee_id?'selected':''}>${escapeHtml(u.full_name)}</option>`).join('')}</select></label>
    <label class="field-label">Tenggat · WIB (opsional)<input class="field-input" type="datetime-local" name="due_at" value="${escapeHtml(designLocalInput(task.due_at))}"></label><p class="inline-form-error" role="alert" hidden></p>
    <button class="button button-primary" type="submit">${task.brief_sent_at?'Kirim pembaruan brief':'Kirim brief'}</button><small class="push-help">Notifikasi dikirim setelah brief tersimpan. Pengiriman isi yang sama tidak mengirim ulang.</small></form>`:
    `<p class="design-brief-text">${escapeHtml(task.brief_text||'Belum ada brief yang dikirim.')}</p>`}</section>`;
  const status=has('design.update')&&d.status==='scheduled'?`<section class="design-brief-card"><h3>Perbarui desain</h3><form id="design-status-form" data-task-id="${task.id}">
    <label class="field-label">Tahap<select class="field-input" name="status">${designStages.map(([code,label])=>`<option value="${code}" ${code===task.status?'selected':''}>${label}</option>`).join('')}</select></label>
    <label class="field-label">Catatan / arahan revisi<textarea class="field-input" name="note" rows="3" maxlength="2000" placeholder="Tuliskan bagian yang perlu diperbaiki atau informasi untuk coordinator."></textarea></label>
    <label class="field-label">Tautan hasil desain (opsional)<input class="field-input" name="final_url" type="url" pattern="https://.*" maxlength="2048" value="${escapeHtml(task.final_url||'')}" placeholder="https://…"></label>
    <p class="inline-form-error" role="alert" hidden></p><button class="button button-primary" type="submit">Simpan tahap desain</button></form></section>`:'';
  let link='';try {const url=new URL(task.final_url);if(url.protocol==='https:'&&!url.username&&!url.password)link=`<a class="button button-light" href="${escapeHtml(url.href)}" target="_blank" rel="noopener noreferrer">Buka hasil desain ↗</a>`;}catch{}
  return brief+status+link+(notes?`<section class="design-brief-card"><h3>Catatan terbaru</h3>${notes}</section>`:'');
}
document.addEventListener('submit',async event=>{
  const form=event.target;if(!['design-brief-form','design-status-form'].includes(form.id))return;
  event.preventDefault();if(form.dataset.busy||!form.reportValidity())return;
  form.dataset.busy='1';const button=form.querySelector('[type="submit"]'),error=form.querySelector('[role="alert"]');button.disabled=true;error.hidden=true;
  const uid=state.data.user.id;
  try {
    const data=Object.fromEntries(new FormData(form)),brief=form.id==='design-brief-form';
    if(brief){data.assignee_id=data.assignee_id?Number(data.assignee_id):null;data.version=Number(form.dataset.version);if(data.due_at)data.due_at+=':00+07:00';}
    await api(`/api/design/${Number(form.dataset.taskId)}/${brief?'brief':'status'}`,{method:'POST',body:JSON.stringify(data)});
    if(state.data?.user.id===uid){toast(brief?'Brief tersimpan; pemberitahuan dibuat untuk penerima terkait.':'Tahap desain diperbarui.');await refreshData();}
  }catch(exc){if(state.data?.user.id===uid){error.textContent=exc.message;error.hidden=false;}}
  finally{delete form.dataset.busy;button.disabled=false;}
});

/* Team Design only sees the cards assigned to them; everyone else with design access sees every card. */
function canSeeDesignStatus(event) {
  return has('design.read') && (has('design.read_all') || event.design_assignee_id === state.data.user.id);
}
