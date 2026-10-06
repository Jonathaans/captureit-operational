/* Drafts belong to a workspace, account, and event; logout only clears memory. */
const closingDrafts = new Map();
const closingMaterials = [['frame','Frame'],['lenticular','Lensa lenticular'],['magnet','Magnet'],['keychain','Keychain']];
const closingLabels = {not_started:['Belum diisi','gray'],draft:['Draft','gray'],submitted:['Menunggu review','gold'],revision:['Perlu revisi','red'],accepted:['Diterima','green']};
const closingActionLabels = {draft:'Menyimpan draft',submit:'Mengirim laporan',accept:'Menerima laporan',revise:'Meminta revisi'};

function closingDraft(d) {
  const r=d.closing_report;
  const key=closingDraftKey(d);
  let draft=closingDrafts.get(key);
  if (!draft || (!draft.dirty && draft.version!==r.version)) {
    draft={version:r.version,data:{ribbon_rolls:[],materials:{},...structuredClone(r.data)},photos:structuredClone(r.photos),token:null,dirty:false};
    closingDrafts.set(key,draft);
  }
  if(draft.dirty&&(draft.version!==r.version||!r.can_edit)) draft.conflict='server';
  return draft;
}

function closingDraftKey(d,session=state.data) {
  return deviceDraftStore.key(session.draft_namespace,session.user.id,d.id);
}

async function prepareClosingDraft(d,session) {
  if(!d.closing_report) return;
  const key=closingDraftKey(d,session);
  if(closingDrafts.has(key)) return;
  try {
    const record=deviceDraftStore.read(key);
    if(record) {
      const draft=await deviceDraftStore.hydrate(key,record);
      const available=new Set(d.closing_report.photos.map(p=>p.id));
      draft.photos=draft.photos.map(p=>p.id&&!available.has(p.id)?{...p,missing:true}:p);
      if(draft.photos.some(p=>p.missing)) draft.storageError='Ada foto draft yang tidak tersedia. Tambahkan ulang atau hapus foto tersebut.';
      if(state.data?.user.id===session.user.id&&state.data?.draft_namespace===session.draft_namespace&&!closingDrafts.has(key)) closingDrafts.set(key,draft);
    }
  } catch(error) {
    if(state.data?.user.id===session.user.id) {
      const r=d.closing_report;
      closingDrafts.set(key,{version:r.version,data:{ribbon_rolls:[],materials:{},...structuredClone(r.data)},photos:structuredClone(r.photos),token:null,storageError:'Draft otomatis belum tersedia. '+error.message});
    }
  }
}

function persistClosingDraft(draft,d=state.drawer) {
  if(!draft||!d||draft.conflict||draft.saving) return;
  const key=closingDraftKey(d);
  draft.dirty=true;
  try {
    const saved=deviceDraftStore.write(key,draft,draft.token);
    draft.token=saved.record.token;draft.savedAt=saved.record.updated_at;draft.photo_ids=saved.record.photo_ids;
    draft.storageError='';draft.persisting=true;
    const token=draft.token;
    draft.persistPromise=saved.ready.then(()=>{
      if(draft.token===token) {draft.persisting=false;draft.storageError='';updateClosingDraftStatus();}
    }).catch(()=>{
      if(draft.token===token) {draft.persisting=false;draft.storageError='Teks tersimpan di perangkat, tetapi foto belum tersimpan. Simpan ke server atau coba kembali sebelum menutup halaman.';updateClosingDraftStatus();}
    });
  } catch(error) {
    draft.persisting=false;
    if(error.code==='draft_conflict') {draft.conflict='tab';if(state.drawerTab==='closing') renderDrawer();}
    else draft.storageError='Draft belum tersimpan di perangkat. Penyimpanan mungkin penuh atau dibatasi. Simpan ke server sebelum menutup halaman.';
  }
  updateClosingDraftStatus();
}

function closingDraftStatus(draft) {
  if(draft.saving) return 'Menyimpan laporan ke server…';
  if(draft.storageError) return draft.storageError;
  if(draft.photosBusy) return 'Memproses foto; tunggu sampai draft tersimpan di perangkat.';
  if(draft.persisting) return 'Menyimpan foto draft di perangkat…';
  if(!draft.dirty) return draft.version?'Versi tersimpan di server. Perubahan berikutnya disimpan otomatis di perangkat.':'Isian akan disimpan otomatis di perangkat ini.';
  const stamp=draft.savedAt?new Intl.DateTimeFormat('id-ID',{hour:'2-digit',minute:'2-digit'}).format(new Date(draft.savedAt)):'';
  return `${navigator.onLine===false?'Offline · ':draft.restored?'Draft dipulihkan · ':''}Tersimpan di perangkat${stamp?' '+stamp:''} · belum disimpan ke server`;
}

function updateClosingDraftStatus() {
  const node=$('#closing-draft-status');
  if(!node||!state.drawer||!state.data) return;
  const draft=closingDraft(state.drawer);
  node.textContent=closingDraftStatus(draft);node.className=`draft-status${draft.storageError?' is-error':''}`;
}

function closingDraftPreview(draft) {
  const v=draft.data;
  const lines=[['Mulai aktual',v.actual_start],['Selesai aktual',v.actual_end],['Hasil layanan',{planned:'Sesuai rencana',changed:'Ada perubahan'}[v.service_result]],['Catatan layanan',v.service_note],['Kendala',v.issues],['Penanganan',v.resolution],['Tindak lanjut',v.follow_up],['Total cetak',v.prints_total],['Cetak gagal',v.prints_failed],...closingMaterials.map(([key,label])=>[label,v.materials?.[key]])];
  for(const [i,roll] of (v.ribbon_rolls||[]).entries()) lines.push([`Ribbon roll ${i+1}`,`${roll.start??'—'} → ${roll.end??'—'}`]);
  return lines.filter(([,value])=>value!=null&&value!=='').map(([label,value])=>`${label}: ${value}`).join('\n')||'Belum ada isian teks.';
}

function closingRecoveryHtml(d,draft) {
  if(!draft.conflict) return '';
  const tab=draft.conflict==='tab';
  return `<section class="draft-recovery" role="status"><b>${tab?'Draft berubah di tab lain':'Ada draft perangkat yang belum tersimpan'}</b><p>${tab?'Pilih isian yang ingin dilanjutkan. Isian dari tab ini ditampilkan di bawah.':'Laporan di server sudah berubah atau terkunci. Draft perangkat tetap tersedia untuk diperiksa.'}</p><details><summary>Lihat isi draft perangkat</summary><pre>${escapeHtml(closingDraftPreview(draft))}</pre><div class="closing-photos">${closingPhotosHtml(draft.photos,false)}</div></details><div class="draft-recovery-actions">${d.closing_report.can_edit?`<button type="button" class="button button-primary button-small" data-action="closing-recover">${tab?'Pakai isian ini':'Pulihkan draft ke form'}</button>`:''}<button type="button" class="button button-light button-small" data-action="${tab?'closing-load-other':'closing-discard'}">${tab?'Muat draft tab lain':d.closing_report.can_edit?'Gunakan versi server':'Hapus draft perangkat'}</button></div></section>`;
}

function closingRollHtml(roll,index,editable) {
  return `<div class="closing-roll"><b>Roll ${index+1}</b><label class="field-label">Sisa awal<input class="field-input" type="number" min="0" max="999999999" step="1" data-roll-start value="${escapeHtml(roll.start??'')}"></label><label class="field-label">Sisa akhir<input class="field-input" type="number" min="0" max="999999999" step="1" data-roll-end value="${escapeHtml(roll.end??'')}"></label>${editable?`<button type="button" class="text-link" data-action="closing-remove-roll" data-index="${index}" aria-label="Hapus roll ${index+1}">Hapus</button>`:''}</div>`;
}

function renderClosingTab(d) {
  const r=d.closing_report;
  if (!r) return '<p class="empty-state">Laporan belum tersedia. Muat ulang halaman.</p>';
  const local=closingDraft(d),editable=r.can_edit&&!local.conflict&&!local.saving;
  const draft=r.can_edit&&!local.conflict?local:{data:r.data,photos:r.photos};
  const v=draft.data,ctx=['submitted','accepted'].includes(r.status)?r.context:{};
  const team=ctx.team||d.assignments;
  const text=(key,label,placeholder='')=>`<label class="field-label">${label}<textarea class="field-input" name="${key}" rows="3" maxlength="4000" placeholder="${placeholder}">${escapeHtml(v[key]||'')}</textarea></label>`;
  const step=['not_started','draft','revision'].includes(r.status)?0:r.status==='submitted'?1:d.status==='completed'?3:2;
  const transport=ctx.transport_label??d.logistics?.transport_label??ctx.vehicle_name??d.logistics?.vehicle_name??'Belum diisi';
  return `<section class="closing-header"><span class="eyebrow-dark">LAPORAN PENUTUPAN</span><div><h3>Hasil pelaksanaan event</h3>${badge(...(closingLabels[r.status]||closingLabels.not_started))}</div><p>PIC mencatat hasil event, lalu coordinator memeriksa sebelum Clear & Evaluasi.</p></section>
    <ol class="closing-progress" aria-label="Tahapan penutupan">${['Draft PIC','Review','Diterima','Clear & Evaluasi'].map((label,i)=>`<li class="${i<step?'done':i===step?'current':''}"><span>${i<step?'✓':i+1}</span>${label}</li>`).join('')}</ol>
    <section class="closing-context"><div><small>Event & jadwal</small><b>${escapeHtml(ctx.title||d.title)}</b><span>${escapeHtml(ctx.project_code||d.project_code)} · ${dateLong(ctx.starts_at||d.starts_at)} · ${timePart(ctx.starts_at||d.starts_at)}–${timePart(ctx.ends_at||d.ends_at)} WIB</span></div><div><small>Tim</small><span>${team.map(a=>`${escapeHtml(a.full_name)} (${a.assignment_type==='pic'?'PIC':'Crew'})`).join(', ')||'Belum ditugaskan'}</span></div><div><small>Transportasi</small><span>${escapeHtml(transport||'Belum diisi')}${(ctx.driver_name??d.logistics?.driver_name)?' · Driver: '+escapeHtml(ctx.driver_name??d.logistics.driver_name):''}</span></div></section>
    ${r.review_note?`<div class="closing-review-note ${r.status==='revision'?'needs-revision':''}"><b>Catatan coordinator</b><p>${escapeHtml(r.review_note)}</p></div>`:''}
    ${closingRecoveryHtml(d,local)}
    ${r.can_edit&&!local.conflict?`<p id="closing-draft-status" class="draft-status${local.storageError?' is-error':''}" role="status" aria-live="polite">${escapeHtml(closingDraftStatus(local))}</p>`:''}
    ${!editable&&r.status==='not_started'?`<div class="closing-info">${d.status==='completed'?'Event ini sudah clear sebelum memakai laporan penutupan. Riwayat evaluasi tetap tersedia.':d.status==='cancelled'?'Event dibatalkan.':'Laporan diisi oleh PIC yang ditugaskan pada event ini.'}</div>`:''}
    ${r.status==='submitted'?'<div class="closing-info">Laporan sudah dikirim. Isi terkunci selama menunggu review coordinator.</div>':''}
    ${r.status!=='not_started'||editable?`<form id="closing-form" data-event-id="${d.id}" novalidate><fieldset class="closing-fieldset" ${editable?'':'disabled'}>
      <h4><span>01</span> Pelaksanaan</h4><div class="closing-grid"><label class="field-label">Mulai aktual (WIB)<input class="field-input" name="actual_start" type="datetime-local" value="${escapeHtml((v.actual_start||'').slice(0,16))}"></label><label class="field-label">Selesai aktual (WIB)<input class="field-input" name="actual_end" type="datetime-local" value="${escapeHtml((v.actual_end||'').slice(0,16))}"></label></div>
      <label class="field-label">Layanan sesuai rencana?<select class="field-input" name="service_result"><option value="">Pilih hasil pelaksanaan</option><option value="planned" ${v.service_result==='planned'?'selected':''}>Sesuai rencana</option><option value="changed" ${v.service_result==='changed'?'selected':''}>Ada perubahan</option></select></label>${text('service_note','Catatan layanan','Jelaskan perubahan jam, paket, atau layanan bila ada.')}
      <section class="closing-materials"><div class="closing-materials-heading"><h4><span>02</span> Pemakaian bahan</h4><span class="optional-label">Semua opsional</span></div><p class="closing-hint">Isi hanya bahan yang dipakai. Kolom kosong tidak menghalangi pengiriman laporan.</p>
      <details class="material-ribbon" ${(v.ribbon_rolls||[]).length?'open':''}><summary><span><b>Ribbon</b><small>Catat per roll bila digunakan</small></span><span class="material-ribbon-summary">${closingRibbonTotal(v)==='—'?'Belum diisi':closingRibbonTotal(v)+' cetak'}</span><span aria-hidden="true">⌄</span></summary><div class="material-ribbon-body"><p class="closing-hint">Sisa kapasitas cetak awal − akhir. Tambahkan baris saat berganti roll.</p><div id="closing-rolls">${(v.ribbon_rolls||[]).map((roll,i)=>closingRollHtml(roll,i,editable)).join('')}</div>${editable?'<button class="button button-light button-small" type="button" data-action="closing-add-roll">+ Tambah roll ribbon</button>':''}<div class="closing-total"><span>Total pemakaian ribbon</span><output id="closing-ribbon-total">${closingRibbonTotal(v)}</output></div></div></details>
      <div class="materials-grid">${closingMaterials.map(([key,label])=>`<label class="material-field"><span>${label}</span><div><input class="field-input" name="material_${key}" type="number" min="0" max="999999999" step="1" value="${escapeHtml(v.materials?.[key]??'')}" placeholder="Jumlah terpakai"><span>pcs</span></div></label>`).join('')}</div>
      <details class="closing-print-summary" ${v.prints_total!=null||v.prints_failed!=null?'open':''}><summary>Rekap cetak <span>Opsional</span></summary><div class="closing-grid"><label class="field-label">Total cetak, termasuk gagal<input class="field-input" name="prints_total" type="number" min="0" max="999999999" step="1" value="${escapeHtml(v.prints_total??'')}" placeholder="Belum diisi"></label><label class="field-label">Cetak gagal<input class="field-input" name="prints_failed" type="number" min="0" max="999999999" step="1" value="${escapeHtml(v.prints_failed??'')}" placeholder="Belum diisi"></label></div></details></section>
      <h4><span>03</span> Kendala & tindak lanjut</h4>${text('issues','Kendala selama event','Contoh: alat, venue, keterlambatan, atau keluhan client. Kosongkan bila tidak ada.')}${text('resolution','Penanganan kendala','Apa yang dilakukan tim dan bagaimana hasilnya?')}${text('follow_up','Tindak lanjut yang masih terbuka','Tuliskan pekerjaan yang tersisa, penanggung jawab, dan target penyelesaiannya.')}
      <h4><span>04</span> Dokumentasi</h4><p class="closing-hint">Foto setup atau pelaksanaan event (opsional). Maksimal 8 foto; gambar diperkecil sebelum disimpan.</p>
      ${editable?'<div class="closing-upload"><label class="field-label">Kategori<select class="field-input" id="closing-photo-kind"><option value="setup">Setup</option><option value="event">Pelaksanaan event</option></select></label><label class="field-label">Tambah foto JPG/PNG<input id="closing-photo-input" type="file" accept="image/jpeg,image/png" multiple></label><p id="closing-photo-status" role="status"></p></div>':''}
      <div id="closing-photos" class="closing-photos">${closingPhotosHtml(draft.photos,editable)}</div></fieldset>
      ${editable?'<div class="closing-form-footer"><p class="closing-hint">Draft otomatis tersimpan di perangkat ini. Simpan ke server agar tersedia di perangkat lain. Setelah dikirim, laporan hanya bisa diubah bila coordinator meminta revisi.</p><p class="inline-form-error" role="alert" hidden></p><div><button class="button button-light" type="submit" name="action" value="draft">Simpan draft ke server</button><button class="button button-primary" type="submit" name="action" value="submit">Kirim untuk review</button></div></div>':''}</form>`:''}
    ${r.can_review?`<form id="closing-review-form" data-event-id="${d.id}" class="closing-review"><h4>Review coordinator</h4><label class="field-label">Catatan review<textarea class="field-input" name="note" rows="3" maxlength="2000" placeholder="Wajib diisi jika meminta revisi."></textarea></label><p class="inline-form-error" role="alert" hidden></p><div><button class="button button-light" name="action" value="revise" type="submit">Minta revisi</button><button class="button button-primary" name="action" value="accept" type="submit">Terima laporan</button></div></form>`:''}
    ${r.status==='accepted'?`<div class="closing-info closing-success">Laporan diterima. Data pemakaian bahan dan catatan penutupan sudah masuk ke export Excel.${r.can_clear?'<br>Lanjutkan Clear untuk membuka penilaian Crew dan PIC.':''}</div>${renderClearEventAction(d)}`:''}
    ${r.history.length?`<details class="closing-history"><summary>Riwayat laporan · ${r.history.length} aktivitas</summary><ol>${r.history.map(h=>`<li><b>${escapeHtml(closingActionLabels[h.action]||h.action)}</b><small>${escapeHtml(h.actor_name||'Akun dihapus')} · ${dateLong(h.created_at)} ${timePart(h.created_at)}</small>${h.note?`<p>${escapeHtml(h.note)}</p>`:''}</li>`).join('')}</ol></details>`:''}`;
}

function closingRibbonTotal(data) {
  const rolls=data.ribbon_rolls||[];
  if(!rolls.length||rolls.some(r=>r.start==null||r.start===''||r.end==null||r.end==='')) return '—';
  if(rolls.some(r=>Number(r.end)>Number(r.start))) return 'Periksa sisa ribbon';
  return String(rolls.reduce((sum,r)=>sum+Number(r.start)-Number(r.end),0));
}

function updateClosingRibbonSummary(data) {
  const total=closingRibbonTotal(data);
  const value=$('#closing-ribbon-total'),summary=$('.material-ribbon-summary');
  if(value) value.textContent=total;
  if(summary) summary.textContent=total==='—'?'Belum diisi':total+' cetak';
}

function closingPhotosHtml(photos,editable) {
  return photos.length?photos.map((photo,i)=>`<figure>${photo.missing?'<p class="closing-hint">Foto tidak tersedia. Tambahkan ulang atau hapus foto ini.</p>':`<a href="${escapeHtml(photo.url||photo.image)}" target="_blank" rel="noopener"><img src="${escapeHtml(photo.url||photo.image)}" alt="${escapeHtml(photo.caption||(photo.kind==='setup'?'Foto setup':'Foto event'))}" loading="lazy"></a>`}<figcaption><span>${photo.kind==='setup'?'Setup':'Event'}${photo.id?'':' · Belum di server'}</span>${editable?`<button type="button" class="text-link" data-action="closing-remove-photo" data-index="${i}">Hapus</button>`:''}</figcaption></figure>`).join(''):'<p class="closing-hint">Belum ada foto dokumentasi.</p>';
}

function captureClosingDraft() {
  const form=$('#closing-form');
  if (!form || form.dataset.busy || !state.drawer?.closing_report?.can_edit) return null;
  const draft=closingDraft(state.drawer);
  if(draft.conflict||draft.saving) return null;
  draft.data=Object.fromEntries(new FormData(form));
  draft.data.printing_mode='print';
  draft.data.materials=Object.fromEntries(closingMaterials.map(([key])=>[key,form.elements.namedItem('material_'+key).value]));
  draft.data.ribbon_rolls=[...form.querySelectorAll('.closing-roll')].map(row=>({start:row.querySelector('[data-roll-start]').value,end:row.querySelector('[data-roll-end]').value}));
  return draft;
}

async function resizeClosingPhoto(file) {
  if (!['image/jpeg','image/png'].includes(file.type)||file.size>15*1024*1024) throw new Error('Pilih foto JPG/PNG, maksimal 15 MB sebelum diperkecil.');
  const source=URL.createObjectURL(file);
  try {
    const img=new Image();img.src=source;await img.decode();
    const scale=Math.min(1,1600/Math.max(img.width,img.height));
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(img.width*scale));canvas.height=Math.max(1,Math.round(img.height*scale));
    const ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(img,0,0,canvas.width,canvas.height);
    for (const quality of [.82,.65,.45,.25]) {
      const encoded=canvas.toDataURL('image/jpeg',quality);
      if ((encoded.length-23)*.75<=500*1024) return encoded;
    }
    throw new Error('Foto terlalu besar setelah diperkecil. Pilih gambar dengan resolusi lebih kecil.');
  } finally {URL.revokeObjectURL(source);}
}

document.addEventListener('input',event=>{
  if(event.target.closest('#closing-form')) {
    const draft=captureClosingDraft();
    if(draft) {persistClosingDraft(draft);updateClosingRibbonSummary(draft.data);}
  }
});
document.addEventListener('change',async event=>{
  const input=event.target;
  if(input.id!=='closing-photo-input') {
    if(input.closest('#closing-form')) {const draft=captureClosingDraft();if(draft) persistClosingDraft(draft);}
    return;
  }
  const draft=captureClosingDraft(),files=[...input.files];
  const draftKey=state.drawer&&closingDraftKey(state.drawer);
  const form=input.form,feedback=$('#closing-photo-status'),kind=$('#closing-photo-kind').value;
  if(!draft||!files.length) return;
  const buttons=[...form.querySelectorAll('button[type="submit"]')];
  if(draft.photosBusy) {input.value='';return;}
  draft.photosBusy=true;input.disabled=true;buttons.forEach(b=>b.disabled=true);form.dataset.photosBusy='1';
  try {
    if(draft.photos.length+files.length>8) throw new Error('Maksimal 8 foto per event. Hapus foto yang tidak diperlukan.');
    feedback.textContent='Memproses foto…';
    draft.photoWork=(async()=>{
      const incoming=[];
      for(const file of files) incoming.push({kind,image:await resizeClosingPhoto(file)});
      return incoming;
    })();
    const incoming=await draft.photoWork;
    // Do not add a delayed photo to another account/event after navigation.
    if(state.data&&closingDraftKey({id:form.dataset.eventId})===draftKey&&closingDrafts.get(draftKey)===draft) {
      draft.photos.push(...incoming);
      persistClosingDraft(draft,{id:form.dataset.eventId});
      if(form.isConnected) form.querySelector('#closing-photos').innerHTML=closingPhotosHtml(draft.photos,true);
    }
    feedback.textContent='Foto siap. Status penyimpanan draft ditampilkan di atas.';
  } catch(error) {feedback.textContent=error.message;}
  finally {
    draft.photosBusy=false;draft.photoWork=null;input.value='';input.disabled=false;buttons.forEach(b=>b.disabled=false);delete form.dataset.photosBusy;
    updateClosingDraftStatus();
    const current=$('#closing-form');
    if(current && current!==form && current.dataset.eventId===form.dataset.eventId && state.drawer?.closing_report?.can_edit) {
      current.querySelector('#closing-photos').innerHTML=closingPhotosHtml(draft.photos,true);
    }
  }
});

document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-action]');
  if(!button||!button.dataset.action.startsWith('closing-')) return;
  const action=button.dataset.action;
  if(['closing-recover','closing-discard','closing-load-other'].includes(action)) {
    const d=state.drawer,draft=closingDraft(d),key=closingDraftKey(d),actorId=state.data.user.id;
    button.disabled=true;
    try {
      if(action==='closing-load-other') {
        const record=deviceDraftStore.read(key);
        const loaded=record?await deviceDraftStore.hydrate(key,record):null;
        if(state.data?.user.id!==actorId) return;
        if(loaded) closingDrafts.set(key,loaded);else closingDrafts.delete(key);
      } else if(action==='closing-discard') {
        if(!deviceDraftStore.clear(key,draft.token)) throw new Error('Draft baru saja berubah di tab lain. Muat ulang sebelum memilih versi.');
        closingDrafts.delete(key);
      } else {
        if(!d.closing_report.can_edit) throw new Error('Laporan sedang terkunci.');
        const currentToken=deviceDraftStore.read(key)?.token||null;
        if(draft.conflict!=='tab'&&(draft.token||null)!==currentToken) {
          draft.conflict='tab';renderDrawer();throw new Error('Draft juga berubah di tab lain. Pilih isian yang ingin dilanjutkan.');
        }
        draft.token=currentToken;
        draft.version=d.closing_report.version;draft.conflict=null;draft.restored=true;
        const available=new Set(d.closing_report.photos.map(p=>p.id));
        draft.photos=draft.photos.map(p=>p.id&&!available.has(p.id)?{...p,missing:true}:p);
        draft.photos.filter(p=>p.local_id).forEach(p=>deviceDraftStore.photoWrites.delete(`${key}:${p.local_id}`));
        persistClosingDraft(draft,d);
      }
      if(state.drawer?.id===d.id) renderDrawer();
    } catch(error) {toast(error.message,'error');}
    finally {button.disabled=false;}
    return;
  }
  const draft=captureClosingDraft();if(!draft) return;
  if(action==='closing-add-roll') {
    if(draft.data.ribbon_rolls.length>=30) {toast('Maksimal 30 roll ribbon.','error');return;}
    draft.data.ribbon_rolls.push({start:null,end:null});
  } else if(action==='closing-remove-roll') draft.data.ribbon_rolls.splice(Number(button.dataset.index),1);
  else if(action==='closing-remove-photo') {
    draft.photos.splice(Number(button.dataset.index),1);persistClosingDraft(draft);$('#closing-photos').innerHTML=closingPhotosHtml(draft.photos,true);return;
  } else return;
  persistClosingDraft(draft);
  $('#closing-rolls').innerHTML=draft.data.ribbon_rolls.map((r,i)=>closingRollHtml(r,i,true)).join('');
  updateClosingRibbonSummary(draft.data);
});

document.addEventListener('submit',async event=>{
  const form=event.target;if(!['closing-form','closing-review-form'].includes(form.id)) return;
  event.preventDefault();if(form.dataset.busy||form.dataset.photosBusy) return;
  const isReport=form.id==='closing-form',report=state.drawer.closing_report;
  const actorId=state.data.user.id;
  const action=event.submitter?.value||(isReport?'draft':'');if(!action) return;
  const payload={action,version:report.version};
  let draft=null,savedToken=null;
  const draftKey=closingDraftKey(state.drawer);
  if(isReport) {
    draft=captureClosingDraft();
    if(!draft) return;
    if(draft.photosBusy) {toast('Tunggu sampai foto selesai diproses.','error');return;}
    if(draft.photos.some(p=>p.missing)) {toast('Ada foto draft yang tidak tersedia. Tambahkan ulang atau hapus foto tersebut.','error');return;}
    persistClosingDraft(draft);
    if(draft.conflict) return;
    savedToken=draft.token;draft.saving=true;
    payload.version=draft.version;
    payload.data=structuredClone(draft.data);
    payload.photos=draft.photos.map(p=>p.id?{id:p.id}:p);
  } else payload.note=form.elements.note.value;
  const errorNode=form.querySelector('.inline-form-error');errorNode.hidden=true;
  const fieldset=form.querySelector('fieldset');if(fieldset) fieldset.disabled=true;
  form.dataset.busy='1';const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
  updateClosingDraftStatus();
  try {
    await api(`/api/events/${form.dataset.eventId}/closing`,{method:'POST',body:JSON.stringify(payload)});
    if(isReport) {
      try {deviceDraftStore.clear(draftKey,savedToken);} catch{}
      if(closingDrafts.get(draftKey)===draft&&draft.token===savedToken) closingDrafts.delete(draftKey);
    }
    if(state.data?.user.id!==actorId) return;
    toast({draft:'Draft laporan disimpan.',submit:'Laporan dikirim ke coordinator.',accept:'Laporan diterima. Event siap di-clear.',revise:'Catatan revisi disimpan untuk PIC.'}[action]);
    await refreshData();
  } catch(error) {
    errorNode.textContent=error.message;errorNode.hidden=false;errorNode.scrollIntoView({block:'nearest'});
    if(!form.isConnected) toast(error.message,'error');
    if(draft&&error.details?.code==='closing_version_conflict') {
      draft.saving=false;
      await openEvent(form.dataset.eventId,'closing');
    }
  }
  finally {
    if(draft) draft.saving=false;if(fieldset) fieldset.disabled=false;delete form.dataset.busy;buttons.forEach(b=>b.disabled=false);
    if(isReport&&!form.isConnected&&String(state.drawer?.id)===form.dataset.eventId&&state.drawerTab==='closing') renderDrawer();
    updateClosingDraftStatus();
  }
});

window.addEventListener('online',updateClosingDraftStatus);
window.addEventListener('offline',updateClosingDraftStatus);
window.addEventListener('storage',event=>{
  if(!event.key||!closingDrafts.has(event.key)) return;
  const draft=closingDrafts.get(event.key);
  try {
    const latest=deviceDraftStore.read(event.key);
    if((latest?.token||null)===(draft.token||null)) return;
  } catch{}
  draft.conflict='tab';
  if(state.data&&state.drawer&&closingDraftKey(state.drawer)===event.key&&state.drawerTab==='closing') renderDrawer();
});
window.addEventListener('beforeunload',event=>{
  if([...closingDrafts.values()].some(d=>d.photosBusy||d.persisting||(d.dirty&&(d.storageError||d.conflict==='tab')))) {
    event.preventDefault();event.returnValue='';
  }
});
