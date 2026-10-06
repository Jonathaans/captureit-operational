/* Private compensation history, fetched on demand and cleared when closed. */
let compensationView = null;
let compensationEpoch = 0;

function clearCompensationHistory() {
  compensationEpoch++;compensationView=null;
  document.getElementById('compensation-body').innerHTML='';
}
function closeCompensationHistory() {
  clearCompensationHistory();
  const dialog=document.getElementById('compensation-dialog');
  if(dialog.open) dialog.close();
}
function renderCompensationHistory(result) {
  const unit=result.kind==='fee'?'per event':'per bulan';
  return `<div class="compensation-summary"><div><small>${escapeHtml(result.user.full_name)}</small><b>${result.current?idr(result.current.new_amount_rupiah):'Belum diatur'} <span>${unit}</span></b><small>Tarif aktif per ${dateLong(result.today)} (WIB)</small></div></div>
    <p class="compensation-note">Urutan pencatatan terbaru. Koreksi menjadi catatan baru; nilai lama tidak dihapus. Tanggal berlaku berbeda dari waktu perubahan dicatat.</p>
    <div class="compensation-timeline">${result.rows.length?result.rows.map(row=>{
      const delta=row.delta_rupiah;
      const changedAt=row.created_at.replace(' ','T')+(/[zZ]$|[+-]\d\d:\d\d$/.test(row.created_at)?'':'Z');
      return `<article class="compensation-entry"><div class="compensation-entry-head"><b>${row.effective_from?`Berlaku ${escapeHtml(row.effective_from)}`:'Tanggal berlaku awal tidak diketahui'}</b>${row.superseded?'<span class="badge badge-gray">Dikoreksi</span>':row.source==='migration'?'<span class="badge badge-gray">Saldo awal migrasi</span>':row.effective_from>result.today?'<span class="badge badge-blue">Terjadwal</span>':''}</div>
        <div class="compensation-amount"><span>${row.old_amount_rupiah===null?'Belum tercatat':idr(row.old_amount_rupiah)}</span><span aria-hidden="true">→</span><strong>${idr(row.new_amount_rupiah)}</strong>${delta===null?'':`<small>${delta>0?'+':delta<0?'−':''}${idr(Math.abs(delta))}</small>`}</div>
        <p>${escapeHtml(row.reason||'Tanpa catatan alasan.')}</p><small>${escapeHtml(row.actor_name||'Pengubah tidak tercatat')} · ${dateLong(changedAt)}, ${timePart(changedAt)} WIB</small></article>`;
    }).join(''):'<div class="empty-state">Belum ada riwayat. Perubahan berikutnya akan tercatat di sini.</div>'}</div>
    ${result.next_cursor?'<button type="button" class="button button-light" data-action="more-compensation-history">Muat riwayat sebelumnya</button>':''}`;
}

async function loadCompensationHistory(more=false) {
  const view=compensationView;
  if(!view||view.loading) return;
  const epoch=compensationEpoch,actorId=state.data?.user.id;
  view.loading=true;
  const error=document.getElementById('compensation-error');error.hidden=true;
  try {
    const result=await api(`/api/compensation-history?kind=${view.kind}&user_id=${view.userId}${more&&view.result?.next_cursor?`&before=${view.result.next_cursor}`:''}`);
    if(epoch!==compensationEpoch||actorId!==state.data?.user.id) return;
    if(more&&view.result) result.rows=[...view.result.rows,...result.rows];
    view.result=result;
    document.getElementById('compensation-body').innerHTML=renderCompensationHistory(result);
  } catch(err) {
    if(epoch!==compensationEpoch||actorId!==state.data?.user.id) return;
    document.getElementById('compensation-body').innerHTML=view.result?renderCompensationHistory(view.result):'';
    error.textContent=err.message;error.hidden=false;
  } finally {view.loading=false;}
}

async function openCompensationHistory(kind,userId) {
  if(!['fee','salary'].includes(kind)||!state.data) return;
  if(!has(kind==='fee'?'fees.manage':'inhouse_payroll.manage')) return;
  clearCompensationHistory();
  compensationView={kind,userId:Number(userId)};
  document.getElementById('compensation-title').textContent=kind==='fee'?'Riwayat fee Crew/PIC':'Riwayat gaji In-house';
  document.getElementById('compensation-body').innerHTML='<p class="empty-state">Memuat riwayat…</p>';
  const dialog=document.getElementById('compensation-dialog');
  if(!dialog.open)dialog.showModal();
  await loadCompensationHistory();
}

document.addEventListener('click',event=>{
  const button=event.target.closest('[data-action]');if(!button)return;
  if(button.dataset.action==='compensation-history')openCompensationHistory(button.dataset.kind,button.dataset.id);
  if(button.dataset.action==='close-compensation-history')closeCompensationHistory();
  if(button.dataset.action==='more-compensation-history')loadCompensationHistory(true);
  if(button.dataset.action==='retry-compensation-history')loadCompensationHistory(false);
});
document.getElementById('compensation-dialog').addEventListener('close',clearCompensationHistory);
document.addEventListener('input',event=>{
  if(event.target.matches('[data-inhouse-salary]'))event.target.dataset.salaryEdited='1';
});
