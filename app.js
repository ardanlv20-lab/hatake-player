'use strict';
const $=id=>document.getElementById(id);
const audio=$('audio');
let db,songs=[],cur=-1,urlNow=null,order=[];
const S=Object.assign({shuffle:false,repeat:'none',dark:false,lastId:null,lastPos:0},JSON.parse(localStorage.getItem('hp-settings')||'{}'));
const save=()=>localStorage.setItem('hp-settings',JSON.stringify(S));
const fmt=s=>{s=Math.floor(s||0);return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
const msg=t=>{$('msg').textContent=t};

function openDB(){return new Promise((res,rej)=>{const r=indexedDB.open('hatake-player',1);
 r.onupgradeneeded=()=>{const d=r.result;d.createObjectStore('meta',{keyPath:'id'});d.createObjectStore('blobs')};
 r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error)})}
function tx(store,mode,fn){return new Promise((res,rej)=>{const t=db.transaction(store,mode);const out=fn(t.objectStore(store));
 t.oncomplete=()=>res(out&&out.result!==undefined?out.result:out);t.onerror=()=>rej(t.error);t.onabort=()=>rej(t.error)})}

function getDuration(blob){return new Promise(res=>{const a=new Audio();const u=URL.createObjectURL(blob);
 const done=d=>{URL.revokeObjectURL(u);res(isFinite(d)?d:0)};
 a.preload='metadata';a.onloadedmetadata=()=>done(a.duration);a.onerror=()=>done(0);setTimeout(()=>done(0),8000);a.src=u})}

async function addFiles(files){
 let n=0,ok=0;
 for(const f of files){
  msg('取り込み中… '+(++n)+'/'+files.length+' '+f.name);
  const ext=(f.name.split('.').pop()||'').toLowerCase();
  const types={mp3:'audio/mpeg',m4a:'audio/mp4',aac:'audio/aac',wav:'audio/wav',mp4:'audio/mp4',caf:'audio/x-caf',aif:'audio/aiff',aiff:'audio/aiff',flac:'audio/flac',ogg:'audio/ogg',opus:'audio/ogg'};
  if(!(f.type||'').startsWith('audio/')&&!types[ext]){msg('音楽ファイルではないのでスキップ: '+f.name);continue}
  let buf;try{buf=await f.arrayBuffer()}catch(e){msg('読み込めませんでした（iCloudにあるファイルは先にダウンロードしてください）: '+f.name);continue}
  const type=(f.type&&f.type.startsWith('audio/'))?f.type:(types[ext]||'audio/mpeg');
  const blob=new Blob([buf],{type});
  const id=Date.now().toString(36)+Math.random().toString(36).slice(2,7);
  const meta={id,title:f.name.replace(/\.[^.]+$/,''),duration:await getDuration(blob),size:blob.size,added:Date.now()};
  try{await tx('blobs','readwrite',s=>s.put({buf,type},id));await tx('meta','readwrite',s=>s.put(meta));songs.push(meta)}
  catch(e){msg('保存できませんでした: '+f.name+'（'+(e&&e.name||e)+'）');ok=-1;break}
  ok++;
 }
 if(ok>=0)msg(ok+'曲を取り込みました');render();usage();makeOrder();
 if(cur<0&&songs.length)load(0,false);
}
async function del(i){
 const s=songs[i];if(!confirm('「'+s.title+'」を削除しますか？'))return;
 await tx('blobs','readwrite',st=>st.delete(s.id));await tx('meta','readwrite',st=>st.delete(s.id));
 const wasCur=i===cur;songs.splice(i,1);
 if(wasCur){audio.pause();audio.removeAttribute('src');cur=-1;if(songs.length)load(Math.min(i,songs.length-1),false);else $('title').textContent='曲を追加してください'}
 else if(i<cur)cur--;
 makeOrder();render();usage();
}
function render(){
 const ul=$('list');ul.innerHTML='';
 songs.forEach((s,i)=>{const li=document.createElement('li');if(i===cur)li.className='cur';
  const t=document.createElement('div');t.className='t';t.textContent=(i===cur?'▶ ':'')+s.title+'（'+fmt(s.duration)+'）';t.onclick=()=>load(i,true);
  const b=document.createElement('button');b.textContent='🗑';b.setAttribute('aria-label','削除');b.onclick=()=>del(i);
  li.append(t,b);ul.append(li)});
}
async function usage(){
 let t='保存曲数: '+songs.length;
 if(navigator.storage&&navigator.storage.estimate){const e=await navigator.storage.estimate();
  t+=' ／ 使用量 '+(e.usage/1048576).toFixed(1)+'MB'+(e.quota?' / 上限目安 '+(e.quota/1048576/1024).toFixed(1)+'GB':'')}
 $('usage').textContent=t;
}
function makeOrder(){order=songs.map((_,i)=>i);if(S.shuffle)for(let i=order.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[order[i],order[j]]=[order[j],order[i]]}}
async function load(i,play,pos){
 if(i<0||i>=songs.length)return;cur=i;const s=songs[i];
 const blob=await tx('blobs','readonly',st=>st.get(s.id));
 if(urlNow)URL.revokeObjectURL(urlNow);urlNow=URL.createObjectURL(blob instanceof Blob?blob:new Blob([blob.buf],{type:blob.type}));
 audio.src=urlNow;$('title').textContent=s.title;$('seek').max=Math.floor(s.duration)||0;$('dur').textContent=fmt(s.duration);
 if(pos)audio.addEventListener('loadedmetadata',()=>{audio.currentTime=pos},{once:true});
 S.lastId=s.id;S.lastPos=pos||0;save();render();session(s);
 if(play)audio.play().catch(()=>{});
}
function step(d){
 if(!songs.length)return;
 let k=order.indexOf(cur)+d;
 if(k>=order.length){if(S.repeat==='all'){if(S.shuffle)makeOrder();k=0}else{audio.pause();return}}
 if(k<0)k=S.repeat==='all'?order.length-1:0;
 load(order[k],true);
}
function session(s){if(!('mediaSession' in navigator))return;
 navigator.mediaSession.metadata=new MediaMetadata({title:s.title,artist:'畑プレーヤー',artwork:[{src:'icon-512.png',sizes:'512x512',type:'image/png'}]});
}
if('mediaSession' in navigator){const m=navigator.mediaSession,h=(a,f)=>{try{m.setActionHandler(a,f)}catch(e){}};
 h('play',()=>audio.play());h('pause',()=>audio.pause());h('previoustrack',()=>step(-1));h('nexttrack',()=>step(1));
 h('seekto',e=>{audio.currentTime=e.seekTime});}
function ui(){
 $('shuffle').classList.toggle('on',S.shuffle);
 $('repeat').classList.toggle('on',S.repeat!=='none');
 $('repeat').textContent={none:'🔁 リピートなし',all:'🔁 全曲リピート',one:'🔂 1曲リピート'}[S.repeat];
 document.body.classList.toggle('dark',S.dark);$('dark').textContent=S.dark?'☀️':'🌙';
 audio.loop=S.repeat==='one';
}
$('play').onclick=()=>{if(cur<0){if(songs.length)load(order[0]??0,true);return}audio.paused?audio.play():audio.pause()};
$('prev').onclick=()=>{if(audio.currentTime>3)audio.currentTime=0;else step(-1)};
$('next').onclick=()=>step(1);
$('shuffle').onclick=()=>{S.shuffle=!S.shuffle;makeOrder();save();ui()};
$('repeat').onclick=()=>{S.repeat={none:'all',all:'one',one:'none'}[S.repeat];save();ui()};
$('dark').onclick=()=>{S.dark=!S.dark;save();ui()};
$('file').onchange=e=>{const f=[...e.target.files];e.target.value='';if(f.length)addFiles(f)};
$('seek').oninput=e=>{audio.currentTime=+e.target.value};
audio.onplay=()=>{$('play').textContent='⏸';$('play').setAttribute('aria-label','一時停止');if('mediaSession' in navigator)navigator.mediaSession.playbackState='playing'};
audio.onpause=()=>{$('play').textContent='▶';$('play').setAttribute('aria-label','再生');S.lastPos=audio.currentTime;save();if('mediaSession' in navigator)navigator.mediaSession.playbackState='paused'};
audio.onended=()=>step(1);
let lastSave=0;
audio.ontimeupdate=()=>{$('cur').textContent=fmt(audio.currentTime);$('seek').value=Math.floor(audio.currentTime);
 if(audio.duration&&isFinite(audio.duration)){$('seek').max=Math.floor(audio.duration);$('dur').textContent=fmt(audio.duration)}
 const n=Date.now();if(n-lastSave>3000){lastSave=n;S.lastPos=audio.currentTime;save()}};
document.addEventListener('visibilitychange',()=>{if(document.hidden&&cur>=0){S.lastPos=audio.currentTime;save()}});

(async()=>{
 ui();db=await openDB();
 songs=(await tx('meta','readonly',s=>s.getAll())).sort((a,b)=>a.added-b.added);
 makeOrder();render();usage();
 if(navigator.storage&&navigator.storage.persist)navigator.storage.persist().then(p=>{window.__persisted=p});
 const i=songs.findIndex(s=>s.id===S.lastId);
 if(i>=0)await load(i,false,S.lastPos);else if(songs.length)await load(0,false);
 if('serviceWorker' in navigator)navigator.serviceWorker.register('sw.js').catch(e=>console.warn(e));
 window.__ready=true;
})();
