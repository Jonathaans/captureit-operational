function renderInventoryAssetPanels(data) {
  return `<section class="panel table-panel"><div class="panel-head"><h3>Asset per unit</h3></div><div class="table-wrap"><table class="data-table"><thead><tr><th>ASSET</th><th>KODE / SERIAL</th><th>STATUS</th><th>SURAT JALAN</th></tr></thead><tbody>${(data.assets || []).map(a=>`<tr><td>${escapeHtml(a.item_name)}</td><td>${escapeHtml(a.asset_code)}<small class="shift-history-label">${escapeHtml(a.serial_number)}</small></td><td>${badge(a.status,a.status==='available' ? 'green' : 'gold')}</td><td>${escapeHtml(a.delivery_number || '-')}</td></tr>`).join('') || '<tr><td colspan="4">Belum ada unit asset.</td></tr>'}</tbody></table></div></section><section class="panel table-panel"><div class="panel-head"><h3>Barang belum kembali</h3></div><div class="table-wrap"><table class="data-table"><thead><tr><th>SURAT JALAN</th><th>TUJUAN</th><th>JUMLAH DI LUAR</th></tr></thead><tbody>${(data.outstanding || []).map(row=>`<tr><td>${escapeHtml(row.number)}</td><td>${escapeHtml(row.destination)}</td><td>${row.quantity}</td></tr>`).join('') || '<tr><td colspan="3">Tidak ada barang yang masih di luar.</td></tr>'}</tbody></table></div></section>`;
}
function prefillDeliverySpk(form,id) { const spk=deliveryBundle().spk_documents.find(d=>d.id===Number(id));if(spk){form.elements.event_id.value=spk.event_id || '';form.elements.destination.value=spk.venue || '';} }
async function handleInventoryToolsAction(action,element) {
  if(action==='edit-inventory-item') {
    if(!has('inventory.manage'))return true;const item=inventoryBundle().items.find(i=>i.id===Number(element.dataset.id));if(!item)return true;
    salesDrawer('Edit item',`<form id="inventory-item-form" class="sales-grid-form"><input type="hidden" name="id" value="${item.id}">${salesInput('SKU','sku',item.sku,'text','maxlength="64" required')}${salesInput('Barcode','barcode',item.barcode,'text','maxlength="64"')}${salesInput('Nama item','name',item.name,'text','maxlength="160" required')}${salesInput('Kategori','category',item.category,'text','maxlength="100"')}${salesInput('Satuan','unit',item.unit,'text','maxlength="24" required')}${salesInput('Min stok','min_quantity',item.min_quantity,'number','min="0" step="1"')}<button class="button button-primary" type="submit">Simpan item</button></form>`);return true;
  }
  if(action==='register-asset') {
    if(!has('inventory.manage'))return true;
    salesDrawer('Daftarkan unit asset',`<form id="inventory-asset-form" class="sales-grid-form"><input type="hidden" name="item_id" value="${Number(element.dataset.id)}">${salesInput('Kode asset unik','asset_code','','text','maxlength="64" required')}${salesInput('Serial number','serial_number','','text','maxlength="120"')}<label class="release-check sales-full"><input type="checkbox" name="existing_stock" value="1"> Beri kode untuk stok lama (tidak menambah stok)</label><button class="button button-primary" type="submit">Daftarkan asset</button></form>`);return true;
  }
  if(action==='edit-delivery-order') {
    const order=deliveryBundle().orders.find(o=>o.id===Number(element.dataset.id));if(!order || order.status!=='draft')return true;const form=$('#delivery-order-form');
    for(const name of ['id','spk_document_id','event_id','recipient_name','destination','notes'])form.elements[name].value=order[name] || '';
    $$('[data-delivery-item]',form).forEach(input=>{input.value=deliveryBundle().items.find(i=>i.delivery_order_id===order.id && i.item_id===Number(input.dataset.deliveryItem))?.planned_quantity || 0;});form.scrollIntoView({behavior:'smooth',block:'start'});return true;
  }
  if(['release-delivery-order','cancel-delivery-order'].includes(action)) {
    if(!window.confirm(action==='release-delivery-order' ? 'Rilis surat jalan? Daftar alat akan dikunci.' : 'Batalkan surat jalan?'))return true;element.disabled=true;
    try {await api('/api/delivery-orders',{method:'POST',body:JSON.stringify({id:Number(element.dataset.id),...(action==='release-delivery-order' ? {release:true} : {status:'cancelled'})})});await refreshData();}finally{element.disabled=false;}return true;
  }
  if(action==='scan-delivery-order'){state.page='inventory';renderShell();renderPage();$('#inventory-scan-form [name="delivery_order_id"]').value=element.dataset.id;return true;}
  if(action==='inventory-camera'){await openInventoryCamera();return true;}
  if(action==='close-inventory-camera'){await stopInventoryCamera();return true;}
  return false;
}
document.addEventListener('change',event=>{if(event.target.name==='spk_document_id' && event.target.form?.id==='delivery-order-form')prefillDeliverySpk(event.target.form,event.target.value);});
document.addEventListener('submit',async event=>{
  const form=event.target;if(form.id!=='inventory-asset-form')return;event.preventDefault();if(form.dataset.busy==='1')return;form.dataset.busy='1';const button=form.querySelector('button[type=submit]');button.disabled=true;
  try {const p=Object.fromEntries(new FormData(form));p.existing_stock=p.existing_stock==='1';await api('/api/inventory/assets',{method:'POST',body:JSON.stringify(p)});$('#modal-root').innerHTML='';await refreshData();toast('Asset terdaftar.');}catch(error){toast(error.message,'error');}finally{form.dataset.busy='0';button.disabled=false;}
});
let inventoryScanner=null,inventoryScannerLoad=null,inventoryCameraSequence=0;
function loadInventoryScanner() {
  if(window.Html5Qrcode)return Promise.resolve();
  if(!inventoryScannerLoad)inventoryScannerLoad=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='/vendor/html5-qrcode.min.js';script.onload=resolve;script.onerror=()=>{inventoryScannerLoad=null;script.remove();reject(new Error('Pemindai belum dapat dimuat. Gunakan scanner USB atau masukkan kode.'));};document.head.appendChild(script);});return inventoryScannerLoad;
}
async function stopInventoryCamera() {
  inventoryCameraSequence++;const scanner=inventoryScanner;inventoryScanner=null;
  if(scanner){try{if(scanner.isScanning)await scanner.stop();scanner.clear();}catch(_){}}
  $('#inventory-camera-dialog')?.close();
}
async function openInventoryCamera() {
  if(!has('inventory.scan') && !has('inventory.manage'))return;
  if(!$('#inventory-camera-dialog')) {
    const dialog=document.createElement('dialog');dialog.id='inventory-camera-dialog';dialog.className='inventory-camera-dialog';
    dialog.innerHTML='<div class="panel-head"><h3>Scan QR / barcode</h3><button class="button button-light" data-action="close-inventory-camera">Tutup</button></div><p id="inventory-camera-error" role="status"></p><div id="inventory-camera-view"></div><label class="field-label">Scan dari foto<input id="inventory-scan-photo" type="file" accept="image/*" capture="environment"></label>';
    document.body.appendChild(dialog);dialog.addEventListener('cancel',e=>{e.preventDefault();stopInventoryCamera();});
    $('#inventory-scan-photo').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;try{await loadInventoryScanner();if(inventoryScanner?.isScanning)await inventoryScanner.stop();inventoryScanner ||= new Html5Qrcode('inventory-camera-view',{verbose:false});const code=await inventoryScanner.scanFile(file,true);await acceptInventoryCode(code);}catch(_){$('#inventory-camera-error').textContent='Kode belum terbaca. Coba foto lebih dekat atau masukkan kode barang.';}finally{event.target.value='';}});
  }
  $('#inventory-camera-dialog').showModal();$('#inventory-camera-error').textContent='Membuka kamera...';const sequence=++inventoryCameraSequence;
  try {await loadInventoryScanner();if(sequence!==inventoryCameraSequence)return;
    if(!navigator.mediaDevices?.getUserMedia || !window.isSecureContext)throw new Error('Kamera memerlukan HTTPS dan izin kamera. Gunakan foto atau scanner USB.');
    const scanner=new Html5Qrcode('inventory-camera-view',{verbose:false});inventoryScanner=scanner;
    await scanner.start({facingMode:'environment'},{fps:8,qrbox:{width:240,height:180}},code=>{if(sequence===inventoryCameraSequence)acceptInventoryCode(code);},()=>{});
    if(sequence!==inventoryCameraSequence){if(scanner.isScanning)await scanner.stop();scanner.clear();return;}$('#inventory-camera-error').textContent='';
  }catch(error){if(sequence===inventoryCameraSequence)$('#inventory-camera-error').textContent=error?.message || 'Kamera tidak dapat dibuka. Periksa izin kamera, gunakan foto, atau scanner USB.';}
}
async function acceptInventoryCode(code) {const input=$('#inventory-scan-form [name="scan_code"]');if(input)input.value=code;await stopInventoryCamera();input?.focus();toast('Kode terbaca. Periksa arah dan surat jalan.');}
document.addEventListener('visibilitychange',()=>{if(document.hidden && inventoryScanner)stopInventoryCamera();});
