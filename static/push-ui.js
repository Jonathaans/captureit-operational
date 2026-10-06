/* Device consent, settings and authenticated notification navigation. */
let pushSettingsEpoch=0, pushSettingsData=null, pushBusy=false, pushMessaging=null, pushSDKPromise=null;
const PUSH_SDK_VERSION='12.19.0';
const pushSettingKey=()=>`captureit.push.enabled:${state.data?.draft_namespace}:${state.data?.user.id}`;
function pushStorage(key,value) {
  try { if(value===undefined)return localStorage.getItem(key); if(value===null)localStorage.removeItem(key);else localStorage.setItem(key,value); }
  catch { if(value!==undefined)throw new Error('Penyimpanan perangkat tidak tersedia. Aktifkan penyimpanan browser untuk memakai push.'); }
}
function pushDeviceKey() {
  const name=`captureit.push.device:${state.data.draft_namespace}`;
  let key=pushStorage(name);
  if(!/^[a-f0-9]{64}$/.test(key||'')) {
    key=Array.from(crypto.getRandomValues(new Uint8Array(32)),v=>v.toString(16).padStart(2,'0')).join('');
    pushStorage(name,key);
  }
  return key;
}
function pushSupported() {
  return !!(window.isSecureContext&&'Notification' in window&&'serviceWorker' in navigator&&'PushManager' in window);
}
function scriptForPush(url) {
  return new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=url;script.onload=resolve;
    script.onerror=()=>{script.remove();reject(new Error('Layanan push belum dapat dihubungi. Periksa koneksi lalu coba lagi.'));};document.head.appendChild(script);});
}
async function firebaseMessaging(config) {
  if(!pushSDKPromise)pushSDKPromise=(async()=>{
    await scriptForPush(`https://www.gstatic.com/firebasejs/${PUSH_SDK_VERSION}/firebase-app-compat.js`);
    await scriptForPush(`https://www.gstatic.com/firebasejs/${PUSH_SDK_VERSION}/firebase-messaging-compat.js`);
    if(!await firebase.messaging.isSupported())throw new Error('Browser ini belum mendukung push. Di iPhone, buka aplikasi dari Home Screen.');
    const app=firebase.apps.find(a=>a.name==='captureit-web')||firebase.initializeApp(config.firebase,'captureit-web');
    const messaging=firebase.messaging(app);
    messaging.onMessage(()=>{if(state.data){refreshNotifications();toast('Ada pembaruan di pusat notifikasi.');}});
    pushMessaging=messaging;return messaging;
  })().catch(error=>{pushSDKPromise=null;throw error;});
  return pushSDKPromise;
}
async function registerPush(config,askPermission=true) {
  if(!pushSupported())throw new Error('Gunakan browser yang mendukung push melalui HTTPS. Di iPhone, tambahkan aplikasi ke Home Screen terlebih dahulu.');
  if(!config.ready)throw new Error(config.message);
  const uid=state.data.user.id, epoch=notificationEpoch;
  const permission=askPermission?await Notification.requestPermission():Notification.permission;
  if(permission!=='granted')throw new Error('Izin notifikasi belum diberikan. Anda dapat mengubahnya di pengaturan browser.');
  const messaging=await firebaseMessaging(config);
  const registration=await navigator.serviceWorker.register('/firebase-messaging-sw.js',{scope:'/'});
  await navigator.serviceWorker.ready;
  const token=await messaging.getToken({vapidKey:config.vapid_key,serviceWorkerRegistration:registration});
  if(!token)throw new Error('Token perangkat belum tersedia. Coba aktifkan lagi.');
  if(!state.data||state.data.user.id!==uid||epoch!==notificationEpoch)return;
  const result=await api('/api/push/register',{method:'POST',body:JSON.stringify({token,device_key:pushDeviceKey(),platform:'web',label:'Browser perangkat saya'})});
  if(state.data?.user.id===uid&&epoch===notificationEpoch)pushStorage(pushSettingKey(),'1');
  return result;
}
async function startPushSession() {
  const uid=state.data?.user.id,epoch=notificationEpoch;
  if(!uid)return;
  if(pushStorage(pushSettingKey())==='1'&&pushSupported()&&Notification.permission==='granted') {
    try { const config=await api('/api/push/config');if(state.data?.user.id===uid&&epoch===notificationEpoch&&config.ready)await registerPush(config,false); }
    catch { /* Inbox remains available; settings allow an explicit retry. */ }
  }
}
async function clearPushSession() {
  try {pushStorage(pushSettingKey(),null);}catch{}
  pushSettingsEpoch++;pushSettingsData=null;$('#push-settings-dialog')?.close();
  if('serviceWorker' in navigator) {
    const registration=await navigator.serviceWorker.getRegistration('/');
    registration?.active?.postMessage({type:'CAPTUREIT_LOGOUT'});
    if(registration?.getNotifications)for(const n of await registration.getNotifications())n.close();
  }
}
function pushSettingsHTML(data) {
  const prefs=data.preferences, devices=data.devices, supported=pushSupported();
  const permission=typeof Notification==='undefined'?'tidak tersedia':Notification.permission;
  const active=devices.find(d=>d.this_session);
  return `<div class="push-intro"><b>${escapeHtml(data.push.ready?'Notifikasi di perangkat Anda':data.push.message)}</b><p>Notifikasi tersimpan di aplikasi. Push memberi tahu Anda ketika ada pembaruan; status dibaca tidak mengonfirmasi kesediaan bertugas.</p></div>
    <section class="push-device-card"><h3>Perangkat ini</h3><p>${active?'Terdaftar untuk akun ini.':supported?'Belum diaktifkan untuk akun ini.':'Push belum didukung di browser/koneksi ini. Gunakan HTTPS; untuk iPhone, buka dari Home Screen.'}</p><small>Izin browser: ${escapeHtml(permission)}</small><div class="push-actions">
    <button class="button button-primary" data-action="enable-push" ${!supported||!data.push.ready?'disabled':''}>${active?'Perbarui koneksi':'Aktifkan notifikasi'}</button>
    ${active?`<button class="button button-light" data-action="test-push" data-id="${active.id}">Tes perangkat ini</button>`:''}</div></section>
    <form id="push-preferences-form"><h3>Jenis push yang diterima</h3><div class="push-category-grid">${Object.entries(data.categories).map(([key,label])=>`<label><input type="checkbox" name="${escapeHtml(key)}" ${prefs.categories[key]?'checked':''}><span>${escapeHtml(label)}</span></label>`).join('')}</div>
    <p class="push-help">Menonaktifkan kategori menghentikan push; catatannya tetap tersedia di aplikasi.</p><h3>Jam tenang · WIB</h3><div class="push-time-grid"><label class="field-label">Mulai<input class="field-input" type="time" name="quiet_start" value="${escapeHtml(prefs.quiet_start)}"></label><label class="field-label">Selesai<input class="field-input" type="time" name="quiet_end" value="${escapeHtml(prefs.quiet_end)}"></label></div>
    <p class="push-help">Kosongkan untuk menerima setiap saat. Perubahan/pembatalan jadwal mendesak tetap dikirim; pengingat biasa ditunda.</p><button class="button button-primary" type="submit">Simpan pengaturan</button></form>
    <section class="push-device-list"><h3>Perangkat aktif</h3>${devices.length?devices.map(d=>`<div><span><b>${escapeHtml(d.label)}</b><small>${escapeHtml(d.platform)}${d.this_session?' · sesi ini':''}</small></span><button class="text-link" data-action="disable-push" data-id="${d.id}" data-current="${d.this_session?'1':'0'}">Nonaktifkan</button></div>`).join(''):'<p>Belum ada perangkat terdaftar.</p>'}</section>
    <p class="push-help">Antrean: ${data.delivery_counts.pending||0} · Diterima layanan push: ${data.delivery_counts.sent||0} · Gagal: ${data.delivery_counts.failed||0}. Diterima layanan bukan bukti notifikasi tampil atau dibaca.</p>`;
}
async function openPushSettings() {
  if(!state.data)return;
  closeNotifications();const dialog=$('#push-settings-dialog');if(!dialog.open)dialog.showModal();
  const epoch=++pushSettingsEpoch, uid=state.data.user.id;$('#push-settings-body').innerHTML='<p>Memuat pengaturan…</p>';$('#push-settings-error').hidden=true;
  try {const data=await api('/api/notifications/settings');if(epoch!==pushSettingsEpoch||uid!==state.data?.user.id||!dialog.open)return;
    pushSettingsData=data;$('#push-settings-body').innerHTML=pushSettingsHTML(data);
  }catch(error){if(epoch===pushSettingsEpoch){$('#push-settings-error').textContent=error.message;$('#push-settings-error').hidden=false;}}
}
async function navigateNotification(item) {
  const uid=state.data?.user.id;
  if(!uid)return;
  if(!item.read)await markNotificationsRead([item.key]);
  if(uid!==state.data?.user.id)return;
  closeNotifications();
  if(item.target_kind==='payslip') {
    state.payslipYear='all';state.payslipTarget=Number(item.target_id);state.page='inhouse-payslips';renderShell();
    $('#page-content').innerHTML='<div class="empty-state">Memuat slip gaji…</div>';
    const html=await renderInhousePayslipsPage();
    if(uid===state.data?.user.id&&state.page==='inhouse-payslips'){$('#page-content').innerHTML=html;$('#page-title').textContent='Slip Gaji Saya';document.querySelector(`[data-payslip-id="${Number(item.target_id)}"]`)?.scrollIntoView({block:'start',behavior:'smooth'});}
  }else if(item.event_id&&item.target_kind!=='inbox')await openEvent(item.event_id,item.tab);
  else {$('#notification-panel').hidden=false;renderNotifications();}
}
async function resumeNoticeLink() {
  const url=new URL(window.location.href),notice=url.searchParams.get('notice');
  if(!state.data||!notice||!/^[a-f0-9]{32}$/.test(notice))return;
  const uid=state.data.user.id;
  try {const result=await api('/api/notifications/item?notice='+encodeURIComponent(notice));if(state.data?.user.id===uid)await navigateNotification(result.item);}
  catch(error){toast(error.message,'error');}
  finally {url.searchParams.delete('notice');history.replaceState(null,'',url.pathname+url.search+url.hash);}
}
async function loadOlderNotifications() {
  const before=state.data?.notifications.next_before;if(!before||notificationLoading)return;
  const epoch=notificationEpoch;notificationLoading=true;
  try {const result=await api('/api/notifications?before='+before);if(epoch!==notificationEpoch||!state.data)return;
    const items=new Map(state.data.notifications.items.map(i=>[i.key,i]));result.items.forEach(i=>items.set(i.key,i));
    state.data.notifications={...result,items:[...items.values()]};renderNotifications();
  }catch(error){toast(error.message,'error');}finally{if(epoch===notificationEpoch)notificationLoading=false;}
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-action]');if(!button)return;
  const action=button.dataset.action;
  if(action==='open-push-settings'){await openPushSettings();return;}
  if(action==='close-push-settings'){pushSettingsEpoch++;pushSettingsData=null;$('#push-settings-dialog').close();return;}
  if(action==='older-notifications'){await loadOlderNotifications();return;}
  if(!['enable-push','test-push','disable-push'].includes(action)||pushBusy||!state.data)return;
  pushBusy=true;button.disabled=true;const uid=state.data.user.id;
  try {
    if(action==='enable-push'){await registerPush(pushSettingsData.push);toast('Push terdaftar. Gunakan Tes perangkat ini untuk memeriksa pengiriman.');}
    if(action==='test-push'){const result=await api('/api/push/test',{method:'POST',body:JSON.stringify({device_id:Number(button.dataset.id)})});toast(result.message);}
    if(action==='disable-push'){
      await api('/api/push/unregister',{method:'POST',body:JSON.stringify({device_id:Number(button.dataset.id)})});
      if(button.dataset.current==='1'){try{pushStorage(pushSettingKey(),null);}catch{} if(pushMessaging)await pushMessaging.deleteToken().catch(()=>{});}
      toast('Perangkat dinonaktifkan untuk akun ini.');
    }
    if(state.data?.user.id===uid)await openPushSettings();
  }catch(error){if(state.data?.user.id===uid){$('#push-settings-error').textContent=error.message;$('#push-settings-error').hidden=false;}}
  finally{pushBusy=false;button.disabled=false;}
});
document.addEventListener('submit',async event=>{
  if(event.target.id!=='push-preferences-form')return;event.preventDefault();if(pushBusy||!pushSettingsData)return;
  const form=event.target;pushBusy=true;const uid=state.data?.user.id,button=form.querySelector('[type="submit"]');button.disabled=true;
  try {const categories=Object.fromEntries(Object.keys(pushSettingsData.categories).map(k=>[k,form.elements[k].checked]));
    await api('/api/notifications/preferences',{method:'POST',body:JSON.stringify({categories,quiet_start:form.elements.quiet_start.value,quiet_end:form.elements.quiet_end.value})});
    if(uid===state.data?.user.id){toast('Pengaturan notifikasi tersimpan.');await openPushSettings();}
  }catch(error){if(uid===state.data?.user.id){$('#push-settings-error').textContent=error.message;$('#push-settings-error').hidden=false;}}
  finally{pushBusy=false;button.disabled=false;}
});
