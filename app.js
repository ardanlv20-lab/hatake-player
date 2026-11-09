'use strict';
const $=id=>document.getElementById(id);
const audio=$('audio');
// Keep one media element and its source alive for iOS remote controls.
audio.playsInline=true;audio.preload='auto';
const retiredURLs=new Set();
function revokeUnused(url){
 if(!url)return;
 if(url===audio.src||url===audio.currentSrc){retiredURLs.add(url);return}
 URL.revokeObjectURL(url);retiredURLs.delete(url);
}
audio.addEventListener('loadedmetadata',()=>{for(const url of retiredURLs)revokeUnused(url)});
let db,songs=[],cur=-1,urlNow=null,order=[];
let loadJob=null,wantPlay=false,initializing=true;
const S=Object.assign({shuffle:false,repeat:'none',dark:false,lastId:null,lastPos:0},JSON.parse(localStorage.getItem('hp-settings')||'{}'));
const save=()=>localStorage.setItem('hp-settings',JSON.stringify(S));
const fmt=s=>{s=Math.floor(s||0);return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
const msg=t=>{$('msg').textContent=t};
function idleScreen(){
 $('title').textContent=songs.length?'曲を選んでください':'曲を追加してください';
 $('seek').max=0;$('seek').value=0;$('cur').textContent=fmt(0);$('dur').textContent=fmt(0);
}

function openDB(){return new Promise((res,rej)=>{const r=indexedDB.open('hatake-player',1);
 r.onupgradeneeded=()=>{const d=r.result;d.createObjectStore('meta',{keyPath:'id'});d.createObjectStore('blobs')};
 r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error)})}
function tx(store,mode,fn){return new Promise((res,rej)=>{const t=db.transaction(store,mode);const out=fn(t.objectStore(store));
 t.oncomplete=()=>res(out&&out.result!==undefined?out.result:out);t.onerror=()=>rej(t.error);t.onabort=()=>rej(t.error)})}

function getDuration(blob){return new Promise(res=>{const a=new Audio();const u=URL.createObjectURL(blob);
 const done=d=>{revokeUnused(u);res(isFinite(d)?d:0)};
 a.preload='metadata';a.onloadedmetadata=()=>done(a.duration);a.onerror=()=>done(0);setTimeout(()=>done(0),8000);a.src=u})}

// ===== MP3変換（m4a・wavなどをMP3にして連続再生モードに入れる） =====
// Bytes decide, not MIME: anything parseMp3 accepts is never re-encoded.
function isMp3(u){
 if(u.length<16)return false;
 const tag=String.fromCharCode(...u.subarray(0,12));
 // Container signatures (m4a/mp4 ftyp, wav, flac, ogg, aiff, caf) are never MP3,
 // even if random bytes inside look like MP3 frame headers.
 if(/^(RIFF|fLaC|OggS|FORM|caff)/.test(tag)||tag.slice(4,8)==='ftyp')return false;
 const r=parseMp3(u);if(!r||r.offsets.length<2)return false;
 let p=0;while(u.length-p>10&&u[p]===0x49&&u[p+1]===0x44&&u[p+2]===0x33)p+=10+(((u[p+6]&127)<<21)|((u[p+7]&127)<<14)|((u[p+8]&127)<<7)|(u[p+9]&127))+((u[p+5]&0x10)?10:0);
 return (r.end-r.start)>=0.5*(u.length-p);
}
const MP3_RATES=[8000,11025,12000,16000,22050,24000,32000,44100,48000];
const MAX_CONVERT_SEC=30*60; // Decoded PCM of longer files can exhaust iPhone memory.
let actx=null,encQ=Promise.resolve();
function audioCtx(){
 if(actx)return actx;
 const A=window.AudioContext||window.webkitAudioContext;if(!A)return null;
 // 44.1kHz output matches most MP3s, so converted songs join the same stream.
 try{actx=new A({sampleRate:44100})}catch(e){try{actx=new A()}catch(e2){actx=null}}
 return actx;
}
const tick=()=>new Promise(r=>setTimeout(r,0));
// Decode with Web Audio and encode to 192kbps MP3. Returns ArrayBuffer or throws.
async function toMp3(raw,onPct){
 if(!window.lamejs||!lamejs.Mp3Encoder)throw new Error('変換プログラムが読み込まれていません');
 const ctx=audioCtx();if(!ctx)throw new Error('この端末では変換できません');
 if(ctx.state==='suspended'){try{await ctx.resume()}catch(e){}}
 // decodeAudioData detaches its input, and old Safari only has the callback form.
 let ab=await new Promise((res,rej)=>{let p;
  try{p=ctx.decodeAudioData(raw.slice(0),res,e=>rej(e||new Error('decode')))}catch(e){rej(e);return}
  if(p&&p.then)p.then(res,rej)});
 const sr=ab.sampleRate;if(!MP3_RATES.includes(sr))throw new Error('サンプリング周波数 '+sr+'Hz はMP3にできません');
 if(!ab.length)throw new Error('音声が空です');
 const nch=ab.numberOfChannels,stereo=nch>=2,len=ab.length;
 const chans=[];for(let c=0;c<nch;c++)chans.push(ab.getChannelData(c));
 const enc=new lamejs.Mp3Encoder(stereo?2:1,sr,192);
 const BLOCK=1152*32,L=new Int16Array(BLOCK),R=stereo?new Int16Array(BLOCK):null,out=[];
 const s16=v=>v<=-1?-32768:v>=1?32767:Math.round(v<0?v*32768:v*32767);
 // Downmix >2 channels: odd channels to left, even to right (center/LFE shared).
 const left=[],right=[];
 if(nch<=2){left.push(chans[0]);if(stereo)right.push(chans[1])}
 else chans.forEach((d,c)=>{if(c%2===0)left.push(d);else right.push(d)});
 for(let o=0;o<len;o+=BLOCK){
  const n=Math.min(BLOCK,len-o);
  for(let i=0;i<n;i++){
   let a=0;for(const d of left)a+=d[o+i];L[i]=s16(a/left.length);
   if(R){let b=0;for(const d of right)b+=d[o+i];R[i]=s16(b/right.length)}
  }
  // Encode in 1152-sample MP3 frames.
  for(let f=0;f<n;f+=1152){const e=Math.min(n,f+1152);
   const m=enc.encodeBuffer(L.subarray(f,e),R?R.subarray(f,e):L.subarray(f,e));if(m.length)out.push(new Uint8Array(m.buffer,m.byteOffset,m.length))}
  if((o/BLOCK)%8===7){if(onPct)onPct(Math.floor(100*o/len));await tick()}
 }
 const m=enc.flush();if(m.length)out.push(new Uint8Array(m.buffer,m.byteOffset,m.length));
 chans.length=0;left.length=0;right.length=0;ab=null; // Drop the decoded PCM early.
 const buf=await new Blob(out,{type:'audio/mpeg'}).arrayBuffer();out.length=0;
 if(!isMp3(new Uint8Array(buf)))throw new Error('変換結果がMP3になりませんでした');
 return buf;
}
// One conversion at a time (import and button) to limit iPhone memory.
function convertOne(raw,onPct){const job=encQ.then(()=>toMp3(raw,onPct));encQ=job.catch(()=>{});return job}
async function convertLibrary(){
 const btn=$('convert');if(btn.disabled)return;btn.disabled=true;
 try{
  msg('変換が必要な曲を探しています…');
  const todo=[];
  for(const s of songs.slice()){
   const b=await tx('blobs','readonly',st=>st.get(s.id));if(!b)continue;
   const u=new Uint8Array(b instanceof Blob?await b.arrayBuffer():b.buf);
   const type=b.type||'';
   if(isMp3(u)){
    // Already MP3 bytes: never re-encode; only fix a wrong stored type.
    if(type!=='audio/mpeg'&&type!=='audio/mp3')await tx('blobs','readwrite',st=>st.put({buf:u.buffer.byteLength===u.length?u.buffer:u.slice().buffer,type:'audio/mpeg'},s.id));
    continue}
   todo.push(s.id);
  }
  if(!todo.length){msg('変換が必要な曲はありません');return}
  let done=0,n=0;const failed=[];let curChanged=false;
  for(const id of todo){
   const s=songs.find(x=>x.id===id);if(!s)continue;n++;
   const label='変換中… '+n+'/'+todo.length+' '+s.title;msg(label);
   try{
    const b=await tx('blobs','readonly',st=>st.get(id));if(!b)continue;
    if(s.duration>MAX_CONVERT_SEC)throw new Error('長すぎる');
    const raw=b instanceof Blob?await b.arrayBuffer():b.buf;
    const mp3=await convertOne(raw,p=>msg(label+'（'+p+'%）'));
    const now=songs.find(x=>x.id===id);if(!now)continue; // Deleted meanwhile.
    const r=parseMp3(new Uint8Array(mp3));
    const meta=Object.assign({},now,{size:mp3.byteLength,duration:r?r.dur:now.duration});
    await tx('blobs','readwrite',st=>st.put({buf:mp3,type:'audio/mpeg'},id));
    await tx('meta','readwrite',st=>st.put(meta));
    Object.assign(now,meta);done++;
    if(cur>=0&&songs[cur]&&songs[cur].id===id)curChanged=true;
   }catch(e){console.warn(e);failed.push(s.title)}
  }
  let t=done+'曲をMP3に変換しました';
  if(failed.length)t+='（変換できなかった曲: '+failed.join('、')+'。Apple Musicなど保護された曲や長すぎる曲は元のまま残しています）';
  render();usage();makeOrder();dropPre();
  if(done&&cur>=0&&(C||curChanged)&&!loadJob){
   // Rejoin with the new MP3s; only resumes if it was already playing.
   const playing=!audio.paused,pos=rel();await load(cur,playing,pos);
  }else if(done)prefetch();
  msg(t);
 }finally{btn.disabled=false}
}

async function addFiles(files){
 const hadSongs=!!songs.length;
 let n=0,ok=0,conv=0;const failed=[];
 for(const f of files){
  const label='取り込み中… '+(++n)+'/'+files.length+' '+f.name;msg(label);
  const ext=(f.name.split('.').pop()||'').toLowerCase();
  const types={mp3:'audio/mpeg',m4a:'audio/mp4',aac:'audio/aac',wav:'audio/wav',mp4:'audio/mp4',caf:'audio/x-caf',aif:'audio/aiff',aiff:'audio/aiff',flac:'audio/flac',ogg:'audio/ogg',opus:'audio/ogg'};
  if(!(f.type||'').startsWith('audio/')&&!types[ext]){msg('音楽ファイルではないのでスキップ: '+f.name);continue}
  let buf;try{buf=await f.arrayBuffer()}catch(e){msg('読み込めませんでした（iCloudにあるファイルは先にダウンロードしてください）: '+f.name);continue}
  let type=(f.type&&f.type.startsWith('audio/'))?f.type:(types[ext]||'audio/mpeg');
  if(isMp3(new Uint8Array(buf)))type='audio/mpeg'; // Already MP3: keep bytes as-is.
  else{
   msg(label+'（MP3に変換中）');
   try{
    if((await getDuration(new Blob([buf],{type})))>MAX_CONVERT_SEC)throw new Error('長すぎる');
    buf=await convertOne(buf,p=>msg(label+'（MP3に変換中 '+p+'%）'));type='audio/mpeg';conv++;
   }catch(e){console.warn(e);failed.push(f.name)} // Keep the original playable file.
  }
  const blob=new Blob([buf],{type});
  const id=Date.now().toString(36)+Math.random().toString(36).slice(2,7);
  const meta={id,title:f.name.replace(/\.[^.]+$/,''),duration:await getDuration(blob),size:blob.size,added:Date.now()};
  try{await tx('blobs','readwrite',s=>s.put({buf,type},id));await tx('meta','readwrite',s=>s.put(meta));songs.push(meta)}
  catch(e){msg('保存できませんでした: '+f.name+'（'+(e&&e.name||e)+'）');ok=-1;break}
  ok++;
 }
 let t='';if(ok>=0){t=ok+'曲を取り込みました';if(conv)t+='（うち'+conv+'曲をMP3に変換しました）';
  if(failed.length)t+='。MP3に変換できなかった曲（元のまま保存）: '+failed.join('、')+'（Apple Musicなど保護された曲は変換できません）';msg(t)}
 render();usage();makeOrder();if(C)rebuild();else prefetch();
 // Keep the import result visible after the first song finishes loading.
 if(!hadSongs&&cur<0&&songs.length){if(await load(0,false)&&t&&(conv||failed.length))msg(t)}
}
async function del(i){
 const s=songs[i];if(!confirm('「'+s.title+'」を削除しますか？'))return;
 await tx('blobs','readwrite',st=>st.delete(s.id));await tx('meta','readwrite',st=>st.delete(s.id));
 const wasCur=i===cur;songs.splice(i,1);
 if(wasCur){pausePlayback();audio.removeAttribute('src');try{audio.load()}catch(e){}
  dropC();if(urlNow){revokeUnused(urlNow);urlNow=null}
  if('mediaSession' in navigator){try{navigator.mediaSession.metadata=null}catch(e){}}
  cur=-1;lastAbs=0;if(songs.length)await load(Math.min(i,songs.length-1),false);else{idleScreen();ui()}}
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
// Avoid a new src at an arbitrary 150MB boundary while iOS has suspended JS.
// Blob slices keep the joined audio out of JS memory (only one song is parsed at
// a time). Xing's byte counter is 32-bit; reserve room for its metadata frame.
const CAP=0xFFFFFFFF-4096;
let C=null,segIdx=-1,lastAbs=0,userPaused=true,building=0;
const BR1=[0,32,40,48,56,64,80,96,112,128,160,192,224,256,320],BR2=[0,8,16,24,32,40,48,56,64,80,96,112,128,144,160],SR=[44100,48000,32000];
function hdr(u,p){if(u[p]!==0xFF||(u[p+1]&0xE0)!==0xE0)return null;const v=(u[p+1]>>3)&3,l=(u[p+1]>>1)&3;if(v===1||l!==1)return null;
 const bi=u[p+2]>>4,si=(u[p+2]>>2)&3;if(bi===0||bi===15||si===3)return null;const v1=v===3;
 const sr=SR[si]/(v1?1:v===2?2:4),br=(v1?BR1:BR2)[bi]*1000,pad=(u[p+2]>>1)&1;
 return{len:Math.floor((v1?144:72)*br/sr)+pad,sr,smp:v1?1152:576,v,mode:u[p+3]>>6}}
function parseMp3(u){let p=0,end=u.length;
 while(end-p>10&&u[p]===0x49&&u[p+1]===0x44&&u[p+2]===0x33){p+=10+(((u[p+6]&127)<<21)|((u[p+7]&127)<<14)|((u[p+8]&127)<<7)|(u[p+9]&127))+((u[p+5]&0x10)?10:0)}
 if(end-p>128&&u[end-128]===0x54&&u[end-127]===0x41&&u[end-126]===0x47)end-=128;
 let h=null,lim=Math.min(end-4,p+65536);while(p<lim&&!(h=hdr(u,p)))p++;if(!h)return null;
 const sr=h.sr;let start=p,samples=0,first=true,q=p,miss=0,header=null;const offsets=[];
 while(q+4<=end){const f=hdr(u,q);if(!f||q+f.len>end){if(++miss>4096)break;q++;continue}
  if(first){first=false;const w=String.fromCharCode(...u.subarray(q+4,Math.min(q+f.len,q+60)));if(/Xing|Info|VBRI/.test(w)){q+=f.len;start=q;continue}}
  if(f.sr!==sr||f.v!==h.v||(f.mode===3)!==(h.mode===3))return null;
  if(!header)header=u.slice(q,q+4);offsets.push(q-start);samples+=f.smp;q+=f.len;p=q}
 if(!samples)return null;return{start,end:p,dur:samples/sr,sr,header,offsets,v:h.v,mode:header[3]>>6}}
// One Xing frame describes the whole joined stream, not each original file.
// Counts exclude this metadata frame; byte count and TOC include its bytes.
function xingFrame(header,offsets,bytes){
 const frameHeader=header.slice();frameHeader[1]|=1; // No CRC in this new frame.
 const v=(frameHeader[1]>>3)&3,mono=(frameHeader[3]>>6)===3;
 frameHeader[2]=(frameHeader[2]&0x0D)|((v===3?9:8)<<4); // 128/64 kbps, no padding.
 const size=hdr(frameHeader,0).len,frame=new Uint8Array(size);
 frame.set(frameHeader);const at=4+(v===3?(mono?17:32):(mono?9:17));
 frame.set([88,105,110,103],at); // Xing
 const view=new DataView(frame.buffer),totalBytes=bytes+size;
 view.setUint32(at+4,7); // Frames + bytes + 100-entry seek TOC.
 view.setUint32(at+8,offsets.length);view.setUint32(at+12,totalBytes);
 for(let n=0;n<100;n++){
  const offset=n===0?0:size+offsets[Math.min(offsets.length-1,Math.floor(n*offsets.length/100))];
  frame[at+16+n]=Math.min(255,Math.floor(256*offset/totalBytes));
 }
 return frame;
}
async function buildChunk(i){
 const k=order.indexOf(i);if(k<0)return null;const parts=[],segs=[],offsets=[];let bytes=0,t=0,sr=0,header=null,version=null,mode=null;
 for(let j=0;j<order.length;j++){
  if((j>0&&S.repeat==='one')||(k+j>=order.length&&S.repeat!=='all'))break;
  const idx=order[(k+j)%order.length],s=songs[idx];
  const b=await tx('blobs','readonly',st=>st.get(s.id));if(!b)break;
  const type=b instanceof Blob?b.type:b.type;if(type&&type!=='audio/mpeg'&&type!=='audio/mp3'){if(j===0)return null;break}
  const u=new Uint8Array(b instanceof Blob?await b.arrayBuffer():b.buf);const r=parseMp3(u);
  if(!r||(sr&&(r.sr!==sr||r.v!==version||(r.mode===3)!==(mode===3)))||(bytes+(r.end-r.start)>CAP)){if(j===0)return null;break}
  sr=r.sr;version=r.v;mode=r.mode;if(!header)header=r.header;
  for(const offset of r.offsets)offsets.push(bytes+offset);
  parts.push(toBlob(b).slice(r.start,r.end));bytes+=r.end-r.start;segs.push({i:idx,id:s.id,start:t,dur:r.dur});t+=r.dur}
 if(!segs.length)return null;
 return{url:URL.createObjectURL(new Blob([xingFrame(header,offsets,bytes),...parts],{type:'audio/mpeg'})),segs,total:t,loop:S.repeat==='one'||(S.repeat==='all'&&segs.length===order.length)}}
function findSeg(t){if(!C)return -1;let r=0;for(let n=0;n<C.segs.length;n++)if(t>=C.segs[n].start-0.05)r=n;return r}
function setSeg(n){if(!C||n<0)return;const g=C.segs[n];segIdx=n;cur=songs.findIndex(x=>x.id===g.id);const s=songs[cur];if(!s)return;
 $('title').textContent=s.title;$('seek').max=Math.floor(g.dur)||0;$('dur').textContent=fmt(g.dur);S.lastId=s.id;if(!loadJob)save();render();session(s);posState()}
function seekAbs(t){if(!audio.getAttribute('src'))return;try{audio.currentTime=t;lastAbs=t}catch(e){}}
function rel(){return C&&segIdx>=0?audio.currentTime-C.segs[segIdx].start:audio.currentTime}
function dropC(){if(C){const u=C.url;C=null;segIdx=-1;if(u!==urlNow)revokeUnused(u)}}
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
function dropPre(){if(pre){if(pre.url&&pre.url!==urlNow)revokeUnused(pre.url);pre=null}}
async function prefetch(){
 const n=nextIdx();
 if(pre&&pre.i===n&&pre.id===(songs[n]||{}).id)return;
 dropPre();if(n<0)return;
 const tok=pre={i:n,id:songs[n].id,url:null};
 try{const blob=await tx('blobs','readonly',st=>st.get(tok.id));
  if(pre!==tok||!blob)return;tok.url=URL.createObjectURL(toBlob(blob))}catch(e){if(pre===tok)pre=null}
}
// Attach listeners before assigning src, and also handle already-loaded metadata.
// A new load cancels both the old metadata wait and its seeked callback.
function restorePosition(position,job=loadJob,url=audio.src){
 return new Promise((resolve,reject)=>{
  let settled=false,metadataSeen=false,timer;
  const current=()=>!job||(loadJob===job&&job.generation===building);
  const clean=()=>{
   clearTimeout(timer);audio.removeEventListener('loadedmetadata',metadata);
   audio.removeEventListener('seeked',seeked);audio.removeEventListener('error',failed);
   if(job&&job.cancel===cancel)job.cancel=null;
  };
  const finish=(ok,error)=>{if(settled)return;settled=true;clean();if(error)reject(error);else resolve(ok)};
  const cancel=()=>finish(false);
  const failed=()=>finish(false,new Error('音楽ファイルを読み込めませんでした'));
  const seeked=()=>{if(!current())finish(false);else if(metadataSeen&&!audio.seeking)finish(true)};
  const metadata=()=>{
   if(!current()){finish(false);return}
   // readyState may briefly still describe the previous source after src changes.
   if(audio.readyState<1||audio.currentSrc!==url||metadataSeen)return;metadataSeen=true;
   let target=Number.isFinite(position)?Math.max(0,position):0;
   if(Number.isFinite(audio.duration)&&audio.duration>0)target=Math.min(target,Math.max(0,audio.duration-0.01));
   try{
    // Never begin playback at zero and jump to the restored position afterwards.
    if(Math.abs(audio.currentTime-target)>0.01)seekAbs(target);else lastAbs=target;
    if(!audio.seeking)finish(true);
   }catch(e){finish(false,e)}
  };
  audio.addEventListener('loadedmetadata',metadata);
  audio.addEventListener('seeked',seeked);audio.addEventListener('error',failed);
  if(job)job.cancel=cancel;
  timer=setTimeout(()=>finish(false,new Error('読み込みに時間がかかっています。曲を選び直してください')),20000);
  if(audio.src!==url)audio.src=url;
  if(audio.readyState>=1&&audio.currentSrc===url)metadata();
 });
}
async function load(i,play=false,pos=0){
 if(i<0||i>=songs.length)return false;
 const previous=loadJob,my=++building;
 if(previous&&previous.cancel)previous.cancel();
 const job=loadJob={generation:my,id:songs[i].id,cancel:null};
 if(play){wantPlay=true;userPaused=false}
 stopTicker();audio.pause();clearTimeout(pauseTimer);
 $('title').textContent=songs[i].title;msg('読み込み中…');
 let ch=null;
 try{
  try{ch=await buildChunk(i)}catch(e){console.warn(e)}
  if(loadJob!==job){if(ch)revokeUnused(ch.url);return false}
  const old=urlNow,oc=C;let url;
  if(ch){
   dropPre();C=ch;url=ch.url;urlNow=url;setSeg(0);ui();
  }else{
   if(pre&&pre.i===i&&pre.url&&pre.id===job.id){url=pre.url;pre=null}
   else{
    const blob=await tx('blobs','readonly',st=>st.get(job.id));
    if(loadJob!==job)return false;
    if(!blob)throw new Error('保存した曲が見つかりません');
    url=URL.createObjectURL(toBlob(blob));
   }
   dropC();cur=i;urlNow=url;ui();
   const song=songs[i];$('title').textContent=song.title;$('seek').max=Math.floor(song.duration)||0;
   $('dur').textContent=fmt(song.duration);session(song);render();
   if(pendingOrder&&pendingOrder[0]===i){order=pendingOrder;pendingOrder=null}
  }
  const ready=restorePosition(pos,job,url);
  if(old&&old!==url)revokeUnused(old);if(oc&&oc.url!==old&&oc.url!==url)revokeUnused(oc.url);
  if(!await ready||loadJob!==job)return false;
  loadJob=null;lastAbs=audio.currentTime;
  S.lastId=songs[cur].id;S.lastPos=rel();save();updateProgress();
  msg(ch?'連続再生モード（'+ch.segs.length+'曲をつなげて再生中）':'通常モード（MP3以外の曲は1曲ずつ再生）');
  if(!ch)setTimeout(prefetch,0);
  if(wantPlay&&!initializing){wantPlay=false;startAudio()}
  return true;
 }catch(e){
  if(loadJob===job){loadJob=null;wantPlay=false;userPaused=true;msg(e.message||'曲を読み込めませんでした');playbackState('paused')}
  return false;
 }
}
function step(d){
 if(!songs.length)return;
 if(cur<0){msg('曲を選んでください');return}
 if(C){let n=segIdx+d;if(C.loop&&S.repeat==='all')n=(n+C.segs.length)%C.segs.length;
  if(n>=0&&n<C.segs.length){seekAbs(C.segs[n].start+0.01);setSeg(n);startAudio();return}
  if(n<0){seekAbs(C.segs[0].start);return}
  const last=C.segs[C.segs.length-1].i,k=order.indexOf(last)+1;
  if(k<order.length){load(order[k],true);return}if(S.repeat==='all'){load(order[0],true);return}audio.pause();return}
 if(d>0){const n=nextIdx();if(n<0){audio.pause();return}load(n,true);return}
 let k=order.indexOf(cur)+d;
 if(k<0)k=S.repeat==='all'?order.length-1:0;
 load(order[k],true);
}
function onEnded(){
 if(C){if(S.repeat==='one'){seekAbs(C.segs[segIdx].start);startAudio();return}
  const last=C.segs[C.segs.length-1].i,k=order.indexOf(last)+1;
  if(k<order.length){load(order[k],true);return}if(S.repeat==='all'&&order.length){load(order[0],true);return}userPaused=true;return}
 if(S.repeat==='one'){seekAbs(0);startAudio();return}
 const n=nextIdx();if(n<0){audio.pause();return}
 load(n,true);
}
function posState(){
 if(loadJob||!('mediaSession' in navigator)||!navigator.mediaSession.setPositionState)return;
 const rate=Number.isFinite(audio.playbackRate)&&audio.playbackRate>0?audio.playbackRate:1;
 const valid=(d,p)=>Number.isFinite(d)&&d>0&&Number.isFinite(p)&&p>=0&&p<=d;
 const publish=(d,p)=>{
  if(!valid(d,p))return false;
  try{navigator.mediaSession.setPositionState({duration:d,playbackRate:rate,position:p});return true}catch(e){return false}
 };
 const g=C&&segIdx>=0?C.segs[segIdx]:null;
 // Prefer the displayed song's clock, but retry using the media element if rejected.
 if(g&&publish(g.dur,audio.currentTime-g.start))return;
 const d=audio.duration,p=audio.currentTime;
 if(Number.isFinite(d)&&d>0&&Number.isFinite(p))publish(d,Math.max(0,Math.min(p,d)));
}
function session(s){if(!('mediaSession' in navigator))return;
 navigator.mediaSession.metadata=new MediaMetadata({title:s.title,artist:'畑プレーヤー',artwork:[192,512].map(size=>({src:new URL('icon-'+size+'.png',location.href).href,sizes:size+'x'+size,type:'image/png'}))});
}
let playAttempt=0;
function playbackState(state){if('mediaSession' in navigator)navigator.mediaSession.playbackState=state;posState()}
function playbackSession(){
 try{if(navigator.audioSession)navigator.audioSession.type='playback'}catch(e){}
}
function pausePlayback(){
 ++playAttempt;wantPlay=false;userPaused=true;audio.pause();stopTicker();clearTimeout(pauseTimer);
 // Do not clear src or metadata: Control Center must still be able to resume.
 if(!loadJob){S.lastPos=rel();save()}playbackState('paused');
}
function startAudio(recover=true){
 if(cur<0&&!loadJob){
  wantPlay=false;userPaused=true;
  if(!initializing)msg(songs.length?'曲を選んでください':'曲を追加してください');
  return Promise.resolve();
 }
 if(initializing||loadJob||!audio.getAttribute('src')){
  wantPlay=true;userPaused=false;msg('読み込み中… 再生を予約しました');return Promise.resolve();
 }
 wantPlay=false;
 // Restore a resource that iOS reset to zero, synchronously before remote play().
 const wrap=C&&C.loop&&lastAbs>C.total-3;
 if(lastAbs>1&&audio.currentTime<1&&!wrap){
  const target=Number.isFinite(audio.duration)?Math.min(lastAbs,Math.max(0,audio.duration-0.01)):lastAbs;
  try{seekAbs(target)}catch(e){}
 }
 const attempt=++playAttempt;userPaused=false;clearTimeout(pauseTimer);playbackSession();
 // This must be synchronous in the Media Session action's call stack. No DB awaits.
 let playing;try{playing=audio.play()}catch(e){playing=Promise.reject(e)}
 const i=cur,position=rel();
 const source=audio.src,generation=building;
 return Promise.resolve(playing).then(()=>{
  if(attempt!==playAttempt||generation!==building||source!==audio.src||loadJob||userPaused)return;
  if(songs[cur])session(songs[cur]);playbackState('playing');startTicker();
 }).catch(async e=>{
  if(attempt!==playAttempt||userPaused||source!==audio.src||generation!==building)return;
  playbackState('paused');
  if(!recover||!songs[i]){msg('再生できませんでした。アプリを開いて再生ボタンを押してください。');return}
  // Only a failed direct resume may rebuild a discarded/invalid media resource.
  try{
   const restored=await load(i,false,Math.max(0,position||0));
   if(!restored||attempt!==playAttempt||userPaused||!audio.paused)return;
   await startAudio(false);
  }catch(error){msg('曲を読み直せませんでした。アプリで曲を選び直してください。')}
 });
}
if('mediaSession' in navigator){const m=navigator.mediaSession,h=(a,f)=>{try{m.setActionHandler(a,f)}catch(e){}};
 h('play',()=>startAudio());h('pause',pausePlayback);h('stop',pausePlayback);h('previoustrack',()=>step(-1));h('nexttrack',()=>step(1));
 h('seekto',e=>{seekAbs((C&&segIdx>=0?C.segs[segIdx].start:0)+e.seekTime);posState()});h('seekbackward',e=>{seekAbs(Math.max(0,audio.currentTime-(e.seekOffset||10)));posState()});h('seekforward',e=>{seekAbs(Math.min(audio.duration||0,audio.currentTime+(e.seekOffset||10)));posState()});}
function ui(){
 $('shuffle').classList.toggle('on',S.shuffle);
 $('repeat').classList.toggle('on',S.repeat!=='none');
 $('repeat').textContent={none:'🔁 リピートなし',all:'🔁 全曲リピート',one:'🔂 1曲リピート'}[S.repeat];
 document.body.classList.toggle('dark',S.dark);$('dark').textContent=S.dark?'☀️':'🌙';
 audio.loop=C?C.loop:S.repeat==='one';
}
$('play').onclick=()=>{if(loadJob||initializing){if(wantPlay)pausePlayback();else startAudio();return}if(audio.paused)startAudio();else pausePlayback()};
$('prev').onclick=()=>{if(rel()>3)seekAbs(C&&segIdx>=0?C.segs[segIdx].start:0);else step(-1)};
$('next').onclick=()=>step(1);
$('shuffle').onclick=()=>{S.shuffle=!S.shuffle;makeOrder();save();ui();if(C)rebuild();else prefetch()};
$('repeat').onclick=()=>{S.repeat={none:'all',all:'one',one:'none'}[S.repeat];save();ui();if(C)rebuild();else prefetch()};
$('dark').onclick=()=>{S.dark=!S.dark;save();ui()};
$('convert').onclick=()=>convertLibrary();
$('file').onchange=e=>{const f=[...e.target.files];e.target.value='';if(f.length)addFiles(f)};
$('seek').oninput=e=>{seekAbs((C&&segIdx>=0?C.segs[segIdx].start:0)+(+e.target.value))};
let ticker=null,pauseTimer=null,lastSave=0;
function stopTicker(){if(ticker!==null){clearInterval(ticker);ticker=null}}
function startTicker(){
 if(audio.paused)return;
 if(ticker===null)ticker=setInterval(updateProgress,250);
 updateProgress();
}
function markPaused(){
 $('play').textContent='▶';$('play').setAttribute('aria-label','再生');
 if(!loadJob){S.lastPos=rel();save()}playbackState('paused');
}
audio.onplay=()=>{
 clearTimeout(pauseTimer);$('play').textContent='⏸';$('play').setAttribute('aria-label','一時停止');
 playbackState('playing');startTicker();
};
audio.onpause=()=>{
 stopTicker();clearTimeout(pauseTimer);
 if(userPaused){markPaused();return}
 // iOS can emit pause during seeks / buffering / audio-route changes.
 pauseTimer=setTimeout(()=>{if(audio.paused)markPaused();else startTicker()},300);
};
audio.onended=()=>{stopTicker();clearTimeout(pauseTimer);onEnded();if(audio.paused)playbackState('paused')};
audio.addEventListener('loadedmetadata',()=>{updateProgress();posState()});
audio.addEventListener('durationchange',posState);audio.addEventListener('ratechange',posState);
audio.addEventListener('playing',()=>{clearTimeout(pauseTimer);playbackState('playing');startTicker()});
audio.addEventListener('seeked',()=>{updateProgress();if(!audio.paused)startTicker()});
function updateProgress(){
 if(loadJob)return;
 let t=audio.currentTime;if(!Number.isFinite(t)||t<0)return;
 if(C){
  if(t<1&&lastAbs>5&&!(C.loop&&lastAbs>C.total-3)&&!audio.paused&&document.hidden){}else{
   const n=findSeg(t);if(n!==segIdx)setSeg(n);lastAbs=t}
  const g=C.segs[Math.max(segIdx,0)],r=Math.max(0,Math.min(g.dur,t-g.start));
  $('cur').textContent=fmt(r);$('seek').value=Math.floor(r);
 }else{
  $('cur').textContent=fmt(t);$('seek').value=Math.floor(t);lastAbs=t;
  if(Number.isFinite(audio.duration)&&audio.duration>0){$('seek').max=Math.floor(audio.duration);$('dur').textContent=fmt(audio.duration)}
 }
 posState(); // Every tick; only persistence is throttled.
 const n=Date.now();if(n-lastSave>3000){lastSave=n;S.lastPos=rel();save()}
}
audio.ontimeupdate=updateProgress;
document.addEventListener('visibilitychange',()=>{
 if(loadJob||initializing)return;
 if(document.hidden){if(cur>=0){S.lastPos=rel();save()}return}
 if(cur<0||userPaused)return;
 const wrap=C&&C.loop&&lastAbs>C.total-3;
 if(audio.currentTime<1&&lastAbs>5&&!wrap)seekAbs(lastAbs);
 if(audio.paused)startAudio();else startTicker();
});

window.addEventListener('pagehide',()=>{if(cur>=0&&!loadJob){S.lastPos=rel();save();posState()}});

const APP_VERSION='v13';
$('update').onclick=async()=>{
 if(!confirm('アプリを最新版に更新します（曲は消えません）。よろしいですか？'))return;
 msg('更新中…');
 try{if('serviceWorker' in navigator)for(const r of await navigator.serviceWorker.getRegistrations())await r.unregister();
  if(window.caches)for(const k of await caches.keys())if(k.startsWith('hatake-player-'))await caches.delete(k)}catch(e){}
 location.replace(location.pathname+'?u='+Date.now());
};
(async()=>{
 ui();$('ver').textContent=APP_VERSION;msg('読み込み中…');db=await openDB();
 songs=(await tx('meta','readonly',s=>s.getAll())).sort((a,b)=>a.added-b.added);
 makeOrder();render();usage();
 if(navigator.storage&&navigator.storage.persist)navigator.storage.persist().then(p=>{window.__persisted=p});
 idleScreen();msg('');
 initializing=false;
 if(wantPlay){wantPlay=false;userPaused=true;if(songs.length)msg('曲を選んでください')}
 if('serviceWorker' in navigator){
  const had=!!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener('controllerchange',()=>{
   if(!had)return;
   // Updating the worker must not tear down playing OR paused remote audio.
   if(loadJob||wantPlay||audio.getAttribute('src')){msg('更新があります。再生を終えて「アプリを更新」を押してください');return}
   const k='hp-reloaded',t=+sessionStorage.getItem(k)||0;
   if(Date.now()-t<30000)return;sessionStorage.setItem(k,Date.now());location.reload()});
  navigator.serviceWorker.register('sw.js',{updateViaCache:'none'}).then(r=>{window.__reg=r;r.update().catch(()=>{})}).catch(e=>console.warn(e));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&window.__reg)window.__reg.update().catch(()=>{})});
 }
 window.__ready=true;
})();
