const VERSION='v5';
const CACHE='hatake-player-'+VERSION;
const SHELL=['./','index.html','app.js','manifest.webmanifest','icon-180.png','icon-192.png','icon-512.png'];
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(CACHE).then(c=>Promise.all(SHELL.map(u=>fetch(new Request(u,{cache:'reload'})).then(r=>{if(r.ok)return c.put(u,r)}).catch(()=>{})))))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k.startsWith('hatake-player-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
const netFirst=req=>fetch(req,{cache:'no-cache'}).then(res=>{if(res.ok){const c=res.clone();caches.open(CACHE).then(ca=>ca.put(req.mode==='navigate'?'index.html':new URL(req.url).pathname.endsWith('/')?'./':req,c))}return res})
 .catch(()=>caches.match(req,{ignoreSearch:true}).then(r=>r||caches.match('index.html')));
self.addEventListener('fetch',e=>{
 const req=e.request;if(req.method!=='GET'||!req.url.startsWith(self.location.origin))return;
 const path=new URL(req.url).pathname;
 if(req.mode==='navigate'||/\/$|\.(html|js|webmanifest)$/.test(path)){e.respondWith(netFirst(req));return}
 e.respondWith(caches.match(req,{ignoreSearch:true}).then(r=>r||fetch(req).then(res=>{if(res.ok){const c=res.clone();caches.open(CACHE).then(ca=>ca.put(req,c))}return res})));
});
