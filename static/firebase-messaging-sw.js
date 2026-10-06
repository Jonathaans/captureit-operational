/* No offline cache: private API responses, salary and photos never enter Cache Storage. */
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
let sessionEpoch=0;
self.addEventListener('message',event=>{
  if(event.data?.type==='CAPTUREIT_LOGOUT'){sessionEpoch++;event.waitUntil(self.registration.getNotifications().then(rows=>rows.forEach(n=>n.close())));}
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const notice=event.notification.data?.notice;
  if(!/^[a-f0-9]{32}$/.test(notice||''))return;
  const url=new URL('/?notice='+notice,self.location.origin).href;
  event.waitUntil(self.clients.matchAll({type:'window',includeUncontrolled:true}).then(async clients=>{
    const tab=clients.find(c=>new URL(c.url).origin===self.location.origin);
    if(tab){await tab.navigate(url);return tab.focus();}
    return self.clients.openWindow(url);
  }));
});
importScripts('/push-config.js');
const config=self.CAPTUREIT_PUSH_CONFIG;
if(config?.ready){
  importScripts('https://www.gstatic.com/firebasejs/12.19.0/firebase-app-compat.js');
  importScripts('https://www.gstatic.com/firebasejs/12.19.0/firebase-messaging-compat.js');
  firebase.initializeApp(config.firebase);
  firebase.messaging().onBackgroundMessage(async payload=>{
    const notice=payload.data?.notice;if(!/^[a-f0-9]{32}$/.test(notice||''))return;
    const epoch=sessionEpoch;
    try {
      const response=await fetch('/api/push/context?notice='+notice,{credentials:'include',cache:'no-store'});
      if(!response.ok)return;const safe=await response.json();if(epoch!==sessionEpoch)return;
      await self.registration.showNotification(safe.title,{body:safe.body,icon:'/app-icon.svg',badge:'/app-icon.svg',tag:notice,data:{notice},renotify:false});
    }catch{/* The durable inbox remains available if the server cannot be reached. */}
  });
}
