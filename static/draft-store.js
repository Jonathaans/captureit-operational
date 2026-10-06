/* Text is written synchronously; compressed photos use IndexedDB, not localStorage. */
function draftToken() {
  return globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function devicePhotoStorage() {
  let opening;
  function database() {
    if(!opening) opening=new Promise((resolve,reject)=>{
      if(!globalThis.indexedDB) {reject(new Error('Penyimpanan foto tidak tersedia.'));return;}
      let expired=false;
      const timer=setTimeout(()=>{expired=true;reject(new Error('Penyimpanan foto tidak merespons.'));},8000);
      const request=indexedDB.open('captureit-device-drafts',1);
      request.onupgradeneeded=()=>request.result.createObjectStore('photos');
      request.onsuccess=()=>{clearTimeout(timer);if(expired){request.result.close();return;}request.result.onversionchange=()=>request.result.close();resolve(request.result);};
      request.onerror=()=>{clearTimeout(timer);reject(request.error);};
      request.onblocked=()=>{expired=true;clearTimeout(timer);reject(new Error('Penyimpanan foto sedang dipakai tab lain.'));};
    }).catch(error=>{opening=null;throw error;});
    return opening;
  }
  async function run(mode,action) {
    const db=await database();
    return new Promise((resolve,reject)=>{
      const tx=db.transaction('photos',mode),request=action(tx.objectStore('photos'));
      const timer=setTimeout(()=>{try{tx.abort();}catch{}reject(new Error('Penyimpanan foto tidak merespons.'));},8000);
      tx.oncomplete=()=>{clearTimeout(timer);resolve(request.result);};
      tx.onerror=tx.onabort=()=>{clearTimeout(timer);reject(tx.error||new Error('Foto belum tersimpan di perangkat.'));};
    });
  }
  return {put:(key,value)=>run('readwrite',store=>store.put(value,key)),get:key=>run('readonly',store=>store.get(key)),remove:key=>run('readwrite',store=>store.delete(key))};
}

class DeviceDraftStore {
  constructor(storage=()=>globalThis.localStorage,photos=devicePhotoStorage()) {
    this.storage=storage;this.photos=photos;this.photoWrites=new Map();
  }
  key(namespace,userId,eventId) {
    if(!namespace||!userId||!eventId) throw new Error('Identitas draft belum tersedia. Muat ulang aplikasi.');
    return `captureit:closing:v1:${namespace}:${userId}:${eventId}`;
  }
  read(key) {
    const raw=this.storage().getItem(key);if(!raw) return null;
    const record=JSON.parse(raw);
    if(record.format!==1||!Number.isInteger(record.version)||!record.data||!Array.isArray(record.photos)||!record.token) throw new Error('Draft perangkat tidak dapat dibaca.');
    return record;
  }
  write(key,draft,expectedToken) {
    const previous=this.read(key);
    if((previous?.token||null)!==(expectedToken||null)) {
      const error=new Error('Draft berubah di tab lain.');error.code='draft_conflict';throw error;
    }
    const photos=draft.photos.map(photo=>{
      if(photo.id) return {id:photo.id,kind:photo.kind,caption:photo.caption,url:photo.url,missing:photo.missing||undefined};
      photo.local_id ||= draftToken();
      return {local_id:photo.local_id,kind:photo.kind,caption:photo.caption};
    });
    const photoIds=[...new Set([...(draft.photo_ids||[]),...(previous?.photo_ids||[]),...photos.map(p=>p.local_id).filter(Boolean)])];
    const record={format:1,version:draft.version,data:structuredClone(draft.data),photos,photo_ids:photoIds,token:draftToken(),updated_at:new Date().toISOString()};
    this.storage().setItem(key,JSON.stringify(record));
    const pending=draft.photos.map(photo=>{
      if(photo.missing) return Promise.reject(new Error('Ada foto draft yang tidak tersedia. Tambahkan ulang atau hapus foto tersebut.'));
      if(photo.id) return Promise.resolve();
      if(!photo.image) return Promise.reject(new Error('Ada foto draft yang tidak tersedia. Tambahkan ulang atau hapus foto tersebut.'));
      const photoKey=`${key}:${photo.local_id}`;
      if(!this.photoWrites.has(photoKey)) {
        const write=this.photos.put(photoKey,photo.image).catch(error=>{this.photoWrites.delete(photoKey);throw error;});
        this.photoWrites.set(photoKey,write);
      }
      return this.photoWrites.get(photoKey);
    });
    return {record,ready:Promise.all(pending)};
  }
  async hydrate(key,record) {
    let missing=0;
    const photos=await Promise.all(record.photos.map(async photo=>{
      if(photo.id) return {...photo};
      let image;
      try {image=await this.photos.get(`${key}:${photo.local_id}`);} catch{}
      if(!image) {missing++;return {...photo,missing:true};}
      return {...photo,image};
    }));
    return {version:record.version,data:structuredClone(record.data),photos,photo_ids:record.photo_ids||[],
      token:record.token,savedAt:record.updated_at,dirty:true,restored:true,
      storageError:missing?'Ada foto yang belum tersimpan di perangkat. Tambahkan ulang atau hapus foto bertanda tidak tersedia.':''};
  }
  clear(key,expectedToken) {
    const current=this.read(key);
    if((current?.token||null)!==(expectedToken||null)) return false;
    this.storage().removeItem(key);
    for(const id of current?.photo_ids||[]) {
      const photoKey=`${key}:${id}`;
      const pending=this.photoWrites.get(photoKey)||Promise.resolve();
      this.photoWrites.delete(photoKey);
      pending.catch(()=>{}).then(()=>{
        // An explicitly recovered draft in another tab may still reference this photo.
        if(!this.read(key)?.photo_ids?.includes(id)) return this.photos.remove(photoKey);
      }).catch(()=>{});
    }
    return true;
  }
}

const deviceDraftStore=new DeviceDraftStore();
