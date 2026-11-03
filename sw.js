const CACHE='hatake-player-v4';
const SHELL=['./','index.html','app.js','manifest.webmanifest','icon-180.png','icon-192.png','icon-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{
 if(e.request.method!=='GET'||!e.request.url.startsWith(self.location.origin))return;
 e.respondWith(caches.match(e.request,{ignoreSearch:true}).then(r=>r||fetch(e.request).then(res=>{
  if(res.ok){const c=res.clone();caches.open(CACHE).then(ca=>ca.put(e.request,c))}return res}).catch(()=>caches.match('index.html'))));
});
