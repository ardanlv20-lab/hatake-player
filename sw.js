const VERSION='v12';
const CACHE='hatake-player-'+VERSION;
const SHELL=['./','index.html','app.js','manifest.webmanifest','icon-180.png','icon-192.png','icon-512.png'];

self.addEventListener('install',e=>{
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(c=>Promise.all(SHELL.map(u=>
    fetch(new Request(u,{cache:'reload'}))
      .then(r=>{if(r&&r.ok)return c.put(u,r)})
      .catch(()=>{})
  ))));
});

self.addEventListener('activate',e=>{
  e.waitUntil(
    caches.keys().then(ks=>Promise.all(
      ks.filter(k=>k.startsWith('hatake-player-')&&k!==CACHE).map(k=>caches.delete(k))
    )).then(()=>self.clients.claim())
  );
});

function cacheKeyFor(req){
  const path=new URL(req.url).pathname;
  if(req.mode==='navigate'||path.endsWith('/')||/\.html$/.test(path))return 'index.html';
  if(/\.js$/.test(path))return 'app.js';
  if(/\.webmanifest$/.test(path))return 'manifest.webmanifest';
  return null;
}

async function matchShell(req){
  const c=await caches.open(CACHE);
  const key=cacheKeyFor(req);
  const tries=[];
  if(key)tries.push(key);
  if(key==='index.html')tries.push('./','index.html');
  if(key==='app.js')tries.push('app.js');
  tries.push(req);
  for(const t of tries){
    const hit=await c.match(t,{ignoreSearch:true});
    if(hit)return hit;
  }
  return (await caches.match(req,{ignoreSearch:true}))
    || (await caches.match('index.html',{ignoreSearch:true}))
    || (await caches.match('./',{ignoreSearch:true}));
}

function bgUpdate(req,key){
  fetch(req,{cache:'no-cache'}).then(res=>{
    if(!res||!res.ok)return;
    caches.open(CACHE).then(c=>{
      c.put(key||req,res.clone()).catch(()=>{});
      if(key==='index.html')c.put('./',res.clone()).catch(()=>{});
    });
  }).catch(()=>{});
}

function withTimeout(p,ms){
  return Promise.race([
    p,
    new Promise((_,rej)=>setTimeout(()=>rej(new Error('timeout')),ms))
  ]);
}

async function staleWhileRevalidate(req){
  const key=cacheKeyFor(req)||req;
  const cached=await matchShell(req);
  if(cached){
    bgUpdate(req,typeof key==='string'?key:null);
    return cached;
  }
  try{
    const res=await withTimeout(fetch(req,{cache:'no-cache'}),2500);
    if(res&&res.ok){
      const k=typeof key==='string'?key:req;
      caches.open(CACHE).then(c=>{
        c.put(k,res.clone()).catch(()=>{});
        if(k==='index.html')c.put('./',res.clone()).catch(()=>{});
      });
      return res;
    }
  }catch(e){}
  const again=await matchShell(req);
  if(again)return again;
  return new Response('オフラインです。電波のあるところで一度開いてください。',{
    status:503,headers:{'Content-Type':'text/plain; charset=utf-8'}
  });
}

self.addEventListener('fetch',e=>{
  const req=e.request;
  if(req.method!=='GET'||!req.url.startsWith(self.location.origin))return;
  const path=new URL(req.url).pathname;
  const isShell=req.mode==='navigate'||/\/$|\.(html|js|webmanifest)$/.test(path);
  if(isShell){
    e.respondWith(staleWhileRevalidate(req));
    return;
  }
  e.respondWith((async()=>{
    const hit=await caches.match(req,{ignoreSearch:true});
    if(hit)return hit;
    const res=await fetch(req);
    if(res&&res.ok)caches.open(CACHE).then(c=>c.put(req,res.clone())).catch(()=>{});
    return res;
  })());
});
