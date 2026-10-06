/* Additional operational views. Loaded before app.js; handlers run after startup. */
let notificationTimer = null;
let notificationLoading = false;
let notificationOnlyUnread = false;
let notificationError = '';
let notificationEpoch = 0;
let vehicleMonth = null;
let vehicleEditId = null;
let vehicleOnlyMine = true;
let logoutBusy = false;
const logisticsDrafts = new Map();
const transportChoices = [['fleet','Kendaraan operasional','Mobil atau van, misalnya Luxio'],['motorcycles','Motor masing-masing','Tim memakai motor pribadi'],['courier','Lalamove / kurir','Pengiriman alat via layanan kurir'],['other','Lainnya','Transportasi lain yang dipakai']];

function logisticsFormData(form) {
  const data={};
  for(const input of form.querySelectorAll('input[name],textarea[name],select[name]')) {
    if(input.name!=='transport_modes') data[input.name]=input.value;
  }
  data.transport_modes=[...form.querySelectorAll('[name="transport_modes"]:checked')].map(input=>input.value);
  return data;
}

function captureLogisticsDraft(form) {
  if(!state.data || !form || form.id!=='event-logistics-form' || form.dataset.busy) return;
  const data=logisticsFormData(form);
  logisticsDrafts.set(`${state.data.user.id}:${form.dataset.eventId}`,data);
  const status=form.querySelector('[data-logistics-status]');
  if(status) {status.textContent='Belum disimpan';status.classList.remove('saved');}
  return data;
}

function syncTransportFields(form) {
  const modes=[...form.querySelectorAll('[name="transport_modes"]:checked')].map(input=>input.value);
  for(const group of form.querySelectorAll('[data-transport-fields]')) {
    group.hidden=!modes.includes(group.dataset.transportFields);
    group.querySelectorAll('input,textarea,select').forEach(input=>input.disabled=group.hidden);
  }
}

function renderNotificationBadge() {
  const count = state.data?.notifications?.unread_count || 0;
  const badge = $('#notification-count');
  badge.hidden = !count;
  badge.textContent = count > 99 ? '99+' : String(count);
  $('#notification-button').setAttribute('aria-label',`Notifikasi, ${count} belum dibaca`);
}

function closeNotifications(returnFocus = false) {
  $('#notification-panel').hidden = true;
  $('#notification-button').setAttribute('aria-expanded','false');
  if (returnFocus) $('#notification-button').focus();
}

function renderNotifications() {
  const panel = $('#notification-panel');
  const data = state.data?.notifications || {items:[],unread_count:0};
  const items = data.items.filter(item=>!notificationOnlyUnread || !item.read);
  panel.innerHTML = `<div class="notification-head"><div><h2>Notifikasi</h2><p>${data.unread_count} belum dibaca</p></div><button class="drawer-close" data-action="close-notifications" aria-label="Tutup notifikasi">×</button></div>
    <div class="notification-tools"><button class="notification-filter ${!notificationOnlyUnread?'active':''}" data-action="notification-filter" data-unread="0">Semua</button><button class="notification-filter ${notificationOnlyUnread?'active':''}" data-action="notification-filter" data-unread="1">Belum dibaca</button><button class="text-link" data-action="read-all-notifications" ${!data.unread_count?'disabled':''}>Tandai semua dibaca</button></div>
    ${notificationError?`<div class="notification-error" role="status">${escapeHtml(notificationError)} <button class="text-link" data-action="refresh-notifications">Coba lagi</button></div>`:''}
    <div class="notification-list">${items.length?items.map(item=>`<button class="notification-item ${item.read?'':'unread'}" data-action="open-notification" data-key="${escapeHtml(item.key)}"><span class="notification-indicator ${item.tone==='red'?'urgent':''}" aria-hidden="true"></span><span><b>${escapeHtml(item.title)}</b><strong>${escapeHtml(item.event_title)}</strong><span>${escapeHtml(item.body)}</span><small>${dateLong(item.event_at)} · ${timePart(item.event_at)}${item.read?' · Dibaca':''}</small></span><span class="notification-go" aria-hidden="true">↗</span></button>`).join(''):`<div class="notification-empty"><span aria-hidden="true">✓</span><b>${notificationOnlyUnread?'Semua sudah dibaca':'Belum ada notifikasi'}</b><p>Pengingat dan pembaruan event Anda muncul di sini.</p></div>`}</div>
    ${data.next_before?'<button class="text-link notification-older" data-action="older-notifications">Lihat notifikasi sebelumnya</button>':''}
    <div class="notification-foot"><button class="text-link" data-action="open-push-settings">Pengaturan & push</button><button class="text-link" data-action="refresh-notifications" ${notificationLoading?'disabled':''}>Muat ulang</button></div>`;
}

async function refreshNotifications(showErrors = false) {
  if (!state.data || notificationLoading) return;
  const epoch = notificationEpoch;
  notificationLoading = true;
  try {
    const data = await api('/api/notifications');
    if (!state.data || epoch !== notificationEpoch) return;
    state.data.notifications = data;
    notificationError = '';
    renderNotificationBadge();
  } catch (error) {
    if (epoch !== notificationEpoch) return;
    notificationError = 'Notifikasi belum dapat diperbarui.';
    if (showErrors) toast(error.message,'error');
  } finally {
    if (epoch === notificationEpoch) {
      notificationLoading = false;
      if (!$('#notification-panel').hidden) renderNotifications();
    }
  }
}

function startNotifications() {
  stopNotifications();
  renderNotificationBadge();
  notificationTimer = setInterval(()=>{if (!document.hidden) refreshNotifications();},60000);
}

function stopNotifications() {
  clearInterval(notificationTimer);
  notificationTimer = null;
  notificationEpoch++;
  notificationLoading = false;
  notificationError = '';
  closeNotifications();
}

async function markNotificationsRead(keys) {
  const epoch = notificationEpoch;
  const data = await api('/api/notifications/read',{method:'POST',body:JSON.stringify(keys===null?{all:true}:{keys})});
  if (!state.data || epoch !== notificationEpoch) return;
  state.data.notifications = data;
  renderNotificationBadge();
  renderNotifications();
}

function openLogoutDialog() {
  if (!state.data) return;
  closeNotifications();
  const user = state.data.user;
  $('#logout-name').textContent = user.full_name;
  $('#logout-email').textContent = user.email;
  $('#logout-avatar').textContent = initials(user.full_name);
  $('#logout-error').hidden = true;
  $('#logout-dialog').showModal();
  $('#logout-cancel').focus();
}

async function confirmLogout() {
  if (logoutBusy) return;
  logoutBusy = true;
  const button = $('#logout-confirm');
  button.disabled = true; $('#logout-cancel').disabled = true;
  button.textContent = 'Keluar…';
  try {
    const drafts=[...closingDrafts.values()];
    if(drafts.some(d=>d.photosBusy||d.persisting)) button.textContent='Menyimpan draft…';
    await Promise.allSettled(drafts.map(d=>d.photoWork).filter(Boolean));
    await Promise.allSettled(drafts.map(d=>d.persistPromise).filter(Boolean));
    if(drafts.some(d=>d.dirty&&(d.storageError||d.conflict==='tab'))) {
      throw new Error('Ada draft yang belum aman tersimpan di perangkat. Kembali ke Penutupan untuk menyimpan ke server atau menyelesaikan pilihan draft.');
    }
    await api('/api/logout',{method:'POST',body:'{}'});
    await clearPushSession().catch(()=>{});
    closeCompensationHistory();
    stopNotifications();
    state.attendanceStream?.getTracks().forEach(track=>track.stop());
    state.attendanceStream = null; state.attendanceCapture = null;
    state.data = null; state.drawer = null; state.page = 'dashboard'; state.search = '';
    closingDrafts.clear();
    logisticsDrafts.clear();
    state.eventMonth = null; state.queueMonth = null; state.eventStatus = 'all'; state.drawerTab = 'overview';
    state.rateUserId = ''; state.kpiSubjectId = ''; state.performanceSubjectId = ''; state.payslipYear = '';
    vehicleMonth = null; vehicleEditId = null; vehicleOnlyMine = true; notificationOnlyUnread = false;
    $('#modal-root').innerHTML = ''; $('#notification-panel').innerHTML = ''; $('#page-content').innerHTML = '';
    $('#navigation').innerHTML = ''; $('#global-search').value = ''; $('#login-password').value = '';
    $('#sidebar').classList.remove('open'); $('#logout-dialog').close();
    showLogin(); $('#login-email').focus(); toast('Anda sudah keluar dari workspace.');
  } catch (error) {
    $('#logout-error').textContent = error.message; $('#logout-error').hidden = false;
  } finally {
    logoutBusy = false; button.disabled = false; $('#logout-cancel').disabled = false; button.textContent = 'Ya, keluar';
  }
}

function canEditEventLogistics(event) {
  return has('events.assign') && (!event.coordinator_id || Number(event.coordinator_id)===Number(state.data.user.id) || has('users.manage') || has('kpi.evaluate_operations'));
}

function renderVehiclesPage() {
  if (!has('events.assign')) return '<div class="empty-state">Anda tidak memiliki akses mapping kendaraan.</div>';
  const fleet = state.data.vehicles || [];
  const allEvents = state.data.events.filter(e=>e.status!=='cancelled');
  const months = eventMonths(allEvents);
  if (vehicleMonth === null) vehicleMonth = defaultEventMonth(months);
  const events = allEvents.filter(e=>(vehicleMonth==='all'||wibDateISO(e.starts_at).slice(0,7)===vehicleMonth) && (!vehicleOnlyMine || Number(e.coordinator_id)===Number(state.data.user.id)));
  const mapped = events.filter(e=>e.transport_modes?.length || e.vehicle_id).length;
  const v = fleet.find(item=>item.id===vehicleEditId) || {};
  return `${pageHead('TRANSPORTASI','Transportasi event','Atur kendaraan operasional, motor tim, dan pengiriman alat.')}
    <div class="transport-summary"><div><span>EVENT DITAMPILKAN</span><b>${events.length}</b></div><div><span>TRANSPORTASI DIATUR</span><b>${mapped}<small> / ${events.length}</small></b></div><div><span>ARMADA AKTIF</span><b>${fleet.filter(v=>v.active).length}</b></div></div>
    <section class="panel"><div class="panel-head transport-panel-head"><div><h3>Jadwal transportasi</h3><p>Semua waktu menggunakan WIB. Buka event untuk mengatur mapping.</p></div><div class="transport-filters"><select id="vehicle-month" class="filter-select" aria-label="Bulan mapping">${monthFilterOptions(months,vehicleMonth)}</select><label><input id="vehicle-only-mine" type="checkbox" ${vehicleOnlyMine?'checked':''}> Event saya</label></div></div>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>EVENT / COORDINATOR</th><th>TRANSPORTASI</th><th>DRIVER / PIC</th><th>LOADING</th><th></th></tr></thead><tbody>${events.length?events.map(e=>`<tr><td><div class="table-event"><b>${escapeHtml(e.title)}</b><small>${dateLong(e.starts_at)} · ${escapeHtml(e.coordinator_name||'Belum ada coordinator')}</small></div></td><td><div class="table-event"><b>${escapeHtml(e.transport_label||e.vehicle_name||'Belum diatur')}</b><small>${escapeHtml(e.plate_number||'')}${e.vendor_name?' · '+escapeHtml(e.vendor_name):''}</small></div></td><td>${escapeHtml(e.driver_name||'Belum diisi')}</td><td>${e.loading_date?`${escapeHtml(e.loading_date)}<br><small>${escapeHtml(e.loading_time)} WIB</small>`:'—'}</td><td><button class="button button-light button-small" data-action="open-logistics" data-id="${e.id}">${canEditEventLogistics(e)?'Atur mapping':'Lihat'}</button></td></tr>`).join(''):'<tr><td colspan="5"><div class="empty-state">Belum ada event untuk filter ini. Pilih bulan lain atau tampilkan semua coordinator.</div></td></tr>'}</tbody></table></div></section>
    <details class="fleet-management"><summary>Kelola armada operasional <span>Opsional · kendaraan bisa diketik langsung pada event</span></summary><div class="fleet-layout"><section class="panel"><div class="panel-head"><div><h3>Daftar kendaraan</h3><p>Kendaraan dapat diketik langsung saat mengisi event.</p></div><span class="badge badge-purple">${fleet.length} unit</span></div><div class="fleet-list">${fleet.length?fleet.map(v=>`<div class="fleet-item"><span class="fleet-icon" aria-hidden="true">▱</span><div><b>${escapeHtml(v.name)}</b><small>${escapeHtml(v.plate_number||'Tanpa nomor polisi')} · ${v.ownership==='rental'?escapeHtml(v.vendor_name):'Internal'}${v.active?'':' · Nonaktif'}</small></div><button class="text-link" data-action="edit-vehicle" data-id="${v.id}" aria-label="Edit ${escapeHtml(v.name)}">Edit</button></div>`).join(''):'<div class="empty-state">Ketik nama kendaraan di detail event untuk mulai mapping. Data kendaraan akan muncul di sini setelah disimpan.</div>'}</div></section>
    <section class="panel"><div class="panel-head"><div><h3>${v.id?'Edit':'Tambah'} kendaraan</h3><p>Kendaraan internal maupun vendor sewa.</p></div></div><form id="vehicle-form" class="fleet-form" data-id="${v.id||''}">
      <label class="field-label">Nama kendaraan<input class="field-input" name="name" maxlength="100" value="${escapeHtml(v.name||'')}" placeholder="Contoh: Luxio" required></label>
      <label class="field-label">Nomor polisi<input class="field-input" name="plate_number" maxlength="24" value="${escapeHtml(v.plate_number||'')}" placeholder="Contoh: B 1234 ABC"></label>
      <label class="field-label">Jenis kendaraan<select class="field-input" name="ownership"><option value="internal" ${v.ownership==='rental'?'':'selected'}>Internal</option><option value="rental" ${v.ownership==='rental'?'selected':''}>Sewa</option></select></label>
      <label class="field-label" id="vehicle-vendor-field" ${v.ownership==='rental'?'':'hidden'}>Vendor sewa<input class="field-input" name="vendor_name" maxlength="160" value="${escapeHtml(v.vendor_name||'')}" ${v.ownership==='rental'?'required':''}></label>
      <label class="fleet-active"><input type="checkbox" name="active" ${v.active===0?'':'checked'}> Aktif untuk penugasan baru</label>
      <div class="form-actions">${v.id?'<button type="button" class="button button-light" data-action="new-vehicle">Batal</button>':''}<button class="button button-primary" type="submit">Simpan kendaraan</button></div><p class="inline-form-error" role="alert" hidden></p></form></section></div></details>`;
}

function renderLogisticsTab(d) {
  const saved=d.logistics||{};
  const draft=logisticsDrafts.get(`${state.data.user.id}:${d.id}`);
  const o={...saved,...draft};
  const modes=o.transport_modes|| (o.vehicle_id?['fleet']:[]);
  const editable=d.can_manage_logistics&&d.status!=='cancelled';
  const input=(name,label,type='text',extra='')=>`<label class="field-label">${label}<input class="field-input" name="${name}" type="${type}" value="${escapeHtml(o[name]??'')}" ${extra}></label>`;
  const vehicleText=draft?.vehicle_name??[saved.vehicle_name,saved.plate_number].filter(Boolean).join(' · ');
  const people=[...new Set([...(d.assignments||[]).map(a=>a.full_name),...(state.data.employees||[]).map(u=>u.full_name)])];
  return `<div class="logistics-intro"><span class="eyebrow-dark">TRANSPORTASI & DETAIL EVENT</span><h3>Rencana keberangkatan</h3><p>Atur perjalanan tim dan pengiriman alat dalam satu form.</p></div>
    ${(d.vehicle_conflicts||[]).length?`<div class="logistics-warning" role="alert">Kendaraan bentrok dengan ${d.vehicle_conflicts.map(c=>escapeHtml(c.title)).join(', ')}. Perbarui kendaraan atau jadwalnya.</div>`:''}
    ${!editable?'<p class="logistics-readonly">Detail ini dapat diubah oleh coordinator event, Head Operations, atau Admin.</p>':''}
    <form id="event-logistics-form" data-event-id="${d.id}" novalidate><fieldset class="logistics-fieldset" ${editable?'':'disabled'}>
      <section class="ops-form-card"><div class="ops-card-heading"><span>01</span><div><h4>Transportasi yang dipakai</h4><p>Boleh pilih lebih dari satu, misalnya motor tim + Lalamove.</p></div></div>
        <div class="transport-options">${transportChoices.map(([key,title,desc])=>`<label class="transport-option"><input type="checkbox" name="transport_modes" value="${key}" ${modes.includes(key)?'checked':''}><span><b>${title}</b><small>${desc}</small></span></label>`).join('')}</div>
        <div class="transport-details" data-transport-fields="fleet" ${modes.includes('fleet')?'':'hidden'}><div class="logistics-grid">
          <label class="field-label">Nama kendaraan<input class="field-input" name="vehicle_name" type="text" maxlength="140" value="${escapeHtml(vehicleText)}" placeholder="Ketik langsung, contoh: Luxio" autocomplete="off" ${modes.includes('fleet')?'':'disabled'}><small>Langsung tersimpan saat klik Simpan. Nomor polisi cukup jika nama unit sama.</small></label>
          ${input('driver_name','Driver / PIC perjalanan (opsional)','text','maxlength="120" list="event-driver-options"')}
          <datalist id="event-driver-options">${people.map(name=>`<option value="${escapeHtml(name)}"></option>`).join('')}</datalist>
          <label class="field-label logistics-wide">Perkiraan kendaraan kembali (opsional, WIB)<input class="field-input" name="vehicle_return_at" type="datetime-local" value="${escapeHtml((o.vehicle_return_at||'').slice(0,16))}"><small>Kosongkan untuk memakai waktu selesai event.</small></label>
        </div>${o.vendor_name?`<p class="logistics-hint">Vendor sewa: ${escapeHtml(o.vendor_name)}</p>`:''}</div>
        <div class="transport-details transport-simple" data-transport-fields="motorcycles" ${modes.includes('motorcycles')?'':'hidden'}><span class="transport-tick" aria-hidden="true">✓</span><p>Tim menggunakan motor pribadi masing-masing. Tidak perlu mendaftarkan motor satu per satu.</p></div>
        <div class="transport-details" data-transport-fields="courier" ${modes.includes('courier')?'':'hidden'}><div class="logistics-grid">
          <label class="field-label">Layanan pengiriman<input class="field-input" name="courier_name" maxlength="100" value="${escapeHtml(o.courier_name||'Lalamove')}" placeholder="Lalamove"></label>
          ${input('courier_note','Detail kiriman (opsional)','text','maxlength="300" placeholder="Nomor pesanan, nama driver, atau jadwal jemput"')}
        </div></div>
        <div class="transport-details" data-transport-fields="other" ${modes.includes('other')?'':'hidden'}>${input('other_transport','Transportasi lainnya','text','maxlength="160" placeholder="Contoh: taksi online atau kendaraan client"')}</div>
        <label class="field-label transport-notes">Catatan perjalanan (opsional)<textarea class="field-input" name="transport_note" rows="2" maxlength="1000" placeholder="Contoh: tim berangkat dengan motor, alat dikirim Lalamove.">${escapeHtml(o.transport_note||'')}</textarea></label>
      </section>
      <details class="ops-form-card loading-details" ${o.loading_date||o.loading_time?'open':''}><summary><span class="ops-section-number">02</span><span>Jadwal loading <small>Opsional · semua waktu dalam WIB</small></span><span class="ops-expand" aria-hidden="true">⌄</span></summary><div class="logistics-grid">${input('loading_date','Tanggal loading','date')}${input('loading_time','Jam loading','time')}</div></details>
      <section class="ops-form-card"><div class="ops-card-heading"><span>03</span><div><h4>Client & layanan</h4><p>Data ini ikut masuk ke export event.</p></div></div><div class="logistics-grid">${input('client_name','Nama client','text','maxlength="160"')}${input('client_phone','Nomor telepon client','tel','maxlength="40"')}${input('service_type','Jenis layanan','text','maxlength="100" placeholder="Contoh: Classic"')}${input('equipment_setup','Setup peralatan','text','maxlength="200" placeholder="Contoh: Probooth"')}${input('sales_name','Sales','text','maxlength="120"')}</div><label class="field-label logistics-notes">Catatan event (opsional)<textarea class="field-input" name="notes" rows="3" maxlength="4000">${escapeHtml(o.notes||'')}</textarea></label></section>
      </fieldset>${editable?`<div class="logistics-save"><p class="inline-form-error" role="alert" hidden></p><div class="ops-save-row"><span class="ops-save-status ${draft?'':'saved'}" data-logistics-status role="status">${draft?'Belum disimpan':saved.updated_at?'Data tersimpan':'Siap diisi'}</span><button class="button button-primary" type="submit">Simpan perubahan</button></div></div>`:''}</form>`;
}

document.addEventListener('click', async event=>{
  const button = event.target.closest('[data-action]');
  if (!event.target.closest('.notification-wrap') && !$('#notification-panel').hidden) closeNotifications();
  if (!button) return;
  const action = button.dataset.action;
  try {
    if (action==='toggle-notifications') {
      const panel=$('#notification-panel');
      if (!panel.hidden) {closeNotifications();return;}
      panel.hidden=false; $('#notification-button').setAttribute('aria-expanded','true'); renderNotifications();
      panel.querySelector('button')?.focus(); await refreshNotifications();
    }
    if (action==='close-notifications') closeNotifications(true);
    if (action==='refresh-notifications') await refreshNotifications(true);
    if (action==='notification-filter') {notificationOnlyUnread=button.dataset.unread==='1'; renderNotifications();}
    if (action==='read-all-notifications') {button.disabled=true; await markNotificationsRead(null);}
    if (action==='open-notification') {
      const item=state.data.notifications.items.find(i=>i.key===button.dataset.key);
      if (!item) return;
      await navigateNotification(item);
    }
    if (action==='cancel-logout' && !logoutBusy) $('#logout-dialog').close();
    if (action==='confirm-logout') await confirmLogout();
    if (action==='open-logistics') await openEvent(button.dataset.id,'logistics');
    if (action==='edit-vehicle' || action==='new-vehicle') {
      vehicleEditId=action==='new-vehicle'?null:Number(button.dataset.id); renderPage();
      $('.fleet-management').open=true;
      $('#vehicle-form [name="name"]').focus();
    }
  } catch(error) {toast(error.message,'error'); if (action.includes('notification')) {await refreshNotifications();renderNotifications();}}
});

async function saveEventLogistics(form) {
  if(form.dataset.busy) return;
  const actorId=state.data.user.id;
  const payload=captureLogisticsDraft(form);
  const button=form.querySelector('[type="submit"]'),fieldset=form.querySelector('fieldset');
  const errorNode=form.querySelector('.inline-form-error'),status=form.querySelector('[data-logistics-status]');
  errorNode.hidden=true;form.dataset.busy='1';fieldset.disabled=true;button.disabled=true;button.textContent='Menyimpan…';status.textContent='Menyimpan…';
  try {
    const result=await api(`/api/events/${form.dataset.eventId}/logistics`,{method:'POST',body:JSON.stringify(payload)});
    if(state.data?.user.id!==actorId) return;
    if(!result.logistics) throw new Error('Respons penyimpanan belum lengkap. Muat ulang aplikasi setelah memperbarui server.');
    const saved=result.logistics,eventId=Number(form.dataset.eventId);
    logisticsDrafts.delete(`${actorId}:${eventId}`);
    if(Number(state.drawer?.id)===eventId) {
      state.drawer.logistics=saved;state.drawer.vehicle_conflicts=[];
    }
    state.data.events=state.data.events.map(event=>event.id===eventId?{...event,...saved,
      coordinator_id:event.coordinator_id||actorId,coordinator_name:event.coordinator_name||state.data.user.full_name}:event);
    if(saved.vehicle_id && state.data.vehicles && !state.data.vehicles.some(v=>v.id===saved.vehicle_id)) {
      state.data.vehicles.push({id:saved.vehicle_id,name:saved.vehicle_name,plate_number:saved.plate_number||'',ownership:saved.ownership,vendor_name:saved.vendor_name||'',active:1});
    }
    form.querySelector('[name="vehicle_name"]').value=[saved.vehicle_name,saved.plate_number].filter(Boolean).join(' · ');
    status.textContent='✓ Tersimpan';status.classList.add('saved');
    renderPage();toast('Transportasi dan detail event tersimpan.');
  } catch(error) {
    errorNode.textContent=error.message;errorNode.hidden=false;status.textContent='Belum tersimpan';
    errorNode.scrollIntoView({block:'nearest'});
  } finally {
    delete form.dataset.busy;fieldset.disabled=false;button.disabled=false;button.textContent='Simpan perubahan';syncTransportFields(form);
  }
}

document.addEventListener('submit',async event=>{
  const form=event.target;
  if (!['event-logistics-form','vehicle-form'].includes(form.id)) return;
  event.preventDefault();
  if(form.id==='event-logistics-form') {await saveEventLogistics(form);return;}
  const button=form.querySelector('[type="submit"]');
  if (button.disabled) return;
  button.disabled=true;
  const errorNode=form.querySelector('.inline-form-error'); errorNode.hidden=true;
  try {
    const payload=Object.fromEntries(new FormData(form));
    if (form.id==='vehicle-form') {
      if(form.dataset.id) payload.id=Number(form.dataset.id);
      payload.active=form.elements.active.checked;
      await api('/api/vehicles',{method:'POST',body:JSON.stringify(payload)});
      vehicleEditId=null; toast('Data kendaraan disimpan.');
    }
    await refreshData();
  } catch(error) {errorNode.textContent=error.message;errorNode.hidden=false;errorNode.scrollIntoView({block:'nearest'});}
  finally {button.disabled=false;}
});

document.addEventListener('change',event=>{
  const input=event.target;
  const form=input.closest('#event-logistics-form');
  if(form) {captureLogisticsDraft(form);if(input.name==='transport_modes') syncTransportFields(form);}
  if(input.id==='vehicle-month') {vehicleMonth=input.value;renderPage();}
  if(input.id==='vehicle-only-mine') {vehicleOnlyMine=input.checked;renderPage();}
  if(input.closest('#vehicle-form') && input.name==='ownership') {
    const vendor=$('#vehicle-vendor-field');vendor.hidden=input.value!=='rental';vendor.querySelector('input').required=input.value==='rental';
  }
});
document.addEventListener('input',event=>{
  const form=event.target.closest('#event-logistics-form');
  if(form) captureLogisticsDraft(form);
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape' && !$('#notification-panel').hidden) {event.stopImmediatePropagation();closeNotifications(true);}
});
document.addEventListener('visibilitychange',()=>{if (!document.hidden && state.data) refreshNotifications();});
document.getElementById('logout-dialog').addEventListener('cancel',event=>{if(logoutBusy) event.preventDefault();});
