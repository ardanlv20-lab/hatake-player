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
 if(ok>=0)msg(ok+'曲を取り込みました');render();usage();makeOrder();if(C)rebuild();else prefetch();
 if(cur<0&&songs.length)load(0,false);
}
async function del(i){
 const s=songs[i];if(!confirm('「'+s.title+'」を削除しますか？'))return;
 await tx('blobs','readwrite',st=>st.delete(s.id));await tx('meta','readwrite',st=>st.delete(s.id));
 const wasCur=i===cur;songs.splice(i,1);
 if(wasCur){audio.pause();audio.removeAttribute('src');cur=-1;if(songs.length)load(Math.min(i,songs.length-1),false);else $('title').textContent='曲を追加してください'}
 else if(i<cur)cur--;
 makeOrder();render();usage();dropPre();if(C&&!wasCur)rebuild();else prefetch();
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
// ===== 連続再生モード（MP3を1本につなげて再生） =====
const CAP=150*1048576;
let C=null,segIdx=-1,lastAbs=0,userPaused=true,building=0;
const BR1=[0,32,40,48,56,64,80,96,112,128,160,192,224,256,320],BR2=[0,8,16,24,32,40,48,56,64,80,96,112,128,144,160],SR=[44100,48000,32000];
function hdr(u,p){if(u[p]!==0xFF||(u[p+1]&0xE0)!==0xE0)return null;const v=(u[p+1]>>3)&3,l=(u[p+1]>>1)&3;if(v===1||l!==1)return null;
 const bi=u[p+2]>>4,si=(u[p+2]>>2)&3;if(bi===0||bi===15||si===3)return null;const v1=v===3;
 const sr=SR[si]/(v1?1:v===2?2:4),br=(v1?BR1:BR2)[bi]*1000,pad=(u[p+2]>>1)&1;
 return{len:Math.floor((v1?144:72)*br/sr)+pad,sr,smp:v1?1152:576}}
function parseMp3(u){let p=0,end=u.length;
 while(end-p>10&&u[p]===0x49&&u[p+1]===0x44&&u[p+2]===0x33){p+=10+(((u[p+6]&127)<<21)|((u[p+7]&127)<<14)|((u[p+8]&127)<<7)|(u[p+9]&127))+((u[p+5]&0x10)?10:0)}
 if(end-p>128&&u[end-128]===0x54&&u[end-127]===0x41&&u[end-126]===0x47)end-=128;
 let h=null,lim=Math.min(end-4,p+65536);while(p<lim&&!(h=hdr(u,p)))p++;if(!h)return null;
 const sr=h.sr;let start=p,samples=0,first=true,q=p,miss=0;
 while(q+4<=end){const f=hdr(u,q);if(!f||q+f.len>end){if(++miss>4096)break;q++;continue}
  if(first){first=false;const w=String.fromCharCode(...u.subarray(q+4,Math.min(q+f.len,q+60)));if(/Xing|Info|VBRI/.test(w)){q+=f.len;start=q;continue}}
  samples+=f.smp;q+=f.len;p=q}
 if(!samples)return null;return{start,end:p,dur:samples/sr,sr}}
async function buildChunk(i){
 const k=order.indexOf(i);if(k<0)return null;const parts=[],segs=[];let bytes=0,t=0,sr=0;
 for(let j=0;j<order.length;j++){
  if(k+j>=order.length&&S.repeat!=='all')break;
  const idx=order[(k+j)%order.length],s=songs[idx];
  const b=await tx('blobs','readonly',st=>st.get(s.id));if(!b)break;
  const type=b instanceof Blob?b.type:b.type;if(type&&type!=='audio/mpeg'&&type!=='audio/mp3'){if(j===0)return null;break}
  const u=new Uint8Array(b instanceof Blob?await b.arrayBuffer():b.buf);const r=parseMp3(u);
  if(!r||(sr&&r.sr!==sr)||(j>0&&bytes+(r.end-r.start)>CAP)){if(j===0)return null;break}
  sr=r.sr;parts.push(u.subarray(r.start,r.end));bytes+=r.end-r.start;segs.push({i:idx,id:s.id,start:t,dur:r.dur});t+=r.dur}
 if(!segs.length)return null;
 return{url:URL.createObjectURL(new Blob(parts,{type:'audio/mpeg'})),segs,total:t,loop:S.repeat==='all'&&segs.length===order.length}}
function findSeg(t){if(!C)return -1;let r=0;for(let n=0;n<C.segs.length;n++)if(t>=C.segs[n].start-0.05)r=n;return r}
function setSeg(n){if(!C||n<0)return;const g=C.segs[n];segIdx=n;cur=songs.findIndex(x=>x.id===g.id);const s=songs[cur];if(!s)return;
 $('title').textContent=s.title;$('seek').max=Math.floor(g.dur)||0;$('dur').textContent=fmt(g.dur);S.lastId=s.id;save();render();session(s);posState()}
function seekAbs(t){audio.currentTime=t;lastAbs=t}
function rel(){return C&&segIdx>=0?audio.currentTime-C.segs[segIdx].start:audio.currentTime}
function dropC(){if(C){const u=C.url;C=null;segIdx=-1;if(u!==urlNow)URL.revokeObjectURL(u)}}
async function rebuild(){if(!C||cur<0)return;const r=rel(),pl=!audio.paused;await load(cur,pl,r)}
let pre=null,pendingOrder=null;
const toBlob=blob=>blob instanceof Blob?blob:new Blob([blob.buf],{type:blob.type});
function nextIdx(){
 if(!songs.length)return -1;pendingOrder=null;
 const k=order.indexOf(cur)+1;
 if(k<order.length)return order[k];
 if(S.repeat!=='all')return -1;
 if(S.shuffle){const o=songs.map((_,i)=>i);for(let i=o.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[o[i],o[j]]=[o[j],o[i]]}pendingOrder=o;return o[0]}
 return order[0];
}
function dropPre(){if(pre){if(pre.url&&pre.url!==urlNow)URL.revokeObjectURL(pre.url);pre=null}}
async function prefetch(){
 const n=nextIdx();
 if(pre&&pre.i===n&&pre.id===(songs[n]||{}).id)return;
 dropPre();if(n<0)return;
 const tok=pre={i:n,id:songs[n].id,url:null};
 try{const blob=await tx('blobs','readonly',st=>st.get(tok.id));
  if(pre!==tok||!blob)return;tok.url=URL.createObjectURL(toBlob(blob))}catch(e){if(pre===tok)pre=null}
}
function apply(i,url,play,pos){
 dropC();ui();msg('通常モード（MP3以外の曲は1曲ずつ再生）');const old=urlNow;cur=i;const s=songs[i];urlNow=url;
 audio.src=url;if(play){const p=audio.play();if(p&&p.catch)p.catch(()=>{})}
 if(old&&old!==url)URL.revokeObjectURL(old);
 if(pendingOrder&&pendingOrder[0]===i){order=pendingOrder;pendingOrder=null}
 $('title').textContent=s.title;$('seek').max=Math.floor(s.duration)||0;$('dur').textContent=fmt(s.duration);
 if(pos)audio.addEventListener('loadedmetadata',()=>{audio.currentTime=pos},{once:true});
 S.lastId=s.id;S.lastPos=pos||0;save();render();session(s);
 setTimeout(prefetch,0);
}
async function load(i,play,pos){
 if(i<0||i>=songs.length)return;
 const my=++building;let ch=null;try{ch=await buildChunk(i)}catch(e){console.warn(e)}
 if(my!==building){if(ch)URL.revokeObjectURL(ch.url);return}
 if(ch){dropPre();const old=urlNow,oc=C;C=ch;urlNow=ch.url;audio.src=ch.url;ui();
  if(old&&old!==ch.url)URL.revokeObjectURL(old);if(oc&&oc.url!==old)URL.revokeObjectURL(oc.url);
  setSeg(0);lastAbs=pos||0;S.lastPos=pos||0;save();
  if(pos)audio.addEventListener('loadedmetadata',()=>{seekAbs(pos)},{once:true});
  if(play){userPaused=false;audio.play().catch(()=>{})}
  msg('連続再生モード（'+ch.segs.length+'曲をつなげて再生中）');return}
 if(pre&&pre.i===i&&pre.url&&pre.id===songs[i].id){const u=pre.url;pre=null;apply(i,u,play,pos);return}
 const s=songs[i];const blob=await tx('blobs','readonly',st=>st.get(s.id));
 apply(i,URL.createObjectURL(toBlob(blob)),play,pos);
}
function step(d){
 if(!songs.length)return;
 if(C){let n=segIdx+d;if(C.loop)n=(n+C.segs.length)%C.segs.length;
  if(n>=0&&n<C.segs.length){seekAbs(C.segs[n].start+0.01);setSeg(n);userPaused=false;audio.play().catch(()=>{});return}
  if(n<0){seekAbs(C.segs[0].start);return}
  const last=C.segs[C.segs.length-1].i,k=order.indexOf(last)+1;
  if(k<order.length){load(order[k],true);return}if(S.repeat==='all'){load(order[0],true);return}audio.pause();return}
 if(d>0){const n=nextIdx();if(n<0){audio.pause();return}load(n,true);return}
 let k=order.indexOf(cur)+d;
 if(k<0)k=S.repeat==='all'?order.length-1:0;
 load(order[k],true);
}
function onEnded(){
 if(C){if(S.repeat==='one'){seekAbs(C.segs[segIdx].start);audio.play().catch(()=>{});return}
  const last=C.segs[C.segs.length-1].i,k=order.indexOf(last)+1;
  if(k<order.length){load(order[k],true);return}if(S.repeat==='all'&&order.length){load(order[0],true);return}userPaused=true;return}
 if(S.repeat==='one'){audio.currentTime=0;audio.play().catch(()=>{});return}
 const n=nextIdx();if(n<0){audio.pause();return}
 if(pre&&pre.i===n&&pre.url&&pre.id===songs[n].id){const u=pre.url;pre=null;apply(n,u,true);return}
 load(n,true);
}
function posState(){if('mediaSession' in navigator&&navigator.mediaSession.setPositionState&&(C||(audio.duration&&isFinite(audio.duration)))){
 const g=C&&segIdx>=0?C.segs[segIdx]:null,d=g?g.dur:audio.duration,p=g?audio.currentTime-g.start:audio.currentTime;
 try{navigator.mediaSession.setPositionState({duration:d,playbackRate:audio.playbackRate||1,position:Math.max(0,Math.min(p,d))})}catch(e){}}}
function session(s){if(!('mediaSession' in navigator))return;
 navigator.mediaSession.metadata=new MediaMetadata({title:s.title,artist:'畑プレーヤー',artwork:[{src:'icon-512.png',sizes:'512x512',type:'image/png'}]});
}
if('mediaSession' in navigator){const m=navigator.mediaSession,h=(a,f)=>{try{m.setActionHandler(a,f)}catch(e){}};
 h('play',()=>{userPaused=false;if(cur<0&&songs.length)load(order[0]??0,true);else audio.play().catch(()=>{})});h('pause',()=>{userPaused=true;audio.pause()});h('stop',()=>{userPaused=true;audio.pause()});h('previoustrack',()=>step(-1));h('nexttrack',()=>step(1));
 h('seekto',e=>{seekAbs((C&&segIdx>=0?C.segs[segIdx].start:0)+e.seekTime);posState()});h('seekbackward',e=>{seekAbs(Math.max(0,audio.currentTime-(e.seekOffset||10)));posState()});h('seekforward',e=>{seekAbs(Math.min(audio.duration||0,audio.currentTime+(e.seekOffset||10)));posState()});}
function ui(){
 $('shuffle').classList.toggle('on',S.shuffle);
 $('repeat').classList.toggle('on',S.repeat!=='none');
 $('repeat').textContent={none:'🔁 リピートなし',all:'🔁 全曲リピート',one:'🔂 1曲リピート'}[S.repeat];
 document.body.classList.toggle('dark',S.dark);$('dark').textContent=S.dark?'☀️':'🌙';
 audio.loop=C?C.loop:S.repeat==='one';
}
$('play').onclick=()=>{if(cur<0){if(songs.length)load(order[0]??0,true);return}if(audio.paused){userPaused=false;audio.play().catch(()=>{})}else{userPaused=true;audio.pause()}};
$('prev').onclick=()=>{if(rel()>3)seekAbs(C&&segIdx>=0?C.segs[segIdx].start:0);else step(-1)};
$('next').onclick=()=>step(1);
$('shuffle').onclick=()=>{S.shuffle=!S.shuffle;makeOrder();save();ui();if(C)rebuild();else prefetch()};
$('repeat').onclick=()=>{S.repeat={none:'all',all:'one',one:'none'}[S.repeat];save();ui();if(C)rebuild();else prefetch()};
$('dark').onclick=()=>{S.dark=!S.dark;save();ui()};
$('file').onchange=e=>{const f=[...e.target.files];e.target.value='';if(f.length)addFiles(f)};
$('seek').oninput=e=>{seekAbs((C&&segIdx>=0?C.segs[segIdx].start:0)+(+e.target.value))};
audio.onplay=()=>{$('play').textContent='⏸';$('play').setAttribute('aria-label','一時停止');if('mediaSession' in navigator)navigator.mediaSession.playbackState='playing'};
audio.onpause=()=>{$('play').textContent='▶';$('play').setAttribute('aria-label','再生');S.lastPos=rel();save();if('mediaSession' in navigator)navigator.mediaSession.playbackState='paused'};
audio.onended=onEnded;audio.addEventListener('loadedmetadata',posState);audio.addEventListener('ratechange',posState);
let lastSave=0;
audio.ontimeupdate=()=>{
 let t=audio.currentTime;
 if(C){
  if(t<1&&lastAbs>5&&!(C.loop&&lastAbs>C.total-3)&&!audio.paused&&document.hidden){}else{
   if(S.repeat==='one'&&segIdx>=0){const g=C.segs[segIdx];if(t>=g.start+g.dur-0.25||findSeg(t)!==segIdx){seekAbs(g.start);t=g.start}}
   const n=findSeg(t);if(n!==segIdx)setSeg(n);lastAbs=t}
  const g=C.segs[Math.max(segIdx,0)],r=t-g.start;$('cur').textContent=fmt(r);$('seek').value=Math.floor(r)}
 else{$('cur').textContent=fmt(t);$('seek').value=Math.floor(t);lastAbs=t;
  if(audio.duration&&isFinite(audio.duration)){$('seek').max=Math.floor(audio.duration);$('dur').textContent=fmt(audio.duration)}}
 const n=Date.now();if(n-lastSave>3000){lastSave=n;posState();S.lastPos=rel();save()}};
document.addEventListener('visibilitychange',()=>{
 if(document.hidden){if(cur>=0){S.lastPos=rel();save()}return}
 if(cur<0||userPaused)return;
 const wrap=C&&C.loop&&lastAbs>C.total-3;
 if(audio.currentTime<1&&lastAbs>5&&!wrap)seekAbs(lastAbs);
 if(audio.paused)audio.play().catch(()=>{});
});

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
