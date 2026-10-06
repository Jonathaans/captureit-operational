
let templates = [];
let editing = null;
let selectedSlot = null;
let addSlotMode = false;
let addSlotStart = null;
let slotPointerAction = null;
let templateDirty = false;
let canvasZoom = 0;
let canvasZoomMode = 'fit-all';
let canvasResizeFrame = null;
let previewStream = null;
let previewTransform = {rotation:0,mirrorHorizontal:false,mirrorVertical:false};
let backendRotationOffset = 180;
let activeSessionTransform = null;
let sessionPreviewActive = false;
let resumeBrowserPreviewAfterSession = false;
let sessionPreviewPollTimer = null;
let sessionPreviewPollToken = 0;
let sessionPreviewSequence = -1;
let sessionPreviewImageToken = 0;
let sessionPreviewObjectUrl = null;
let transformSaveTimer = null;
let configuredCameraName = '';
let configuredBrowserCameraLabel = '';
let directShowDevices = [];
let kioskModeActive = false;
let kioskSessionOwned = false;
let kioskAwaitingStart = false;
let kioskLastPhase = 'idle';
let kioskResetTimer = null;
let kioskResetRemaining = 0;
let kioskShownOutput = '';
let lastLoadedOutput = '';
let currentSettings = {};
let kioskCurrentPhoto = '';
let detectedPrinters = [];
let recommendedPrinter = '';
let kioskPreviewTemplate = null;
let kioskPreviewOverlay = null;
let kioskPreviewState = null;
let kioskCapturedFrames = {};
let kioskCaptureInFlight = {};
let kioskFrameTransformCanvas = null;
let kioskLatestFrameSource = null;
let kioskTemplateRenderScale = 1;
let browserCaptureActive = false;
let browserRecorder = null;
let browserRecorderChunks = [];
let browserSessionId = '';
let browserProgressSequence = 0;

const $ = id => document.getElementById(id);
const api = async (url, opts={}) => {
  const r = await fetch(url, opts);
  const data = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
};

document.querySelectorAll('.tab').forEach(btn=>{
  btn.onclick=async()=>{
    // The browser preview and FFmpeg DirectShow cannot reliably hold the same
    // webcam at the same time. Release the browser stream before opening
    // Settings so C920/UVC devices become visible to the DirectShow scan.
    if(btn.dataset.page==='settings' && previewStream && !sessionPreviewActive){
      await stopPreview();
      await new Promise(resolve=>setTimeout(resolve,250));
      $('previewMsg').textContent='Preview browser dihentikan sementara agar kamera dapat dipindai oleh FFmpeg.';
    }
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
    btn.classList.add('active'); $(btn.dataset.page).classList.add('active');
    $('appMain').classList.toggle('template-workspace-mode',btn.dataset.page==='templates');
    if(btn.dataset.page==='templates'&&editing){
      requestAnimationFrame(refreshCanvasZoom);
      setTimeout(refreshCanvasZoom,220);
    }
  };
});

async function loadTemplates(){
  templates = await api('/api/templates');
  const select = $('captureTemplate');
  const old = select.value;
  select.innerHTML = templates.map(t=>`<option value="${t.id}">${escapeHtml(t.name)}</option>`).join('');
  if(old && templates.some(t=>t.id===old)) select.value=old;
  if(!select.value && templates[0]) select.value=templates[0].id;
  renderTemplateList();
  updateCaptureTemplateInfo();
}
function escapeHtml(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function renderTemplateList(){
  $('templateList').innerHTML = templates.map(t=>`
    <div class="template-item ${editing?.id===t.id?'selected':''}" onclick="editTemplate('${t.id}')">
      <strong>${escapeHtml(t.name)}</strong>
      <div class="small">${t.canvas.width}×${t.canvas.height} • ${t.slots.length} slots • ${t.takeCount} take</div>
    </div>`).join('');
}
async function updateCaptureTemplateInfo(){
  const id=$('captureTemplate').value;if(!id)return;
  const t=await api(`/api/templates/${id}`);
  $('capTakeCount').textContent=t.takeCount;
  $('capCountdown').textContent=`${t.capture.countdown}s`;
  $('capOutput').textContent=`${t.canvas.width}×${t.canvas.height} • ${t.video.fps} FPS`;
}
$('captureTemplate').onchange=async()=>{ await updateCaptureTemplateInfo(); await updateWorkflowSummary(); updateKioskTemplateSummary(); };


async function updateWorkflowSummary(){
  const id=$('captureTemplate').value;if(!id)return;
  try{
    const info=await api(`/api/session/workflow/${id}`);
    const inputFps=info.inputFpsRequested>0?`${info.inputFpsRequested} FPS`:'Auto';
    $('workflowSummary').textContent=
      `${info.takeCount} sesi × ${info.countdownSeconds}s • input ${inputFps} → output ${info.outputFps} FPS • live preview ${info.previewFps} FPS`;
  }catch(e){
    $('workflowSummary').textContent='Workflow: -';
  }
}

async function listPreviewDevices(){
  try{
    // Ask permission once so device labels become visible.
    const temp = await navigator.mediaDevices.getUserMedia({video:true,audio:false});
    temp.getTracks().forEach(t=>t.stop());
  }catch(e){}
  const devices = await navigator.mediaDevices.enumerateDevices();
  const cams = devices.filter(d=>d.kind==='videoinput');
  const old=$('previewDevice').value;
  const oldSettings=$('cameraBrowserDevice')?.value||'';
  const optionHtml=cams.map((d,i)=>`<option value="${escapeHtml(d.deviceId)}">${escapeHtml(d.label || ('Camera '+(i+1)))}</option>`).join('');
  $('previewDevice').innerHTML = optionHtml;
  if($('cameraBrowserDevice'))$('cameraBrowserDevice').innerHTML = optionHtml;
  const preferred=cams.find(d=>{
    const label=(d.label||'').toLowerCase(),wanted=(configuredBrowserCameraLabel||configuredCameraName||'').toLowerCase();
    return wanted&&(label===wanted||label.includes(wanted)||wanted.includes(label));
  });
  if(old&&cams.some(d=>d.deviceId===old))$('previewDevice').value=old;
  else if(preferred)$('previewDevice').value=preferred.deviceId;
  const previewValue=$('previewDevice').value;
  if($('cameraBrowserDevice')){
    if(oldSettings&&cams.some(d=>d.deviceId===oldSettings))$('cameraBrowserDevice').value=oldSettings;
    else $('cameraBrowserDevice').value=previewValue || (preferred?.deviceId||'');
    const selected=$('cameraBrowserDevice').selectedOptions?.[0];
    configuredBrowserCameraLabel=selected?.textContent||configuredBrowserCameraLabel;
    if(!previewValue && selected)$('previewDevice').value=$('cameraBrowserDevice').value;
  }
  updateCameraSourceMessage();
  if(!cams.length) $('previewMsg').textContent='Tidak ada browser camera yang terdeteksi.';
}

function selectedBrowserDeviceId(){
  return $('cameraBrowserDevice')?.value || $('previewDevice')?.value || '';
}

function selectedBrowserCameraLabel(){
  return $('cameraBrowserDevice')?.selectedOptions?.[0]?.textContent
    || $('previewDevice')?.selectedOptions?.[0]?.textContent
    || configuredBrowserCameraLabel
    || 'Browser camera';
}

function updateCameraSourceMessage(){
  const mode=$('cameraSource')?.value||'auto';
  const browserReady=!!selectedBrowserDeviceId();
  const directReady=directShowDevices.length>0;
  if(!$('cameraSourceMsg'))return;
  if(mode==='browser'){
    $('cameraSourceMsg').textContent=browserReady
      ? `Mode Browser aktif: ${selectedBrowserCameraLabel()}. Rekaman sesi dibuat dari MediaRecorder browser.`
      : 'Mode Browser dipilih, tetapi belum ada browser camera. Klik Refresh pada kamera browser.';
  }else if(mode==='directshow'){
    $('cameraSourceMsg').textContent=directReady
      ? `${directShowDevices.length} DirectShow camera tersedia untuk FFmpeg.`
      : 'Tidak ada DirectShow camera. Pilih Mode Browser untuk memakai kamera yang tampil di preview.';
  }else{
    $('cameraSourceMsg').textContent=directReady
      ? `Auto akan memakai DirectShow (${directShowDevices[0]}) untuk sesi.`
      : (browserReady
        ? `Auto akan memakai Browser (${selectedBrowserCameraLabel()}) karena DirectShow kosong.`
        : 'Auto belum menemukan DirectShow atau browser camera.');
  }
}

async function stopPreview(){
  if(previewStream){
    previewStream.getTracks().forEach(t=>t.stop());
    previewStream = null;
  }
  $('livePreview').srcObject = null;
  $('kioskLivePreview').srcObject = null;
}

function applyPreviewTransform(media,stage,transform=previewTransform){
  const sourceWidth=Number(media.videoWidth||media.naturalWidth||0);
  const sourceHeight=Number(media.videoHeight||media.naturalHeight||0);
  if(!sourceWidth || !sourceHeight || !stage.clientWidth || !stage.clientHeight) return;

  const rotation=((Number(transform.rotation)||0)%360+360)%360;
  const quarterTurn=rotation===90 || rotation===270;
  const rotatedWidth=quarterTurn ? sourceHeight : sourceWidth;
  const rotatedHeight=quarterTurn ? sourceWidth : sourceHeight;
  const scale=Math.min(stage.clientWidth/rotatedWidth,stage.clientHeight/rotatedHeight);
  const mirrorX=transform.mirrorHorizontal ? -1 : 1;
  const mirrorY=transform.mirrorVertical ? -1 : 1;

  media.style.width=`${sourceWidth*scale}px`;
  media.style.height=`${sourceHeight*scale}px`;
  media.style.transform=`translate(-50%,-50%) scaleX(${mirrorX}) scaleY(${mirrorY}) rotate(${rotation}deg)`;
}
function applyBrowserPreviewTransform(){
  applyPreviewTransform($('livePreview'),$('liveStage'),previewTransform);
  applyPreviewTransform($('kioskLivePreview'),$('kioskStage'),previewTransform);
  const sessionTransform=activeSessionTransform||previewTransform;
  applyPreviewTransform($('sessionPreview'),$('liveStage'),sessionTransform);
  applyPreviewTransform($('kioskSessionPreview'),$('kioskStage'),sessionTransform);
}

function readTransformForm(){
  return {
    rotation:Number($('cameraRotation').value)||0,
    mirrorHorizontal:$('mirrorHorizontal').checked,
    mirrorVertical:$('mirrorVertical').checked
  };
}

function readBackendRotationOffset(){
  return Number($('cameraBackendRotationOffset').value)||0;
}

function effectiveFfmpegTransform(transform,offset=backendRotationOffset){
  return {
    rotation:((Number(transform.rotation)||0)+(Number(offset)||0))%360,
    mirrorHorizontal:!!transform.mirrorHorizontal,
    mirrorVertical:!!transform.mirrorVertical
  };
}

function transformFilterLabel(transform){
  const rotation=Number(transform.rotation)||0;
  const filters=[];
  if(rotation===90) filters.push('transpose=1');
  else if(rotation===180) filters.push('hflip','vflip');
  else if(rotation===270) filters.push('transpose=2');
  if(transform.mirrorHorizontal) filters.push('hflip');
  if(transform.mirrorVertical) filters.push('vflip');
  return filters.length?filters.join(','):'none';
}

function renderTransformStatus(transform, filter=transformFilterLabel(effectiveFfmpegTransform(transform)), prefix='Rotasi aktif', offset=backendRotationOffset){
  const mirrors=[];
  if(transform.mirrorHorizontal) mirrors.push('mirror horizontal');
  if(transform.mirrorVertical) mirrors.push('mirror vertical');
  $('transformStatus').textContent=
    `${prefix}: ${Number(transform.rotation)||0}°${mirrors.length?' + '+mirrors.join(' + '):''} • Koreksi sumber FFmpeg: ${Number(offset)||0}° • Filter: ${filter}`;
}

async function saveTransformOnly(){
  if(transformSaveTimer){clearTimeout(transformSaveTimer);transformSaveTimer=null;}
  const requested=readTransformForm();
  const requestedOffset=readBackendRotationOffset();
  previewTransform={...requested};
  backendRotationOffset=requestedOffset;
  applyBrowserPreviewTransform();
  const saved=await api('/api/settings/camera-transform',{
    method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      cameraTransform:requested,
      cameraBackendRotationOffset:requestedOffset
    })
  });
  previewTransform={...saved.cameraTransform};
  backendRotationOffset=Number(saved.cameraBackendRotationOffset)||0;
  renderTransformStatus(saved.cameraTransform,saved.cameraTransformFilter,'Rotasi aktif',backendRotationOffset);
  return saved;
}

function queueTransformSave(){
  const requested=readTransformForm();
  const requestedOffset=readBackendRotationOffset();
  previewTransform={...requested};
  backendRotationOffset=requestedOffset;
  applyBrowserPreviewTransform();
  renderTransformStatus(requested,transformFilterLabel(effectiveFfmpegTransform(requested,requestedOffset)),'Sedang diterapkan',requestedOffset);
  $('settingsMsg').textContent='Menyimpan Camera Transform…';
  if(transformSaveTimer) clearTimeout(transformSaveTimer);
  transformSaveTimer=setTimeout(async()=>{
    try{
      const saved=await saveTransformOnly();
      $('settingsMsg').textContent=`Camera Transform ${saved.cameraTransform.rotation}° tersimpan otomatis.`;
    }catch(e){
      $('settingsMsg').textContent='Gagal menyimpan Camera Transform: '+e.message;
    }
  },120);
}

function stopSessionPreviewPolling(){
  sessionPreviewPollToken++;
  sessionPreviewImageToken++;
  if(sessionPreviewPollTimer){
    clearTimeout(sessionPreviewPollTimer);
    sessionPreviewPollTimer=null;
  }
  sessionPreviewSequence=-1;
  if(sessionPreviewObjectUrl){
    URL.revokeObjectURL(sessionPreviewObjectUrl);
    sessionPreviewObjectUrl=null;
  }
  kioskLatestFrameSource=null;
}

async function pollSessionPreview(token){
  if(!sessionPreviewActive || token!==sessionPreviewPollToken) return;
  try{
    const response=await fetch(`/api/session/live-frame?after=${sessionPreviewSequence}&t=${Date.now()}`,{cache:'no-store'});
    const seq=Number(response.headers.get('X-Preview-Sequence'));
    if(Number.isFinite(seq)) sessionPreviewSequence=seq;
    if(response.status===200){
      const blob=await response.blob();
      if(blob.size && sessionPreviewActive && token===sessionPreviewPollToken){
        const nextUrl=URL.createObjectURL(blob);
        const frameToken=++sessionPreviewImageToken;
        const frameImage=new Image();
        frameImage.onload=()=>{
          if(!sessionPreviewActive||token!==sessionPreviewPollToken||frameToken!==sessionPreviewImageToken){
            URL.revokeObjectURL(nextUrl);
            return;
          }
          const previousUrl=sessionPreviewObjectUrl;
          sessionPreviewObjectUrl=nextUrl;
          kioskLatestFrameSource=frameImage;
          $('sessionPreview').src=nextUrl;
          $('kioskSessionPreview').src=nextUrl;
          requestAnimationFrame(applyBrowserPreviewTransform);
          requestAnimationFrame(()=>renderKioskTemplatePreview(kioskPreviewState));
          if(previousUrl)setTimeout(()=>URL.revokeObjectURL(previousUrl),120);
        };
        frameImage.onerror=()=>URL.revokeObjectURL(nextUrl);
        frameImage.src=nextUrl;
        $('previewMsg').textContent='Live preview sesi aktif — gambar ini berasal dari stream rekaman yang sama.';
      }
    }else if(response.status!==204){
      throw new Error(`HTTP ${response.status}`);
    }
  }catch(e){
    if(sessionPreviewActive && token===sessionPreviewPollToken){
      $('previewMsg').textContent='Menunggu frame live preview dari kamera…';
    }
  }finally{
    if(sessionPreviewActive && token===sessionPreviewPollToken){
      sessionPreviewPollTimer=setTimeout(()=>pollSessionPreview(token),20);
    }
  }
}

function showSessionPreview(active){
  const videos=[$('livePreview'),$('kioskLivePreview')];
  const images=[$('sessionPreview'),$('kioskSessionPreview')];
  const wasActive=sessionPreviewActive;
  sessionPreviewActive=active;
  if(active){
    videos.forEach(video=>video.style.display='none');
    images.forEach(image=>{
      image.style.display='block';
      if(sessionPreviewObjectUrl)image.src=sessionPreviewObjectUrl;
      else image.removeAttribute('src');
    });
    requestAnimationFrame(applyBrowserPreviewTransform);
    if(wasActive)return;
    stopSessionPreviewPolling();
    sessionPreviewActive=true;
    const token=++sessionPreviewPollToken;
    $('previewMsg').textContent='Menyiapkan live preview sesi…';
    pollSessionPreview(token);
  }else{
    if(!wasActive){
      images.forEach(image=>image.style.display='none');
      videos.forEach(video=>video.style.display='block');
      applyBrowserPreviewTransform();
      return;
    }
    stopSessionPreviewPolling();
    images.forEach(image=>{image.style.display='none';image.removeAttribute('src')});
    videos.forEach(video=>video.style.display='block');
    applyBrowserPreviewTransform();
  }
}

async function startPreview(){
  try{
    await stopPreview();
    const deviceId = $('previewDevice').value;
    const attempts=[];
    if(deviceId){
      attempts.push({video:{deviceId:{exact:deviceId},frameRate:{ideal:30,max:30}},audio:false});
      // Some UVC drivers reject a device-id and frame-rate constraint in the
      // same request. Retry the same camera without the FPS constraint.
      attempts.push({video:{deviceId:{exact:deviceId}},audio:false});
    }
    if(!deviceId) attempts.push({video:{frameRate:{ideal:30,max:30}},audio:false});
    let lastError=null;
    for(const constraints of attempts){
      try{
        previewStream = await navigator.mediaDevices.getUserMedia(constraints);
        break;
      }catch(e){lastError=e;}
    }
    if(!previewStream) throw lastError||new Error('Kamera tidak dapat dibuka.');
    $('livePreview').srcObject = previewStream;
    $('kioskLivePreview').srcObject = previewStream;
    showSessionPreview(false);
    await Promise.all([
      $('livePreview').play().catch(()=>{}),
      $('kioskLivePreview').play().catch(()=>{})
    ]);
    applyBrowserPreviewTransform();
    const selectedLabel=$('previewDevice').selectedOptions?.[0]?.textContent||'kamera';
    $('previewMsg').textContent = `Preview ${selectedLabel} aktif dengan target 30 FPS. ${shouldUseBrowserCapture()?'Mode Browser akan merekam stream ini.':'Saat sesi dimulai, tampilan berpindah ke live feed FFmpeg 20 FPS.'}`;
    return true;
  }catch(e){
    const detail=e?.name ? `${e.name}: ${e.message||'perangkat tidak dapat dibuka'}` : (e?.message||String(e));
    $('previewMsg').textContent = 'Preview gagal: ' + detail + ' Tutup Camera/Zoom/OBS lalu coba Refresh Cameras.';
    if(kioskModeActive)showKioskError('Kamera belum dapat dibuka. Silakan hubungi petugas.');
    return false;
  }
}

$('refreshPreviewDevices').onclick=()=>listPreviewDevices().catch(e=>$('previewMsg').textContent=e.message);
$('startPreview').onclick=()=>startPreview();
$('stopPreview').onclick=()=>stopPreview();
$('livePreview').addEventListener('loadedmetadata',applyBrowserPreviewTransform);
$('kioskLivePreview').addEventListener('loadedmetadata',applyBrowserPreviewTransform);
$('sessionPreview').addEventListener('load',applyBrowserPreviewTransform);
$('kioskSessionPreview').addEventListener('load',()=>{applyBrowserPreviewTransform();renderKioskTemplatePreview(kioskPreviewState)});
window.addEventListener('resize',()=>{
  applyBrowserPreviewTransform();
  resizeKioskTemplateCanvas();
  renderKioskTemplatePreview(kioskPreviewState);
  if(canvasResizeFrame)cancelAnimationFrame(canvasResizeFrame);
  canvasResizeFrame=requestAnimationFrame(refreshCanvasZoom);
});

$('importTemplate').onclick=async()=>{
  try{
    const file=$('newOverlay').files[0]; if(!file) throw new Error('Pilih overlay PNG.');
    const fd=new FormData(); fd.append('overlay',file); fd.append('name',$('newTemplateName').value||file.name.replace(/\.png$/i,''));
    $('importMsg').textContent='Detecting transparent slots...';
    const r=await fetch('/api/templates/import',{method:'POST',body:fd});
    const data=await r.json(); if(!r.ok) throw new Error(data.error||'Import gagal');
    $('importMsg').textContent=`Detected ${data.slotCount} slot • ${data.takeCount} take.`;
    await loadTemplates(); await editTemplate(data.templateId);
  }catch(e){$('importMsg').textContent=e.message}
};

async function editTemplate(id){
  editing=await api(`/api/templates/${id}`);
  selectedSlot=null;
  addSlotMode=false;
  addSlotStart=null;
  slotPointerAction=null;
  templateDirty=false;
  canvasZoom=0;
  canvasZoomMode='fit-all';
  $('templateEmpty').style.display='none';$('templateEditor').style.display='block';
  $('editorTitle').textContent=editing.name;
  $('editorMeta').textContent=`${editing.canvas.width} × ${editing.canvas.height}`;
  $('deleteTemplate').disabled=editing.id==='video-classic';
  $('deleteTemplate').title=editing.id==='video-classic'?'Sample template tidak dapat dihapus.':'';
  $('tplCountdown').value=editing.capture.countdown;
  $('tplPre').value=editing.capture.preSessionDelay||0;
  $('tplBetween').value=editing.capture.betweenTakes||0;
  $('tplCaptureDelay').value=editing.capture.captureDelay??0.25;
  $('tplDuration').value=editing.video.outputDuration;
  $('tplFps').value=editing.video.fps;
  $('tplFit').value=editing.video.fitMode||'cover';
  $('tplLoop').value=editing.video.loopMode||'none';
  $('tplVideoQuality').value=editing.video.quality||'hd';
  renderOverlayEditor(); renderSlotRows(); renderSelectedSlotEditor(); updateTakeCount(); renderTemplateList();
}
function updateTakeCount(){
  if(!editing)return;
  const tc=Math.max(1,...editing.slots.map(s=>Number(s.sourceTake)||1));
  $('tplTakeCount').value=tc;
}

const SLOT_HANDLES=['nw','n','ne','e','se','s','sw','w'];
const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
function updateCanvasToolbar(){
  const percent=Math.max(1,Math.round(canvasZoom*100));
  $('canvasZoomRange').value=String(clamp(percent,1,200));
  $('canvasZoomReadout').textContent=`${percent}%`;
  $('fitCanvasBtn').classList.toggle('active',canvasZoomMode==='fit-all');
  $('fitWidthBtn').classList.toggle('active',canvasZoomMode==='fit-width');
  $('actualSizeBtn').classList.toggle('active',canvasZoomMode==='actual');
}
function applyCanvasZoom(scale,mode='manual',preserveCenter=true){
  if(!editing)return;
  const viewport=$('canvasViewport'),wrap=$('overlayPreview');
  const oldScrollWidth=Math.max(1,viewport.scrollWidth),oldScrollHeight=Math.max(1,viewport.scrollHeight);
  const centerX=(viewport.scrollLeft+viewport.clientWidth/2)/oldScrollWidth;
  const centerY=(viewport.scrollTop+viewport.clientHeight/2)/oldScrollHeight;
  canvasZoom=clamp(Number(scale)||.01,.01,2);
  canvasZoomMode=mode;
  wrap.style.width=`${Math.max(1,editing.canvas.width*canvasZoom)}px`;
  wrap.style.height=`${Math.max(1,editing.canvas.height*canvasZoom)}px`;
  updateCanvasToolbar();
  requestAnimationFrame(()=>{
    if(!preserveCenter||mode==='fit-all'){
      viewport.scrollLeft=Math.max(0,(viewport.scrollWidth-viewport.clientWidth)/2);
      viewport.scrollTop=Math.max(0,(viewport.scrollHeight-viewport.clientHeight)/2);
      return;
    }
    viewport.scrollLeft=Math.max(0,viewport.scrollWidth*centerX-viewport.clientWidth/2);
    viewport.scrollTop=Math.max(0,viewport.scrollHeight*centerY-viewport.clientHeight/2);
  });
}
function fitCanvasAll(){
  if(!editing)return;
  const viewport=$('canvasViewport');
  const availableWidth=Math.max(80,viewport.clientWidth-68);
  const availableHeight=Math.max(80,viewport.clientHeight-68);
  applyCanvasZoom(Math.min(availableWidth/editing.canvas.width,availableHeight/editing.canvas.height),'fit-all',false);
}
function fitCanvasWidth(){
  if(!editing)return;
  const availableWidth=Math.max(80,$('canvasViewport').clientWidth-68);
  applyCanvasZoom(availableWidth/editing.canvas.width,'fit-width',false);
}
function refreshCanvasZoom(){
  if(!editing||!$('templateEditor').offsetParent)return;
  if(canvasZoomMode==='fit-all'||!canvasZoom)fitCanvasAll();
  else if(canvasZoomMode==='fit-width')fitCanvasWidth();
  else applyCanvasZoom(canvasZoom,canvasZoomMode,false);
}
function zoomCanvasBy(factor){
  if(!editing)return;
  applyCanvasZoom((canvasZoom||.1)*factor,'manual',true);
}
function minimumSlotSize(){
  if(!editing)return 8;
  return Math.max(8,Math.min(20,Math.round(Math.min(editing.canvas.width,editing.canvas.height)*.012)));
}
function normalizeSlot(slot){
  const cw=Math.max(1,Number(editing.canvas.width)||1);
  const ch=Math.max(1,Number(editing.canvas.height)||1);
  const minSize=minimumSlotSize();
  const width=clamp(Math.round(Number(slot.width)||minSize),minSize,cw);
  const height=clamp(Math.round(Number(slot.height)||minSize),minSize,ch);
  const x=clamp(Math.round(Number(slot.x)||0),0,cw-width);
  const y=clamp(Math.round(Number(slot.y)||0),0,ch-height);
  Object.assign(slot,{x,y,width,height});
  return slot;
}
function markTemplateDirty(message='Perubahan belum disimpan — klik Save.'){
  templateDirty=true;
  $('editorMeta').textContent=`${editing.canvas.width} × ${editing.canvas.height} • ${message}`;
}
function slotBox(index){
  return $('overlayPreview').querySelector(`.slot-box[data-slot-index="${index}"]`);
}
function syncSlotBox(index){
  const box=slotBox(index);if(!box||!editing?.slots[index])return;
  const s=editing.slots[index];
  box.style.left=(s.x/editing.canvas.width*100)+'%';
  box.style.top=(s.y/editing.canvas.height*100)+'%';
  box.style.width=(s.width/editing.canvas.width*100)+'%';
  box.style.height=(s.height/editing.canvas.height*100)+'%';
  const label=box.querySelector('.slot-label');
  if(label)label.textContent=`S${index+1} • T${s.sourceTake}`;
  const summary=$(`slotCoordinate${index}`);
  if(summary)summary.innerHTML=`${s.width}×${s.height}<br>@ ${s.x},${s.y}`;
  if(selectedSlot===index){
    const values={slotGeomX:s.x,slotGeomY:s.y,slotGeomWidth:s.width,slotGeomHeight:s.height};
    Object.entries(values).forEach(([id,value])=>{const el=$(id);if(el&&document.activeElement!==el)el.value=value});
  }
}
function updateSelectedBoxState(){
  $('overlayPreview').querySelectorAll('.slot-box').forEach((box,i)=>box.classList.toggle('active',i===selectedSlot));
}
function selectSlot(index,focusBox=false){
  if(!editing||index<0||index>=editing.slots.length)return;
  selectedSlot=index;
  updateSelectedBoxState();
  renderSlotRows();
  renderSelectedSlotEditor();
  if(focusBox)slotBox(index)?.focus({preventScroll:true});
}

function renderOverlayEditor(){
  const wrap=$('overlayPreview');
  wrap.innerHTML='';
  wrap.classList.toggle('adding',addSlotMode);
  const img=document.createElement('img');
  img.id='overlayImg';img.alt='Overlay template';
  img.onload=()=>requestAnimationFrame(refreshCanvasZoom);
  img.src=editing.overlayUrl;
  wrap.appendChild(img);
  editing.slots.forEach((s,i)=>{
    normalizeSlot(s);
    const d=document.createElement('div');
    d.className='slot-box'+(selectedSlot===i?' active':'');
    d.dataset.slotIndex=String(i);
    d.tabIndex=0;
    d.title='Drag untuk memindahkan • panah = 1 px • Shift + panah = 10 px';
    const label=document.createElement('span');label.className='slot-label';label.textContent=`S${i+1} • T${s.sourceTake}`;d.appendChild(label);
    SLOT_HANDLES.forEach(handle=>{
      const h=document.createElement('span');h.className='slot-handle';h.dataset.handle=handle;
      h.addEventListener('pointerdown',ev=>beginSlotPointer(ev,i,handle));d.appendChild(h);
    });
    d.addEventListener('pointerdown',ev=>beginSlotPointer(ev,i,'move'));
    d.addEventListener('keydown',ev=>{
      const delta=ev.shiftKey?10:1;
      const keys={ArrowLeft:[-delta,0],ArrowRight:[delta,0],ArrowUp:[0,-delta],ArrowDown:[0,delta]};
      if(!keys[ev.key])return;
      ev.preventDefault();selectSlot(i);nudgeSlot(i,keys[ev.key][0],keys[ev.key][1]);
    });
    wrap.appendChild(d);syncSlotBox(i);
  });
  wrap.onpointerdown=beginAddSlot;
  wrap.onpointermove=updateAddSlot;
  wrap.onpointerup=finishAddSlot;
  wrap.onpointercancel=cancelAddSlot;
  requestAnimationFrame(refreshCanvasZoom);
}
function previewPoint(event){
  const img=$('overlayImg');const rect=img.getBoundingClientRect();
  return {
    x:clamp((event.clientX-rect.left)/Math.max(1,rect.width)*editing.canvas.width,0,editing.canvas.width),
    y:clamp((event.clientY-rect.top)/Math.max(1,rect.height)*editing.canvas.height,0,editing.canvas.height)
  };
}
function setAddSlotMode(active){
  addSlotMode=!!active;addSlotStart=null;
  $('addSlotBtn').textContent=addSlotMode?'Klik-drag di Overlay…':'+ Add Slot';
  $('overlayPreview').classList.toggle('adding',addSlotMode);
  $('overlayPreview').querySelector('.slot-draft')?.remove();
}
function beginAddSlot(event){
  if(!addSlotMode||event.button!==0)return;
  event.preventDefault();
  const point=previewPoint(event);
  addSlotStart={pointerId:event.pointerId,x:point.x,y:point.y};
  const draft=document.createElement('div');draft.className='slot-draft';$('overlayPreview').appendChild(draft);
  $('overlayPreview').setPointerCapture?.(event.pointerId);
  updateAddSlot(event);
}
function updateAddSlot(event){
  if(!addSlotMode||!addSlotStart||event.pointerId!==addSlotStart.pointerId)return;
  event.preventDefault();
  const point=previewPoint(event);const draft=$('overlayPreview').querySelector('.slot-draft');if(!draft)return;
  const x=Math.min(addSlotStart.x,point.x),y=Math.min(addSlotStart.y,point.y);
  const width=Math.abs(point.x-addSlotStart.x),height=Math.abs(point.y-addSlotStart.y);
  draft.style.left=(x/editing.canvas.width*100)+'%';draft.style.top=(y/editing.canvas.height*100)+'%';
  draft.style.width=(width/editing.canvas.width*100)+'%';draft.style.height=(height/editing.canvas.height*100)+'%';
}
function finishAddSlot(event){
  if(!addSlotMode||!addSlotStart||event.pointerId!==addSlotStart.pointerId)return;
  const point=previewPoint(event),minSize=minimumSlotSize();
  const x=Math.round(Math.min(addSlotStart.x,point.x)),y=Math.round(Math.min(addSlotStart.y,point.y));
  const width=Math.round(Math.abs(point.x-addSlotStart.x)),height=Math.round(Math.abs(point.y-addSlotStart.y));
  if(width>=minSize&&height>=minSize){
    editing.slots.push(normalizeSlot({index:editing.slots.length+1,x,y,width,height,sourceTake:Math.max(1,...editing.slots.map(s=>Number(s.sourceTake)||1))}));
    selectedSlot=editing.slots.length-1;markTemplateDirty('Slot baru belum disimpan — klik Save.');
  }
  setAddSlotMode(false);renderOverlayEditor();renderSlotRows();renderSelectedSlotEditor();updateTakeCount();
}
function cancelAddSlot(event){
  if(!addSlotStart||event.pointerId!==addSlotStart.pointerId)return;
  setAddSlotMode(false);
}
function beginSlotPointer(event,index,mode){
  if(addSlotMode||event.button!==0)return;
  event.preventDefault();event.stopPropagation();selectSlot(index);
  const s=editing.slots[index];
  slotPointerAction={pointerId:event.pointerId,index,mode,startClientX:event.clientX,startClientY:event.clientY,start:{x:s.x,y:s.y,width:s.width,height:s.height},changed:false};
}
function updateSlotPointer(event){
  const a=slotPointerAction;if(!a||event.pointerId!==a.pointerId||!editing?.slots[a.index])return;
  event.preventDefault();
  const rect=$('overlayImg').getBoundingClientRect();
  const dx=(event.clientX-a.startClientX)/Math.max(1,rect.width)*editing.canvas.width;
  const dy=(event.clientY-a.startClientY)/Math.max(1,rect.height)*editing.canvas.height;
  const cw=editing.canvas.width,ch=editing.canvas.height,minSize=minimumSlotSize(),s=editing.slots[a.index],start=a.start;
  if(a.mode==='move'){
    s.x=Math.round(clamp(start.x+dx,0,cw-start.width));s.y=Math.round(clamp(start.y+dy,0,ch-start.height));
  }else{
    let left=start.x,top=start.y,right=start.x+start.width,bottom=start.y+start.height;
    if(a.mode.includes('w'))left=clamp(start.x+dx,0,right-minSize);
    if(a.mode.includes('e'))right=clamp(start.x+start.width+dx,left+minSize,cw);
    if(a.mode.includes('n'))top=clamp(start.y+dy,0,bottom-minSize);
    if(a.mode.includes('s'))bottom=clamp(start.y+start.height+dy,top+minSize,ch);
    s.x=Math.round(left);s.y=Math.round(top);s.width=Math.round(right-left);s.height=Math.round(bottom-top);
  }
  normalizeSlot(s);syncSlotBox(a.index);
  if(!a.changed){a.changed=true;markTemplateDirty()}
}
function finishSlotPointer(event){
  if(!slotPointerAction||event.pointerId!==slotPointerAction.pointerId)return;
  const changed=slotPointerAction.changed;slotPointerAction=null;
  if(changed){renderSlotRows();renderSelectedSlotEditor()}
}
document.addEventListener('pointermove',updateSlotPointer,{passive:false});
document.addEventListener('pointerup',finishSlotPointer);
document.addEventListener('pointercancel',finishSlotPointer);

$('fitCanvasBtn').onclick=fitCanvasAll;
$('fitWidthBtn').onclick=fitCanvasWidth;
$('actualSizeBtn').onclick=()=>applyCanvasZoom(1,'actual',false);
$('zoomOutBtn').onclick=()=>zoomCanvasBy(1/1.2);
$('zoomInBtn').onclick=()=>zoomCanvasBy(1.2);
$('canvasZoomRange').oninput=event=>applyCanvasZoom(Number(event.target.value)/100,'manual',true);
$('canvasViewport').addEventListener('wheel',event=>{
  if(!event.ctrlKey||!editing)return;
  event.preventDefault();
  zoomCanvasBy(event.deltaY<0?1.12:1/1.12);
},{passive:false});

$('addSlotBtn').onclick=()=>setAddSlotMode(!addSlotMode);
$('deleteSlotBtn').onclick=()=>{
  if(selectedSlot===null)return;
  editing.slots.splice(selectedSlot,1);editing.slots.forEach((s,i)=>s.index=i+1);selectedSlot=null;
  markTemplateDirty('Slot dihapus, belum disimpan — klik Save.');
  renderOverlayEditor();renderSlotRows();renderSelectedSlotEditor();updateTakeCount();
};
function renderSlotRows(){
  $('slotRows').innerHTML=editing.slots.map((s,i)=>`
    <div class="slot-row ${selectedSlot===i?'active':''}" onclick="selectSlot(${i})">
      <strong>S${i+1}</strong>
      <div><label>Take</label><input type="number" min="1" value="${s.sourceTake}" onchange="setSlotTake(${i},this.value)"></div>
      <div><label>Fit</label><select onchange="setSlotFit(${i},this.value)"><option value="" ${!s.fitMode?'selected':''}>Default</option><option value="cover" ${s.fitMode==='cover'?'selected':''}>Cover</option><option value="contain" ${s.fitMode==='contain'?'selected':''}>Contain</option></select></div>
      <div id="slotCoordinate${i}" class="fit small slot-coordinate">${s.width}×${s.height}<br>@ ${s.x},${s.y}</div>
    </div>`).join('');
}
function renderSelectedSlotEditor(){
  const panel=$('slotGeometryEditor');
  if(selectedSlot===null||!editing?.slots[selectedSlot]){
    panel.className='slot-adjust-panel empty';panel.textContent='Pilih sebuah slot pada overlay atau daftar di bawah.';return;
  }
  const i=selectedSlot,s=editing.slots[i],cw=editing.canvas.width,ch=editing.canvas.height;
  panel.className='slot-adjust-panel';
  panel.innerHTML=`
    <div class="slot-adjust-head"><strong>Slot ${i+1}</strong><span class="small">Kanvas ${cw}×${ch}</span></div>
    <div class="geometry-grid">
      <div><label>X</label><input id="slotGeomX" type="number" min="0" max="${cw-s.width}" value="${s.x}" onchange="setSlotGeometry(${i},'x',this.value)"></div>
      <div><label>Y</label><input id="slotGeomY" type="number" min="0" max="${ch-s.height}" value="${s.y}" onchange="setSlotGeometry(${i},'y',this.value)"></div>
      <div><label>Width</label><input id="slotGeomWidth" type="number" min="${minimumSlotSize()}" max="${cw-s.x}" value="${s.width}" onchange="setSlotGeometry(${i},'width',this.value)"></div>
      <div><label>Height</label><input id="slotGeomHeight" type="number" min="${minimumSlotSize()}" max="${ch-s.y}" value="${s.height}" onchange="setSlotGeometry(${i},'height',this.value)"></div>
    </div>
    <div class="slot-tools">
      <button type="button" onclick="nudgeSlot(${i},-1,0)" title="Geser kiri 1 px">←</button>
      <button type="button" onclick="nudgeSlot(${i},0,-1)" title="Geser atas 1 px">↑</button>
      <button type="button" onclick="nudgeSlot(${i},0,1)" title="Geser bawah 1 px">↓</button>
      <button type="button" onclick="nudgeSlot(${i},1,0)" title="Geser kanan 1 px">→</button>
      <button type="button" onclick="centerSlot(${i},'x')">Center X</button>
      <button type="button" onclick="centerSlot(${i},'y')">Center Y</button>
    </div>
    <div class="small" style="margin-top:9px">Drag kotak untuk pindah; tarik handle kuning untuk resize. Semua nilai otomatis dibatasi di dalam kanvas.</div>`;
}
window.selectSlot=selectSlot;
window.setSlotTake=(i,v)=>{editing.slots[i].sourceTake=Math.max(1,Number(v)||1);updateTakeCount();syncSlotBox(i);markTemplateDirty()};
window.setSlotFit=(i,v)=>{editing.slots[i].fitMode=v||undefined;markTemplateDirty()};
window.setSlotGeometry=(i,key,value)=>{
  const s=editing.slots[i],n=Math.round(Number(value));if(!s||!Number.isFinite(n))return;
  if(key==='x')s.x=clamp(n,0,editing.canvas.width-s.width);
  else if(key==='y')s.y=clamp(n,0,editing.canvas.height-s.height);
  else if(key==='width')s.width=clamp(n,minimumSlotSize(),editing.canvas.width-s.x);
  else if(key==='height')s.height=clamp(n,minimumSlotSize(),editing.canvas.height-s.y);
  normalizeSlot(s);syncSlotBox(i);renderSelectedSlotEditor();markTemplateDirty();
};
window.nudgeSlot=(i,dx,dy)=>{
  const s=editing.slots[i];if(!s)return;
  s.x=clamp(s.x+dx,0,editing.canvas.width-s.width);s.y=clamp(s.y+dy,0,editing.canvas.height-s.height);
  syncSlotBox(i);renderSelectedSlotEditor();markTemplateDirty();
};
window.centerSlot=(i,axis)=>{
  const s=editing.slots[i];if(!s)return;
  if(axis==='x')s.x=Math.round((editing.canvas.width-s.width)/2);
  if(axis==='y')s.y=Math.round((editing.canvas.height-s.height)/2);
  syncSlotBox(i);renderSelectedSlotEditor();markTemplateDirty();
};

$('deleteTemplate').onclick=async()=>{
  if(!editing)return;
  const id=editing.id,name=editing.name||editing.id;
  if(id==='video-classic'){$('templateMsg').textContent='Sample template tidak dapat dihapus.';return}
  if(!confirm(`Hapus template "${name}"?\n\nOverlay dan pengaturan slot template ini akan dihapus permanen.`))return;
  const button=$('deleteTemplate');
  try{
    button.disabled=true;button.textContent='Deleting…';
    await api(`/api/templates/${id}`,{method:'DELETE'});
    editing=null;selectedSlot=null;templateDirty=false;
    $('templateEditor').style.display='none';$('templateEmpty').style.display='block';
    $('templateMsg').textContent=`Template "${name}" berhasil dihapus.`;
    await loadTemplates();updateKioskTemplateSummary();
  }catch(e){
    $('templateMsg').textContent=`Gagal menghapus template: ${e.message}`;
  }finally{button.disabled=false;button.textContent='Delete Template'}
};

$('saveTemplate').onclick=async()=>{
  const button=$('saveTemplate');
  try{
    button.disabled=true;button.textContent='Saving…';
    editing.capture.countdown=Number($('tplCountdown').value);
    editing.capture.preSessionDelay=Number($('tplPre').value);
    editing.capture.betweenTakes=Number($('tplBetween').value);
    editing.capture.captureDelay=Math.max(.05,Number($('tplCaptureDelay').value)||.25);
    editing.video.outputDuration=Number($('tplDuration').value);
    editing.video.fps=Number($('tplFps').value);
    editing.video.fitMode=$('tplFit').value;
    editing.video.loopMode=$('tplLoop').value;
    editing.video.quality=$('tplVideoQuality').value;
    editing.slots.forEach((s,i)=>{s.index=i+1;normalizeSlot(s)});
    const id=editing.id;
    await api(`/api/templates/${id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(editing)});
    await loadTemplates();await editTemplate(id);
    templateDirty=false;$('editorMeta').textContent=`${editing.canvas.width} × ${editing.canvas.height} • Tersimpan.`;
  }catch(e){
    $('editorMeta').textContent=`${editing.canvas.width} × ${editing.canvas.height} • Gagal menyimpan: ${e.message}`;
  }finally{button.disabled=false;button.textContent='Save'}
};

['tplCountdown','tplPre','tplBetween','tplCaptureDelay','tplDuration','tplFps','tplFit','tplLoop','tplVideoQuality'].forEach(id=>{
  $(id).addEventListener('input',()=>{if(editing)markTemplateDirty()});
});

async function loadSettings(){
  const s=await api('/api/settings');
  currentSettings=s;
  configuredCameraName=s.cameraDevice||'';
  configuredBrowserCameraLabel=s.cameraBrowserLabel||'';
  $('ffmpegPath').value=s.ffmpegPath||'ffmpeg';
  $('cameraSource').value=['auto','directshow','browser'].includes(s.cameraSource)?s.cameraSource:'auto';
  $('cameraResolution').value=s.cameraResolution||'';
  $('cameraInputFps').value=s.cameraInputFps||0;
  $('cameraWarmupSeconds').value=s.cameraWarmupSeconds??3;
  const transform=s.cameraTransform||{};
  previewTransform={
    rotation:Number(transform.rotation)||0,
    mirrorHorizontal:!!transform.mirrorHorizontal,
    mirrorVertical:!!transform.mirrorVertical
  };
  $('cameraRotation').value=String(transform.rotation??0);
  backendRotationOffset=Number(s.cameraBackendRotationOffset)||0;
  $('cameraBackendRotationOffset').value=String(backendRotationOffset);
  $('mirrorHorizontal').checked=!!transform.mirrorHorizontal;
  $('mirrorVertical').checked=!!transform.mirrorVertical;
  renderTransformStatus(previewTransform,s.cameraTransformFilter,'Rotasi aktif',backendRotationOffset);
  $('hotEnabled').checked=!!s.hotFolder?.enabled;
  $('hotPath').value=s.hotFolder?.path||'';
  $('qrEnabled').checked=s.qr?.enabled!==false;
  $('qrMode').value=s.qr?.mode||'output';
  $('qrPublicBaseUrl').value=s.qr?.publicBaseUrl||'';
  $('qrFixedUrl').value=s.qr?.fixedUrl||'';
  $('qrLabel').value=s.qr?.label||'Scan untuk mengambil foto';
  $('qrAllowLan').checked=['0.0.0.0','::'].includes(s.server?.host||'');
  $('customQrMsg').textContent=s.customQrUploaded
    ? 'QR/barcode tersimpan dan siap ditampilkan.'
    : 'Belum ada QR/barcode yang diunggah.';
  $('desktopSyncEnabled').checked=!!s.desktopSync?.enabled;
  $('syncPhotoFolder').value=s.desktopSync?.photoFolder||'';
  $('syncVideoFolder').value=s.desktopSync?.videoFolder||'';
  $('syncVideos').checked=s.desktopSync?.syncVideos!==false;
  $('kioskResultTimeout').value=s.kiosk?.resultTimeoutSeconds||60;
  $('printerEnabled').checked=!!s.printer?.enabled;
  $('printerCopies').value=s.printer?.copies||1;
  $('printerStripMode').checked=s.printer?.fitMode==='strip2up';
  updatePrinterModeNotice();
  toggleQrModeFields();
  await refreshDevices(s.cameraDevice||'');
  await refreshPrinters(s.printer?.name||'');
  if(s.qr?.enabled)await refreshQrPreview();
  applyBrowserPreviewTransform();
}
async function refreshDevices(preferred=''){
  // A browser preview can keep an UVC camera claimed. Always release it before
  // asking FFmpeg to enumerate DirectShow devices, then it can be started again
  // from the Capture page.
  if(previewStream && !sessionPreviewActive){
    await stopPreview();
    // Give Windows/UVC a short moment to release the browser handle.
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  $('settingsMsg').textContent='Reading DirectShow devices...';
  try{
    const d=await api('/api/devices');
    let devices=d.devices||[];
    directShowDevices=devices.slice();
    const preferredAvailable=!!preferred&&devices.includes(preferred);
    if(!devices.length){
      $('cameraDevice').innerHTML='<option value="">Tidak ada kamera DirectShow terdeteksi</option>';
      $('settingsMsg').textContent='No DirectShow video device found. Tutup aplikasi kamera lain lalu klik Refresh.';
      configuredCameraName='';
      updateCameraSourceMessage();
      return;
    }
    const selected=preferredAvailable?preferred:devices[0];
    $('cameraDevice').innerHTML=devices.map(x=>`<option value="${escapeHtml(x)}" ${x===selected?'selected':''}>${escapeHtml(x)}</option>`).join('');
    configuredCameraName=selected;
    $('settingsMsg').textContent=preferredAvailable
      ? `${devices.length} DirectShow video device found.`
      : `${devices.length} DirectShow video device found. ${preferred||'Kamera sebelumnya'} tidak tersedia; dipilih ${selected}.`;
    updateCameraSourceMessage();
  }catch(e){$('settingsMsg').textContent=e.message}
}
$('refreshDevices').onclick=()=>refreshDevices($('cameraDevice').value);
async function refreshBrowserCameraList(){
  if(!navigator.mediaDevices?.enumerateDevices){
    $('cameraSourceMsg').textContent='Browser ini tidak mendukung daftar camera.';
    return;
  }
  try{ await listPreviewDevices(); }
  catch(e){ $('cameraSourceMsg').textContent='Gagal membaca browser camera: '+e.message; }
}
$('refreshBrowserCamera').onclick=refreshBrowserCameraList;
$('cameraBrowserDevice').addEventListener('change',()=>{
  const id=selectedBrowserDeviceId();
  if(id)$('previewDevice').value=id;
  configuredBrowserCameraLabel=selectedBrowserCameraLabel();
  updateCameraSourceMessage();
});
$('previewDevice').addEventListener('change',()=>{
  if($('cameraBrowserDevice') && $('previewDevice').value){
    $('cameraBrowserDevice').value=$('previewDevice').value;
    configuredBrowserCameraLabel=selectedBrowserCameraLabel();
  }
  updateCameraSourceMessage();
});
$('cameraSource').addEventListener('change',updateCameraSourceMessage);
function selectedPrintMode(){return $('printerStripMode').checked?'strip2up':'full4r'}
function printModeLabel(mode=selectedPrintMode()){return mode==='strip2up'?'Strip 2×6':'Full 4R (4×6)'}
function updatePrinterModeNotice(){
  $('printerModeNotice').textContent=$('printerStripMode').checked
    ? 'MODE STRIP: aplikasi mencetak dua strip 2×6 pada satu media 4×6. Set DNP 2inch cut = Enable.'
    : 'MODE FULL 4R: satu gambar memenuhi media 4×6 tanpa crop. Set DNP 2inch cut = Disable.';
}
$('printerStripMode').addEventListener('change',updatePrinterModeNotice);
function toggleQrModeFields(){
  const mode=$('qrMode').value;
  $('qrOutputFields').hidden=mode!=='output';
  $('qrFixedFields').hidden=mode!=='fixed';
  $('qrCustomFields').hidden=mode!=='custom';
}
$('qrMode').addEventListener('change',toggleQrModeFields);
async function refreshPrinters(preferred=''){
  $('printerSettingsMsg').textContent='Membaca printer Windows…';
  try{
    const d=await api('/api/printers');
    const printers=d.printers||[];
    detectedPrinters=printers;
    recommendedPrinter=d.recommendedPrinter||d.defaultPrinter||printers[0]||'';
    const selected=printers.includes(preferred)?preferred:recommendedPrinter;
    $('printerName').innerHTML=printers.length
      ? printers.map(name=>`<option value="${escapeHtml(name)}" ${name===selected?'selected':''}>${escapeHtml(name)}</option>`).join('')
      : '<option value="">Tidak ada printer terdeteksi</option>';
    if(currentSettings.printer&&selected)currentSettings.printer.name=selected;
    const backend=d.pywin32Ready?'PyWin32 GDI pixel-perfect':(d.powerShellReady?'Windows PrintDocument fallback':'backend print belum siap');
    $('printerSettingsMsg').textContent=d.supported
      ? (selected
        ? `${printers.length} printer ditemukan • Dipilih: ${selected} • ${backend}.`
        : 'Windows tidak mengembalikan printer. Pastikan driver printer terpasang lalu restart aplikasi.')
      : 'Daftar printer tersedia saat aplikasi dijalankan di Windows.';
    return d;
  }catch(e){$('printerSettingsMsg').textContent=e.message}
}
$('refreshPrinters').onclick=()=>refreshPrinters($('printerName').value);
$('openPrinterPreferences').onclick=async()=>{
  const button=$('openPrinterPreferences');
  try{
    button.disabled=true;
    const result=await api('/api/printers/preferences',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      printerName:$('printerName').value||recommendedPrinter
    })});
    const isDnp=/dnp|ds[- ]?rx|ds620|ds820|qw410/i.test(result.printer||'');
    $('printerSettingsMsg').textContent=isDnp
      ? `Printing Preferences ${result.printer} dibuka. Atur Paper Size (6×4), lalu 2inch cut ${selectedPrintMode()==='strip2up'?'Enable':'Disable'}.`
      : `Printing Preferences ${result.printer} dibuka. Atur ukuran kertas, orientasi, kualitas, dan borderless sesuai driver printer.`;
  }catch(e){$('printerSettingsMsg').textContent=e.message}
  finally{button.disabled=false}
};
$('testPrinter').onclick=async()=>{
  const button=$('testPrinter');
  try{
    button.disabled=true;button.textContent='Mengirim test print…';
    $('printerSettingsMsg').textContent='Menyiapkan halaman uji dan mengirimnya ke antrean Windows…';
    const result=await api('/api/print/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      printerName:$('printerName').value||recommendedPrinter,
      fitMode:selectedPrintMode()
    })});
    $('printerName').value=result.printer;
    $('printerEnabled').checked=true;
    await saveAllSettings(false);
    const resolved=result.details?.resolvedMode||selectedPrintMode();
    $('printerSettingsMsg').textContent=`Test ${printModeLabel(resolved)} dikirim ke ${result.printer} melalui ${result.method}. Print pada layar hasil sudah aktif.`;
  }catch(e){
    $('printerSettingsMsg').textContent='Test print gagal: '+e.message;
  }finally{button.disabled=false;button.textContent='Test Print'}
};
$('uploadCustomQr').onclick=async()=>{
  const file=$('customQrFile').files?.[0];
  if(!file){$('customQrMsg').textContent='Pilih file gambar QR/barcode terlebih dahulu.';return}
  const button=$('uploadCustomQr');
  try{
    button.disabled=true;button.textContent='Mengunggah…';
    const form=new FormData();form.append('qrImage',file);
    const response=await fetch('/api/qr/custom',{method:'POST',body:form});
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(result.error||`HTTP ${response.status}`);
    $('qrMode').value='custom';$('qrEnabled').checked=true;toggleQrModeFields();
    $('customQrMsg').textContent='QR/barcode berhasil disimpan dan mode custom sudah aktif.';
    currentSettings.customQrUploaded=true;
    await saveAllSettings(false);await refreshQrPreview();
  }catch(e){$('customQrMsg').textContent='Gagal mengunggah: '+e.message}
  finally{button.disabled=false;button.textContent='Upload / Ganti QR'}
};
$('testSyncFolders').onclick=async()=>{
  const button=$('testSyncFolders');
  try{
    button.disabled=true;button.textContent='Menguji…';$('syncSettingsMsg').textContent='Menguji akses tulis folder…';
    const result=await api('/api/desktop-sync/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      enabled:$('desktopSyncEnabled').checked,
      photoFolder:$('syncPhotoFolder').value.trim(),
      videoFolder:$('syncVideoFolder').value.trim(),
      syncVideos:$('syncVideos').checked
    })});
    const photo=result.folders?.photo?.ok?'Foto: siap':`Foto: ${result.folders?.photo?.error||'belum siap'}`;
    const video=!$('syncVideos').checked?'Video: dinonaktifkan':(result.folders?.video?.ok?'Video: siap':`Video: ${result.folders?.video?.error||'belum siap'}`);
    $('syncSettingsMsg').textContent=`${photo} • ${video}`;
  }catch(e){$('syncSettingsMsg').textContent='Test gagal: '+e.message}
  finally{button.disabled=false;button.textContent='Test Folder'}
};
async function refreshQrPreview(){
  try{
    const info=await api('/api/share?preview=1&photo=hasil-foto.jpg');
    $('qrSettingsPreview').hidden=!info.enabled;
    if(!info.enabled){$('qrSettingsMsg').textContent=info.message||'QR nonaktif atau tujuan belum tersedia.';return}
    $('qrPreviewImage').src=`${info.qrImageUrl}&t=${Date.now()}`;
    $('qrPreviewLabel').textContent=info.label||'Scan untuk mengambil foto';
    $('qrPreviewTarget').textContent=info.targetUrl;
    const localOnly=/\/\/(127\.0\.0\.1|localhost)(:|\/)/i.test(info.targetUrl||'');
    $('qrSettingsMsg').textContent=$('qrMode').value==='custom'
      ? 'QR/barcode custom siap dipakai pada setiap layar hasil.'
      : (localOnly
        ? 'QR masih memakai localhost dan tidak dapat dibuka dari HP. Aktifkan akses LAN atau isi Public Base URL.'
        : 'QR siap dipakai.');
  }catch(e){$('qrSettingsPreview').hidden=true;$('qrSettingsMsg').textContent=e.message}
}
$('refreshQrPreview').onclick=async()=>{
  try{await saveAllSettings(false);await refreshQrPreview()}catch(e){$('qrSettingsMsg').textContent=e.message}
};
async function saveAllSettings(announce=true){
  if(transformSaveTimer){clearTimeout(transformSaveTimer);transformSaveTimer=null;}
  const old=await api('/api/settings');
  const previousHost=old.server?.host||'127.0.0.1';
  old.ffmpegPath=$('ffmpegPath').value||'ffmpeg';
  old.cameraSource=$('cameraSource').value||'auto';
  old.cameraDevice=$('cameraDevice').value||'';
  old.cameraBrowserLabel=selectedBrowserCameraLabel();
  configuredCameraName=old.cameraDevice;
  configuredBrowserCameraLabel=old.cameraBrowserLabel;
  old.cameraResolution=$('cameraResolution').value.trim();
  old.cameraInputFps=Number($('cameraInputFps').value)||0;
  old.cameraWarmupSeconds=Math.max(0,Number($('cameraWarmupSeconds').value)||0);
  old.cameraTransform=readTransformForm();
  old.cameraBackendRotationOffset=readBackendRotationOffset();
  old.hotFolder={enabled:$('hotEnabled').checked,path:$('hotPath').value.trim()};
  old.desktopSync={
    enabled:$('desktopSyncEnabled').checked,
    photoFolder:$('syncPhotoFolder').value.trim(),
    videoFolder:$('syncVideoFolder').value.trim(),
    syncVideos:$('syncVideos').checked
  };
  old.qr={
    enabled:$('qrEnabled').checked,
    mode:$('qrMode').value,
    publicBaseUrl:$('qrPublicBaseUrl').value.trim(),
    fixedUrl:$('qrFixedUrl').value.trim(),
    label:$('qrLabel').value.trim()||'Scan untuk mengambil foto'
  };
  old.printer={
    enabled:$('printerEnabled').checked,
    name:$('printerName').value||'',
    copies:Math.max(1,Math.min(10,Number($('printerCopies').value)||1)),
    fitMode:selectedPrintMode(),
    modeVersion:3
  };
  old.kiosk={resultTimeoutSeconds:Math.max(15,Math.min(300,Number($('kioskResultTimeout').value)||60))};
  old.server=old.server||{port:5050};
  old.server.host=$('qrAllowLan').checked
    ? (['127.0.0.1','localhost',''].includes(previousHost)?'0.0.0.0':previousHost)
    : '127.0.0.1';
  const saved=await api('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(old)});
  currentSettings={...old,qr:saved.qr,printer:saved.printer,kiosk:saved.kiosk,desktopSync:saved.desktopSync,customQrUploaded:saved.customQrUploaded};
  previewTransform={...saved.cameraTransform};
  backendRotationOffset=Number(saved.cameraBackendRotationOffset)||0;
  applyBrowserPreviewTransform();
  renderTransformStatus(saved.cameraTransform,saved.cameraTransformFilter,'Rotasi aktif',backendRotationOffset);
  if(announce){
    const restart=previousHost!==old.server.host?' • Restart aplikasi untuk perubahan akses LAN.':'';
    $('settingsMsg').textContent=`Semua setting tersimpan. Input FPS ${old.cameraInputFps||'Auto'} • Rotation ${saved.cameraTransform.rotation}°${restart}`;
    await refreshQrPreview();
  }
  return saved;
}
$('saveSettings').onclick=async()=>{
  try{await saveAllSettings(true)}catch(e){$('settingsMsg').textContent='Gagal menyimpan: '+e.message}
};
$('cameraRotation').addEventListener('change',queueTransformSave);
$('cameraBackendRotationOffset').addEventListener('change',queueTransformSave);
$('mirrorHorizontal').addEventListener('change',queueTransformSave);
$('mirrorVertical').addEventListener('change',queueTransformSave);
$('testCamera').onclick=async()=>{
  const button=$('testCamera');
  const hadBrowserPreview=!!previewStream;
  try{
    button.disabled=true;
    await saveAllSettings(false);
    await stopPreview();
    const active=readTransformForm();
    $('settingsMsg').textContent=`Merekam Camera Test 3 detik dengan Rotation ${active.rotation}°…`;
    const d=await api('/api/camera/test',{method:'POST'});
    const player=$('cameraTestVideo');
    player.src=d.url;
    player.load();
    await player.play().catch(()=>{});
    backendRotationOffset=Number(d.cameraBackendRotationOffset)||0;
    renderTransformStatus(d.cameraTransform,d.cameraTransformFilter,'Rotasi aktif',backendRotationOffset);
    $('settingsMsg').textContent=`Camera Test diputar otomatis. Input ${d.inputFpsRequested||'Auto'} FPS • Rotation ${d.cameraTransform.rotation}° • koreksi FFmpeg ${backendRotationOffset}°.`;
  }catch(e){
    $('settingsMsg').textContent='Camera Test gagal: '+e.message;
  }finally{
    button.disabled=false;
    if(hadBrowserPreview) startPreview();
  }
};

const CAMERA_PHASES=['starting','warmup','prepare','recording','capture','between'];
const ACTIVE_PHASES=[...CAMERA_PHASES,'extracting','rendering'];
function sessionIsActive(phase){return ACTIVE_PHASES.includes(phase)}
function showKioskPanel(panel){
  $('kioskApp').dataset.phase=panel==='kioskDonePanel'?'done':'capture';
  ['kioskIdlePanel','kioskActivePanel','kioskDonePanel','kioskErrorPanel'].forEach(id=>$(id).hidden=id!==panel);
}
function setKioskCameraBadge(label,state='preview'){
  const badge=$('kioskCameraBadge');
  if(badge.dataset.state!==state)badge.dataset.state=state;
  if($('kioskCameraLabel').textContent!==label)$('kioskCameraLabel').textContent=label;
}
function updateKioskTemplateSummary(){
  const template=templates.find(t=>t.id===$('captureTemplate').value);
  const takeCount=Math.max(1,Number(template?.takeCount)||1);
  const countdown=Number(template?.capture?.countdown)||5;
  $('kioskTemplateName').textContent=template?.name||'Template aktif';
  $('kioskIdleDescription').textContent=`${takeCount} sesi foto • ${countdown} detik per pose. Lihat ke kamera lalu sentuh tombol mulai.`;
  setKioskPreviewTemplate(template);
  if(!kioskSessionOwned)renderKioskProgress({takeCount,take:0,phase:'idle'});
}

function setKioskPreviewTemplate(template){
  const id=template?.id||'';
  if(kioskPreviewTemplate?.id===id&&kioskPreviewOverlay)return;
  kioskPreviewTemplate=template||null;
  kioskPreviewOverlay=null;
  const canvas=$('kioskTemplatePreview');
  if(!template){
    canvas.width=1;canvas.height=1;canvas.style.display='none';
    $('kioskStage').classList.remove('template-preview-active','template-preview-portrait','template-processing');
    return;
  }
  const image=new Image();
  image.onload=()=>{
    if(kioskPreviewTemplate?.id!==id)return;
    kioskPreviewOverlay=image;
    renderKioskTemplatePreview(kioskPreviewState);
  };
  image.onerror=()=>{ kioskPreviewOverlay=null; };
  image.src=`/template-assets/${encodeURIComponent(id)}/${encodeURIComponent(template.overlay||'overlay.png')}?t=${Date.now()}`;
}

function setKioskTemplatePreviewVisible(visible){
  const stage=$('kioskStage');
  const canvas=$('kioskTemplatePreview');
  const wasVisible=canvas.style.display==='block';
  stage.classList.toggle('template-preview-active',!!visible);
  stage.classList.toggle('template-preview-portrait',!!visible&&Number(kioskPreviewTemplate?.canvas?.height||0)>Number(kioskPreviewTemplate?.canvas?.width||0)*1.2);
  if(!visible)stage.classList.remove('template-processing');
  canvas.style.display=visible?'block':'none';
  if(visible&&!wasVisible) resizeKioskTemplateCanvas();
}

function resizeKioskTemplateCanvas(){
  const template=kioskPreviewTemplate;
  const canvas=$('kioskTemplatePreview');
  const stage=$('kioskStage');
  if(!template||!stage.clientWidth||!stage.clientHeight)return;
  const width=Number(template.canvas?.width)||1;
  const height=Number(template.canvas?.height)||1;
  const scale=Math.min(stage.clientWidth/width,stage.clientHeight/height);
  canvas.style.width=`${Math.max(1,width*scale)}px`;
  canvas.style.height=`${Math.max(1,height*scale)}px`;
  canvas.style.left='50%';canvas.style.top='50%';
  canvas.style.transform='translate(-50%,-50%)';
}

function kioskTemplateRenderSurface(template){
  const width=Math.max(1,Number(template.canvas?.width)||1);
  const height=Math.max(1,Number(template.canvas?.height)||1);
  const maxDimension=1800;
  const scale=Math.min(1,maxDimension/Math.max(width,height));
  const canvas=$('kioskTemplatePreview');
  const targetWidth=Math.max(2,Math.round(width*scale));
  const targetHeight=Math.max(2,Math.round(height*scale));
  if(canvas.width!==targetWidth||canvas.height!==targetHeight){
    canvas.width=targetWidth;canvas.height=targetHeight;
  }
  kioskTemplateRenderScale=scale;
  return {canvas,ctx:canvas.getContext('2d'),scale,width,height};
}

function transformedKioskFrame(source,transform){
  const sourceWidth=Number(source?.naturalWidth||source?.videoWidth||source?.width||0);
  const sourceHeight=Number(source?.naturalHeight||source?.videoHeight||source?.height||0);
  if(!sourceWidth||!sourceHeight)return null;
  const rotation=((Number(transform?.rotation)||0)%360+360)%360;
  const quarterTurn=rotation===90||rotation===270;
  const outputWidth=quarterTurn?sourceHeight:sourceWidth;
  const outputHeight=quarterTurn?sourceWidth:sourceHeight;
  if(!kioskFrameTransformCanvas)kioskFrameTransformCanvas=document.createElement('canvas');
  kioskFrameTransformCanvas.width=outputWidth;
  kioskFrameTransformCanvas.height=outputHeight;
  const ctx=kioskFrameTransformCanvas.getContext('2d');
  ctx.clearRect(0,0,outputWidth,outputHeight);
  ctx.save();
  ctx.translate(outputWidth/2,outputHeight/2);
  ctx.rotate(rotation*Math.PI/180);
  ctx.scale(transform?.mirrorHorizontal?-1:1,transform?.mirrorVertical?-1:1);
  ctx.drawImage(source,-sourceWidth/2,-sourceHeight/2,sourceWidth,sourceHeight);
  ctx.restore();
  return kioskFrameTransformCanvas;
}

function drawKioskSlot(ctx,source,slot,scale,fitMode='cover'){
  if(!source)return;
  const x=Number(slot.x||0)*scale;
  const y=Number(slot.y||0)*scale;
  const width=Math.max(1,Number(slot.width||1)*scale);
  const height=Math.max(1,Number(slot.height||1)*scale);
  const sourceWidth=Number(source.naturalWidth||source.videoWidth||source.width||0);
  const sourceHeight=Number(source.naturalHeight||source.videoHeight||source.height||0);
  if(!sourceWidth||!sourceHeight)return;
  const contain=String(fitMode||'cover').toLowerCase()==='contain';
  const ratio=contain?Math.min(width/sourceWidth,height/sourceHeight):Math.max(width/sourceWidth,height/sourceHeight);
  const drawWidth=sourceWidth*ratio;
  const drawHeight=sourceHeight*ratio;
  const drawX=x+(width-drawWidth)/2;
  const drawY=y+(height-drawHeight)/2;
  ctx.save();
  ctx.beginPath();ctx.rect(x,y,width,height);ctx.clip();
  ctx.fillStyle='#090909';ctx.fillRect(x,y,width,height);
  ctx.drawImage(source,drawX,drawY,drawWidth,drawHeight);
  ctx.restore();
}

function activeKioskTake(state,template){
  const count=Math.max(1,Number(template?.takeCount)||1);
  let take=Math.max(1,Number(state?.take)||1);
  if(state?.phase==='between')take++;
  return Math.min(count,take);
}

function renderKioskTemplatePreview(state=kioskPreviewState){
  const template=kioskPreviewTemplate;
  if(!template||!kioskPreviewOverlay||!state||!kioskModeActive)return;
  const activePhases=CAMERA_PHASES.includes(state.phase)||['extracting','rendering'].includes(state.phase);
  if(!activePhases){setKioskTemplatePreviewVisible(false);return;}
  const {canvas,ctx,scale,width,height}=kioskTemplateRenderSurface(template);
  const currentSource=transformedKioskFrame(kioskLatestFrameSource||$('kioskSessionPreview'),activeSessionTransform||previewTransform);
  if(!currentSource&&!Object.keys(kioskCapturedFrames).length){
    // Keep the last complete canvas while the next MJPEG frame is decoding.
    // Clearing it here causes the visible blink between polling frames.
    return;
  }
  ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle='#050505';ctx.fillRect(0,0,canvas.width,canvas.height);
  const activeTake=activeKioskTake(state,template);
  const defaultFit=template.video?.fitMode||'cover';
  for(const slot of (template.slots||[])){
    const take=Math.max(1,Number(slot.sourceTake)||1);
    const captured=kioskCapturedFrames[take];
    const source=captured||((take===activeTake&&activePhases)?currentSource:null);
    drawKioskSlot(ctx,source,slot,scale,slot.fitMode||defaultFit);
  }
  ctx.drawImage(kioskPreviewOverlay,0,0,width*scale,height*scale);
  setKioskTemplatePreviewVisible(true);
}

function captureKioskTakeFrame(take,attempt=0){
  take=Math.max(1,Number(take)||1);
  if(kioskCapturedFrames[take]||(kioskCaptureInFlight[take]&&attempt===0))return;
  kioskCaptureInFlight[take]=true;
  const source=kioskLatestFrameSource||$('kioskSessionPreview');
  if(!source.complete||!source.naturalWidth){
    if(attempt<8){setTimeout(()=>captureKioskTakeFrame(take,attempt+1),60);return;}
    delete kioskCaptureInFlight[take];
    return;
  }
  const frame=transformedKioskFrame(source,activeSessionTransform||previewTransform);
  if(!frame){delete kioskCaptureInFlight[take];return;}
  try{
    const url=frame.toDataURL('image/jpeg',.92);
    const image=new Image();
    image.onload=()=>{
      kioskCapturedFrames[take]=image;
      delete kioskCaptureInFlight[take];
      renderKioskTemplatePreview(kioskPreviewState);
    };
    image.onerror=()=>{delete kioskCaptureInFlight[take]};
    image.src=url;
  }catch(e){delete kioskCaptureInFlight[take]}
}

function resetKioskTemplatePreview(){
  kioskCapturedFrames={};
  kioskCaptureInFlight={};
  kioskLatestFrameSource=null;
  kioskPreviewState=null;
  setKioskTemplatePreviewVisible(false);
}
function renderKioskProgress(s){
  const template=templates.find(t=>t.id===$('captureTemplate').value);
  const count=Math.max(1,Number(s.takeCount)||Number(template?.takeCount)||1);
  const take=Math.max(0,Number(s.take)||0);
  let completed=Math.max(0,take-1);
  if(['between','extracting','rendering'].includes(s.phase))completed=Math.min(count,take);
  if(s.phase==='done')completed=count;
  $('kioskProgress').style.gridTemplateColumns=`repeat(${count},minmax(0,1fr))`;
  $('kioskProgress').innerHTML=Array.from({length:count},(_,index)=>{
    const number=index+1;
    const state=number<=completed?' done':number===take&&sessionIsActive(s.phase)?' current':'';
    const caption=number<=completed?'SELESAI':number===take&&sessionIsActive(s.phase)?'AKTIF':'FOTO';
    return `<div class="kiosk-step${state}" aria-label="Foto ${number}: ${caption.toLowerCase()}"><span class="kiosk-step-index">${String(number).padStart(2,'0')}</span><span class="kiosk-step-caption">${caption}</span></div>`;
  }).join('');
  if(s.phase==='done')$('kioskProgressLabel').textContent='Semua foto selesai';
  else if(take>0)$('kioskProgressLabel').textContent=`Foto ${Math.min(take,count)} dari ${count}`;
  else $('kioskProgressLabel').textContent=`${count} sesi foto siap dimulai`;
}
function clearKioskResetTimer(){
  if(kioskResetTimer){clearInterval(kioskResetTimer);kioskResetTimer=null}
  kioskResetRemaining=0;
}
function resetKioskExperience(restartPreview=true){
  clearKioskResetTimer();
  kioskSessionOwned=false;kioskAwaitingStart=false;kioskShownOutput='';kioskLastPhase='idle';kioskCurrentPhoto='';
  resetKioskTemplatePreview();
  $('kioskResultPreview').style.display='none';$('kioskResultPreview').removeAttribute('src');
  const video=$('kioskVideoPreview');
  video.pause();video.removeAttribute('src');video.removeAttribute('poster');video.load();
  $('kioskVideoBlock').hidden=true;$('kioskVideoHint').textContent='Putar preview';
  $('kioskQrBlock').hidden=true;$('kioskQrImage').removeAttribute('src');
  $('kioskPrint').hidden=true;$('kioskPrint').disabled=false;$('kioskPrint').textContent='Cetak Foto';
  $('kioskPrintStatus').textContent='';$('kioskPrintStatus').className='kiosk-print-status';
  $('kioskCount').className='kiosk-count';$('kioskCount').textContent='';
  $('kioskFlash').classList.remove('active');
  $('kioskTake').textContent='PREVIEW';$('kioskStageMessage').textContent='Lihat ke kamera';
  setKioskCameraBadge('LIVE PREVIEW');
  $('kioskHeaderStatus').textContent='SIAP DIGUNAKAN';
  showKioskPanel('kioskIdlePanel');updateKioskTemplateSummary();
  if(kioskModeActive&&restartPreview&&!previewStream)startPreview();
}
function showKioskError(message){
  clearKioskResetTimer();
  setKioskTemplatePreviewVisible(false);
  $('kioskErrorText').textContent=message||'Silakan hubungi petugas.';
  $('kioskHeaderStatus').textContent='PERLU BANTUAN';
  setKioskCameraBadge('BUTUH BANTUAN','error');
  showKioskPanel('kioskErrorPanel');
}
async function requestKioskFullscreen(){
  try{if(!document.fullscreenElement)await document.documentElement.requestFullscreen()}catch(e){}
}
function openKiosk(useFullscreen=false){
  kioskModeActive=true;
  document.body.classList.add('kiosk-active');
  $('kioskApp').classList.add('active');$('kioskApp').setAttribute('aria-hidden','false');
  resetKioskExperience(false);
  if(previewStream){
    $('kioskLivePreview').srcObject=previewStream;
    $('kioskLivePreview').play().catch(()=>{});
  }else startPreview();
  requestAnimationFrame(applyBrowserPreviewTransform);
  if(useFullscreen)requestKioskFullscreen();
}
function closeKiosk(){
  if(sessionIsActive(kioskLastPhase))api('/api/session/cancel',{method:'POST'}).catch(()=>{});
  clearKioskResetTimer();kioskModeActive=false;kioskSessionOwned=false;kioskAwaitingStart=false;
  resetKioskTemplatePreview();
  $('kioskApp').classList.remove('active');$('kioskApp').setAttribute('aria-hidden','true');
  document.body.classList.remove('kiosk-active');
  if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});
  if(location.pathname.replace(/\/+$/,'')==='/kiosk')history.replaceState(null,'','/');
}

function shouldUseBrowserCapture(){
  const mode=$('cameraSource')?.value||currentSettings.cameraSource||'auto';
  if(mode==='browser')return true;
  if(mode==='directshow')return false;
  return directShowDevices.length===0;
}

function browserProgress(sessionId,phase,take,takeCount,remaining,message){
  if(!sessionId)return;
  const sequence=++browserProgressSequence;
  api('/api/session/browser/progress',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sessionId,sequence,phase,take,takeCount,remaining,message})
  }).catch(()=>{});
}

async function runBrowserPhase(sessionId,phase,duration,take,takeCount,message){
  const total=Math.max(0,Number(duration)||0);
  const started=performance.now();
  let lastPush=0;
  while(true){
    const elapsed=(performance.now()-started)/1000;
    const remaining=Math.max(0,total-elapsed);
    if(performance.now()-lastPush>120 || remaining<=0){
      browserProgress(sessionId,phase,take,takeCount,remaining,message);
      lastPush=performance.now();
    }
    if(remaining<=0)break;
    await new Promise(resolve=>setTimeout(resolve,70));
  }
  browserProgress(sessionId,phase,take,takeCount,0,message);
}

function chooseRecorderMimeType(){
  if(typeof MediaRecorder==='undefined')return '';
  const candidates=['video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm'];
  if(typeof MediaRecorder.isTypeSupported!=='function')return candidates[1];
  return candidates.find(type=>MediaRecorder.isTypeSupported(type))||'';
}

function stopBrowserRecorder(recorder){
  return new Promise((resolve,reject)=>{
    if(!recorder||recorder.state==='inactive'){resolve();return;}
    const onError=e=>{cleanup();reject(e?.error||new Error('MediaRecorder gagal.'))};
    const cleanup=()=>{
      recorder.removeEventListener('stop',onStop);
      recorder.removeEventListener('error',onError);
    };
    const onStop=()=>{cleanup();resolve()};
    recorder.addEventListener('stop',onStop,{once:true});
    recorder.addEventListener('error',onError,{once:true});
    try{recorder.stop()}catch(e){cleanup();reject(e)}
  });
}

async function startBrowserCaptureSession(origin='admin'){
  let hadBrowserPreview=!!previewStream;
  let recorder=null;
  let sessionId='';
  try{
    if(!navigator.mediaDevices?.getUserMedia)throw new Error('Browser ini tidak mendukung camera capture.');
    const requestedBrowserId=selectedBrowserDeviceId();
    const activeBrowserId=previewStream?.getVideoTracks?.()[0]?.getSettings?.().deviceId||'';
    if(previewStream && requestedBrowserId && activeBrowserId && requestedBrowserId!==activeBrowserId){
      await stopPreview();
    }
    if(!previewStream){
      const ok=await startPreview();
      if(!ok||!previewStream)throw new Error('Kamera browser belum dapat dibuka.');
      hadBrowserPreview=true;
    }
    const mimeType=chooseRecorderMimeType();
    if(!mimeType)throw new Error('Browser tidak mendukung MediaRecorder WebM. Gunakan Chrome atau Edge terbaru.');
    const templateId=$('captureTemplate').value;
    const workflow=await api(`/api/session/workflow/${templateId}`);
    const transformResult=await saveTransformOnly();
    activeSessionTransform={...(transformResult.cameraTransform||previewTransform)};
    const started=await api('/api/session/browser/start',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({templateId,cameraTransform:transformResult.cameraTransform,cameraBackendRotationOffset:0})
    });
    sessionId=started.sessionId;
    browserSessionId=sessionId;
    browserProgressSequence=0;
    browserCaptureActive=true;
    $('statusText').textContent='Merekam dari kamera browser…';
    showSessionPreview(false);
    if(kioskModeActive){kioskLatestFrameSource=$('kioskLivePreview');}

    const chunks=[];
    browserRecorderChunks=chunks;
    recorder=new MediaRecorder(previewStream,{mimeType,videoBitsPerSecond:8000000});
    browserRecorder=recorder;
    recorder.ondataavailable=e=>{if(e.data&&e.data.size)chunks.push(e.data)};
    recorder.start(100);
    const recordingStarted=performance.now();
    const takeCount=Math.max(1,Number(workflow.takeCount)||1);
    const warmup=Math.max(0,Number(workflow.warmupSeconds)||0);
    const preDelay=Math.max(0,Number(workflow.preSessionDelay||0));
    const countdown=Math.max(.1,Number(workflow.countdownSeconds)||5);
    const captureDelay=Math.max(.05,Number(workflow.captureDelay)||.25);
    const between=Math.max(0,Number(workflow.betweenTakes)||0);
    const ranges=[];

    browserProgress(sessionId,'starting',0,takeCount,0,'Menyiapkan kamera browser');
    await runBrowserPhase(sessionId,'warmup',warmup,0,takeCount,'Menyiapkan kamera browser');
    await runBrowserPhase(sessionId,'prepare',preDelay,0,takeCount,'Siap-siap');
    for(let take=1;take<=takeCount;take++){
      const takeStart=Math.max(0,(performance.now()-recordingStarted)/1000);
      await runBrowserPhase(sessionId,'recording',countdown,take,takeCount,`Sesi ${take}/${takeCount} — pertahankan pose`);
      ranges.push({start:Math.max(0,takeStart-.03),duration:countdown+.06});
      await runBrowserPhase(sessionId,'capture',captureDelay,take,takeCount,'Foto diambil');
      if(take<takeCount)await runBrowserPhase(sessionId,'between',between,take,takeCount,'Siapkan pose berikutnya');
    }
    // Give MediaRecorder one final chunk after the last take.
    await new Promise(resolve=>setTimeout(resolve,260));
    await stopBrowserRecorder(recorder);
    recorder=null;browserRecorder=null;
    const blob=new Blob(chunks,{type:mimeType});
    if(blob.size<1024)throw new Error('Video browser kosong. Coba Start Preview lalu ulangi.');
    $('statusText').textContent='Mengunggah video browser…';
    const form=new FormData();
    form.append('sessionId',sessionId);
    form.append('ranges',JSON.stringify(ranges));
    form.append('video',blob,`recordcountdown_${sessionId}.webm`);
    await api('/api/session/browser/upload',{method:'POST',body:form});
    browserCaptureActive=false;
    browserSessionId='';
    browserRecorderChunks=[];
    $('previewMsg').textContent='Video browser diterima. Menyiapkan foto dan video hasil…';
    return true;
  }catch(e){
    if(recorder){try{await stopBrowserRecorder(recorder)}catch(_){} }
    browserRecorder=null;browserRecorderChunks=[];browserCaptureActive=false;
    if(sessionId){api('/api/session/cancel',{method:'POST'}).catch(()=>{});}
    browserSessionId='';activeSessionTransform=null;
    const detail=e?.message||String(e);
    $('statusText').textContent=detail;
    $('previewMsg').textContent='Mode browser gagal: '+detail;
    if(origin==='kiosk')showKioskError(detail||'Sesi belum dapat dimulai. Silakan hubungi petugas.');
    if(!hadBrowserPreview&&!previewStream)startPreview();
    return false;
  }
}

async function startCaptureSession(origin='admin'){
  if(shouldUseBrowserCapture())return startBrowserCaptureSession(origin);
  const hadBrowserPreview=!!previewStream;
  try{
    // Lock the transform that is visibly active before handing the camera to
    // FFmpeg. The backend receives this exact value instead of relying on a
    // delayed autosave that may still contain the previous rotation.
    const transformResult=await saveTransformOnly();
    activeSessionTransform={...(transformResult.sessionPreviewTransform||effectiveFfmpegTransform(transformResult.cameraTransform,transformResult.cameraBackendRotationOffset))};
    resumeBrowserPreviewAfterSession=hadBrowserPreview;
    await stopPreview();
    const started=await api('/api/session/start',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        templateId:$('captureTemplate').value,
        cameraTransform:transformResult.cameraTransform,
        cameraBackendRotationOffset:transformResult.cameraBackendRotationOffset
      })
    });
    activeSessionTransform={...(started.sessionPreviewTransform||activeSessionTransform)};
    showSessionPreview(true);
    $('previewMsg').textContent='Menyiapkan live preview sesi…';
    return true;
  }catch(e){
    activeSessionTransform=null;
    showSessionPreview(false);
    if(hadBrowserPreview) startPreview();
    $('statusText').textContent=e.message;
    if(origin==='kiosk')showKioskError(e.message||'Sesi belum dapat dimulai. Silakan hubungi petugas.');
    return false;
  }
}
async function startKioskSession(){
  if(!$('captureTemplate').value){showKioskError('Belum ada template aktif. Silakan hubungi petugas.');return}
  clearKioskResetTimer();kioskSessionOwned=true;kioskAwaitingStart=true;kioskShownOutput='';
  resetKioskTemplatePreview();
  $('kioskResultPreview').style.display='none';
  $('kioskHeaderStatus').textContent='MENYIAPKAN SESI';
  $('kioskActiveTitle').textContent='Bersiap…';
  $('kioskActiveDescription').textContent='Lihat ke kamera dan siapkan pose pertama.';
  $('kioskLiveStatus').textContent='Menghubungkan kamera…';
  showKioskPanel('kioskActivePanel');
  const ok=await startCaptureSession('kiosk');
  kioskAwaitingStart=false;
  if(!ok)kioskSessionOwned=false;
}
function startKioskResetCountdown(){
  clearKioskResetTimer();
  kioskResetRemaining=Math.max(15,Math.min(300,Number(currentSettings.kiosk?.resultTimeoutSeconds)||60));
  const update=()=>$('kioskResetNote').textContent=`Kembali ke layar awal dalam ${kioskResetRemaining} detik`;
  update();
  kioskResetTimer=setInterval(()=>{
    kioskResetRemaining--;
    if(kioskResetRemaining<=0){resetKioskExperience(true);return}
    update();
  },1000);
}
function showKioskResult(s){
  if(!s.outputPhoto||kioskShownOutput===s.outputPhoto)return;
  kioskShownOutput=s.outputPhoto;kioskCurrentPhoto=s.outputPhoto;
  setKioskTemplatePreviewVisible(false);
  $('kioskResultPreview').src=`${s.outputPhoto}?t=${Date.now()}`;
  $('kioskResultPreview').style.display='block';
  $('kioskDownloadPhoto').href=s.outputPhoto;
  $('kioskOpenVideo').href=s.outputVideo||'#';
  $('kioskOpenVideo').style.display=s.outputVideo?'flex':'none';
  const video=$('kioskVideoPreview');
  video.pause();video.removeAttribute('src');video.removeAttribute('poster');
  if(s.outputVideo){
    video.src=`${s.outputVideo}?t=${Date.now()}`;
    video.poster=`${s.outputPhoto}?t=${Date.now()}`;
    video.load();
    $('kioskVideoBlock').hidden=false;
    $('kioskVideoHint').textContent='Tekan play untuk menonton';
  }else{
    $('kioskVideoBlock').hidden=true;
  }
  $('kioskQrBlock').hidden=true;
  $('kioskPrintStatus').textContent='';$('kioskPrintStatus').className='kiosk-print-status';
  const printCfg=currentSettings.printer||{};
  $('kioskPrint').hidden=false;
  $('kioskPrint').disabled=true;
  $('kioskPrint').textContent=printCfg.enabled?'Memeriksa printer…':'Printer Belum Diaktifkan';
  if(!printCfg.enabled){
        $('kioskPrintStatus').textContent='Aktifkan printer foto di Settings atau jalankan Test Print.';
  }else{
    api('/api/printers').then(info=>{
      if(kioskShownOutput!==s.outputPhoto)return;
      const printers=info.printers||[];
      const selected=printers.includes(printCfg.name)
        ? printCfg.name
        : (info.recommendedPrinter||info.defaultPrinter||printers[0]||'');
      if(!selected){
        $('kioskPrint').textContent='Printer Tidak Terdeteksi';
        $('kioskPrintStatus').textContent='Periksa kabel, driver printer, lalu tekan Refresh di Settings.';
        return;
      }
      currentSettings.printer.name=selected;
      $('kioskPrint').disabled=false;
      const configuredMode=printCfg.fitMode==='strip2up'?'strip2up':'full4r';
      $('kioskPrint').textContent=printCfg.copies>1?`Cetak ${printCfg.copies} Copy`:`Cetak ${printModeLabel(configuredMode)}`;
      $('kioskPrintStatus').textContent=`Siap mencetak ${printModeLabel(configuredMode)} ke ${selected}.`;
    }).catch(e=>{
      $('kioskPrint').textContent='Printer Tidak Siap';
      $('kioskPrintStatus').className='kiosk-print-status error';
      $('kioskPrintStatus').textContent=e.message;
    });
  }
  api(`/api/share?photo=${encodeURIComponent(s.outputPhoto)}`).then(info=>{
    if(kioskShownOutput!==s.outputPhoto||!info.enabled)return;
    $('kioskQrImage').src=`${info.qrImageUrl}&t=${Date.now()}`;
    $('kioskQrLabel').textContent=info.label||'Scan untuk mengambil foto';
    $('kioskQrBlock').hidden=false;
  }).catch(()=>{$('kioskQrBlock').hidden=true});
  $('kioskHeaderStatus').textContent='HASIL SIAP';
  setKioskCameraBadge('HASIL FOTO','result');
  showKioskPanel('kioskDonePanel');startKioskResetCountdown();
}
function updateKioskState(s){
  kioskLastPhase=s.phase||'idle';
  if(!kioskModeActive)return;
  kioskPreviewState=s;
  $('kioskStage').classList.toggle('template-processing',['extracting','rendering'].includes(s.phase));
  renderKioskProgress(s);
  if(CAMERA_PHASES.includes(s.phase)){
    $('kioskResultPreview').style.display='none';
    if(s.phase==='capture')captureKioskTakeFrame(s.take);
    if(s.phase==='between')captureKioskTakeFrame(s.take);
    renderKioskTemplatePreview(s);
    $('kioskHeaderStatus').textContent='SESI BERJALAN';
    setKioskCameraBadge(s.phase==='recording'?'SEDANG MEREKAM':'LIVE PREVIEW',s.phase==='recording'?'recording':'preview');
    showKioskPanel('kioskActivePanel');
    if(s.phase==='recording'){
      $('kioskActiveTitle').textContent=`Pose foto ${s.take||1}`;
      $('kioskActiveDescription').textContent='Pertahankan pose sampai hitungan selesai.';
      $('kioskLiveStatus').textContent=`Foto diambil dalam ${Math.max(1,Math.ceil(s.remaining||0))} detik`;
    }else if(s.phase==='capture'){
      $('kioskActiveTitle').textContent='Senyum!';$('kioskLiveStatus').textContent='Foto berhasil diambil.';
    }else if(s.phase==='between'){
      $('kioskActiveTitle').textContent='Ganti pose';$('kioskLiveStatus').textContent='Bersiap untuk foto berikutnya.';
    }else{
      $('kioskActiveTitle').textContent='Bersiap…';$('kioskLiveStatus').textContent=s.message||'Menyiapkan kamera…';
    }
  }else if(['extracting','rendering'].includes(s.phase)&&kioskSessionOwned){
    $('kioskHeaderStatus').textContent='MEMBUAT HASIL';showKioskPanel('kioskActivePanel');
    setKioskCameraBadge('MEMPROSES HASIL','result');
    $('kioskActiveTitle').textContent='Menyusun hasil…';
    $('kioskActiveDescription').textContent='Semua foto selesai. Mohon tunggu sebentar.';
    $('kioskLiveStatus').textContent=s.message||'Memproses foto dan video…';
  }else if(s.phase==='done'&&kioskSessionOwned&&!kioskAwaitingStart){
    showKioskResult(s);
  }else if(s.phase==='error'&&kioskSessionOwned){
    kioskSessionOwned=false;showKioskError(s.error||'Sesi mengalami kendala. Silakan hubungi petugas.');
  }
}

$('startSession').onclick=()=>startCaptureSession('admin');
$('cancelSession').onclick=()=>api('/api/session/cancel',{method:'POST'}).catch(()=>{});
$('launchKiosk').onclick=()=>openKiosk(true);
$('kioskStart').onclick=startKioskSession;
$('kioskAgain').onclick=()=>{resetKioskExperience(false);startKioskSession()};
$('kioskPrint').onclick=async()=>{
  if(!kioskCurrentPhoto)return;
  const button=$('kioskPrint');
  clearKioskResetTimer();button.disabled=true;button.textContent='Mengirim ke printer…';
  $('kioskPrintStatus').className='kiosk-print-status';$('kioskPrintStatus').textContent='Mohon tunggu.';
  try{
    const result=await api('/api/print',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      photo:kioskCurrentPhoto,
      printerName:currentSettings.printer?.name||''
    })});
    button.textContent='✓ Terkirim ke Printer';
    const resolved=result.details?.resolvedMode||currentSettings.printer?.fitMode||'full4r';
    $('kioskPrintStatus').textContent=`${result.copies} copy ${printModeLabel(resolved)} dikirim ke ${result.printer}.`;
  }catch(e){
    button.disabled=false;button.textContent='Coba Print Lagi';
    $('kioskPrintStatus').className='kiosk-print-status error';$('kioskPrintStatus').textContent=e.message;
  }finally{startKioskResetCountdown()}
};
$('kioskRetry').onclick=()=>{resetKioskExperience(false);startPreview()};
$('kioskFullscreen').onclick=requestKioskFullscreen;
$('kioskIdleFullscreen').onclick=requestKioskFullscreen;
document.addEventListener('keydown',event=>{
  if(kioskModeActive&&event.ctrlKey&&event.shiftKey&&event.key.toLowerCase()==='a'){
    event.preventDefault();closeKiosk();
  }
});

function updatePreviewOverlay(s){
  const count=$('previewCount');
  const take=$('previewTake');
  const flash=$('captureFlash');
  const kioskCount=$('kioskCount');
  const kioskTake=$('kioskTake');
  const kioskFlash=$('kioskFlash');
  count.className='preview-count';
  count.textContent='';
  flash.classList.remove('active');
  take.textContent=s.takeCount ? `SESI ${Math.max(1,s.take||1)} / ${s.takeCount}` : 'PREVIEW';
  kioskCount.className='kiosk-count';
  kioskCount.textContent='';
  kioskFlash.classList.remove('active');
  kioskTake.textContent=s.takeCount ? `FOTO ${Math.max(1,s.take||1)} / ${s.takeCount}` : 'PREVIEW';
  $('kioskStageMessage').textContent='Lihat ke kamera';

  if(s.phase==='recording'){
    const value=String(Math.max(1,Math.ceil(s.remaining||0)));
    count.textContent=value;kioskCount.textContent=value;
    count.classList.add('visible');
    kioskCount.classList.add('visible');$('kioskStageMessage').textContent='Pertahankan pose';
  }else if(s.phase==='capture'){
    count.textContent='📸';kioskCount.textContent='📸';
    count.classList.add('visible');
    kioskCount.classList.add('visible');flash.classList.add('active');kioskFlash.classList.add('active');
    $('kioskStageMessage').textContent='Foto diambil';
  }else if(s.phase==='between'){
    count.textContent='GANTI POSE';kioskCount.textContent='GANTI POSE';
    count.classList.add('visible','word');
    kioskCount.classList.add('visible','word');$('kioskStageMessage').textContent='Siapkan pose berikutnya';
  }else if(['starting','warmup','prepare'].includes(s.phase)){
    count.textContent='SIAP-SIAP';kioskCount.textContent='SIAP-SIAP';
    count.classList.add('visible','word');
    kioskCount.classList.add('visible','word');$('kioskStageMessage').textContent='Lihat ke kamera';
  }else if(['extracting','rendering'].includes(s.phase)){
    kioskCount.textContent='MEMPROSES';kioskCount.classList.add('visible','word');
    $('kioskStageMessage').textContent='Menyusun hasil foto dan video';
  }
}

function updateFpsStatus(s){
  const el=$('fpsStatus');
  const stats=s.captureStats;
  el.className='notice fps-status';
  if(!stats){
    el.textContent='FPS kamera: menunggu sesi.';
    return;
  }
  const output=Number(stats.outputFps)||30;
  const preview=Number(stats.previewFps)||20;
  const encoded=Number(stats.encodedFrames)||0;
  const effective=Number(stats.effectiveSourceFps)||0;
  const duplicate=Number(stats.duplicatePercent)||0;
  const requested=Number(stats.inputFpsRequested)||0;
  if(encoded<5){
    el.textContent=`Target: input ${requested||'Auto'} FPS • output ${output} FPS • live preview ${preview} FPS`;
    return;
  }
  const healthy=effective>=output*.8 && duplicate<=20;
  el.classList.add(healthy?'good':'warn');
  el.textContent=`Kamera efektif ≈ ${effective.toFixed(1)} FPS • output ${output} FPS • preview ${preview} FPS • frame duplikat ${duplicate.toFixed(1)}%`;
}

async function pollStatus(){
  try{
    const s=await api('/api/session/status');
    $('phaseBadge').textContent=s.phase;
    $('statusText').textContent=s.message||s.phase;
    $('takeLabel').textContent=s.takeCount?`Sesi ${s.take||0} / ${s.takeCount}`:'Sesi –';
    $('sessionError').textContent=s.error||'';
    const cameraActive=CAMERA_PHASES.includes(s.phase);
    const active=sessionIsActive(s.phase);
    if(cameraActive&&(s.sessionPreviewTransform||s.cameraTransform)){
      const sessionTransform=s.sessionPreviewTransform||s.cameraTransform;
      activeSessionTransform={
        rotation:Number(sessionTransform.rotation)||0,
        mirrorHorizontal:!!sessionTransform.mirrorHorizontal,
        mirrorVertical:!!sessionTransform.mirrorVertical
      };
      if(sessionPreviewActive)requestAnimationFrame(applyBrowserPreviewTransform);
    }
    if(s.phase==='recording'){
      $('bigCount').textContent=Math.max(1,Math.ceil(s.remaining||0));
    }else if(s.phase==='capture'){
      $('bigCount').textContent='📸';
    }else if(['warmup','prepare','between'].includes(s.phase)){
      $('bigCount').textContent=Math.max(0,Math.ceil(s.remaining||0));
    }else if(['starting','extracting','rendering'].includes(s.phase)) $('bigCount').textContent='…';
    else if(s.phase==='done') $('bigCount').textContent='✓';
    else $('bigCount').textContent='–';
    $('startSession').disabled=active;
    $('startPreview').disabled=cameraActive;
    $('stopPreview').disabled=cameraActive;
    updatePreviewOverlay(s);
    updateFpsStatus(s);
    updateKioskState(s);

    if(cameraActive && !sessionPreviewActive && !browserCaptureActive){
      showSessionPreview(true);
    }else if(sessionPreviewActive && !cameraActive && ['extracting','rendering','done','error','idle'].includes(s.phase)){
      showSessionPreview(false);
      activeSessionTransform=null;
      if(resumeBrowserPreviewAfterSession){
        resumeBrowserPreviewAfterSession=false;
        startPreview();
      }
    }
    let links='';
    if(s.outputVideo) links+=`<a href="${s.outputVideo}" target="_blank">Open MP4</a>`;
    if(s.outputPhoto) links+=`${links?' &nbsp;•&nbsp; ':''}<a href="${s.outputPhoto}" target="_blank">Open JPG</a>`;
    $('resultLinks').innerHTML=links;
    if(s.phase==='done'&&s.outputPhoto&&lastLoadedOutput!==s.outputPhoto){lastLoadedOutput=s.outputPhoto;loadOutputs()}
  }catch(e){}
}
async function loadOutputs(){
  try{
    const items=await api('/api/outputs');
    $('outputs').innerHTML=items.map(x=>`<div class="output"><div><strong>${escapeHtml(x.name)}</strong><div class="small">${escapeHtml(x.category||'Output')} • ${escapeHtml(x.modified)}</div></div><a href="${x.url}" target="_blank">Open</a></div>`).join('')||'<div class="small">No output yet.</div>';
  }catch(e){}
}

(async()=>{
  await loadTemplates(); await loadSettings(); await loadOutputs(); await updateWorkflowSummary();
  if(navigator.mediaDevices && navigator.mediaDevices.enumerateDevices){
    try{ await listPreviewDevices(); }catch(e){ $('previewMsg').textContent=e.message; }
  } else {
    $('previewMsg').textContent='Browser ini tidak mendukung mediaDevices.';
  }
  updateKioskTemplateSummary();
  if(location.pathname.replace(/\/+$/,'')==='/kiosk')openKiosk(false);
  setInterval(pollStatus,100);
})();
