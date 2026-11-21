'use strict';
const $=id=>document.getElementById(id);
let audio=$('audio'),audioGen=0;
const retiredURLs=new Set();
function revokeUnused(url){
 if(!url)return;
 if(audio&&(url===audio.src||url===audio.currentSrc)){retiredURLs.add(url);return}
 URL.revokeObjectURL(url);retiredURLs.delete(url);
}
let db,songs=[],cur=-1,urlNow=null,order=[];
let loadJob=null,wantPlay=false,initializing=true;
const S=Object.assign({shuffle:false,repeat:'none',dark:false,lastId:null,lastPos:0},JSON.parse(localStorage.getItem('hp-settings')||'{}'));
const save=()=>localStorage.setItem('hp-settings',JSON.stringify(S));
const fmt=s=>{s=Math.floor(s||0);return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
const msg=t=>{$('msg').textContent=t};
function idleScreen(){
 $('title').textContent=songs.length?'曲を選んでください':'曲を追加してください';
 $('seek').max=0;$('seek').value=0;$('cur').textContent=fmt(0);$('dur').textContent=fmt(0);
 showDiag(null);
}

let dbOpening=null;
function bindDB(d){
 db=d;
 d.onclose=()=>{if(db===d)db=null};
 d.onerror=()=>{if(db===d)db=null};
 d.onversionchange=()=>{if(db===d){db=null;try{d.close()}catch(e){}}};
}
function dropDB(d){
 const x=d||db;if(!x){if(!d)db=null;return}
 if(db===x)db=null;
 try{x.onclose=x.onerror=x.onversionchange=null}catch(e){}
 try{x.close()}catch(e){}
}
function openDB(){
 if(db)return Promise.resolve(db);
 if(dbOpening)return dbOpening;
 dbOpening=new Promise((res,rej)=>{
  let r;try{r=indexedDB.open('hatake-player',1)}catch(e){rej(e);return}
  r.onupgradeneeded=()=>{const d=r.result;if(!d.objectStoreNames.contains('meta'))d.createObjectStore('meta',{keyPath:'id'});if(!d.objectStoreNames.contains('blobs'))d.createObjectStore('blobs')};
  r.onsuccess=()=>{bindDB(r.result);res(r.result)};
  r.onerror=()=>rej(r.error);
 }).finally(()=>{dbOpening=null});
 return dbOpening;
}
function wakeDB(){if(!db)openDB().catch(()=>{})}
function isDbGone(e){
 const n=(e&&e.name)||'',m=(e&&(e.message||''))||'';
 return n==='InvalidStateError'||n==='TransactionInactiveError'||n==='UnknownError'||n==='AbortError'
  ||/connection is closing|database connection|Failed to execute ['']transaction['']|InvalidState|TransactionInactive|UnknownError/i.test(n+' '+m);
}
function idbErrorMessage(e){
 const n=(e&&e.name)||'',m=(e&&e.message)||'';
 if(n==='QuotaExceededError'||/QuotaExceeded/i.test(m))return '保存容量が足りません。不要な曲を消すか、iPhoneの空き容量を増やしてください';
 return 'データの読み書きに失敗しました。もう一度試してください';
}
async function tx(store,mode,fn){
 if(window.__txDelay)await new Promise(r=>setTimeout(r,window.__txDelay));
 let last;
 for(let attempt=0;attempt<3;attempt++){
  try{
   const d=await openDB();
   return await new Promise((res,rej)=>{
    let t;try{t=d.transaction(store,mode)}catch(e){rej(e);return}
    let out;try{out=fn(t.objectStore(store))}catch(e){rej(e);return}
    t.oncomplete=()=>res(out&&out.result!==undefined?out.result:out);
    t.onerror=()=>rej(t.error||new Error('transaction error'));
    t.onabort=()=>rej(t.error||new Error('transaction aborted'));
   });
  }catch(e){
   last=e;
   if(!isDbGone(e)||attempt===2)throw e;
   // Already closing/dead (iOS memory pressure, or a superseded load's work). Drop the handle; do not keep using it.
   dropDB();
   await new Promise(r=>setTimeout(r,attempt*40));
  }
 }
 throw last;
}

function getDuration(blob){return new Promise(res=>{const a=new Audio();const u=URL.createObjectURL(blob);
 const done=d=>{revokeUnused(u);res(isFinite(d)?d:0)};
 a.preload='metadata';a.onloadedmetadata=()=>done(a.duration);a.onerror=()=>done(0);setTimeout(()=>done(0),8000);a.src=u})}

// ===== 形式をそろえる（m4a・wav・形式違いのMP3を44.1kHzステレオMP3にして連続再生モードに入れる） =====
// Bytes decide, not MIME: an MP3 already in the target format (isTargetMp3) is never re-encoded.
function isMp3(u){
 if(u.length<16)return false;
 const tag=String.fromCharCode(...u.subarray(0,12));
 // Container signatures (m4a/mp4 ftyp, wav, flac, ogg, aiff, caf) are never MP3,
 // even if random bytes inside look like MP3 frame headers.
 if(/^(RIFF|fLaC|OggS|FORM|caff)/.test(tag)||tag.slice(4,8)==='ftyp')return false;
 const r=parseMp3(u);if(!r||r.offsets.length<2)return false;
 const p=skipId3(u,0),end=stripTail(u,u.length);
 return (r.end-r.start)>=0.5*(end-p);
}
// Target format for the whole library: MPEG-1 Layer III, 44100 Hz, 2 channels.
const TARGET_SR=44100;
// True for MP3 bytes already in the target format (these are never re-encoded).
function isTargetMp3(u){
 if(!isMp3(u))return false;
 const r=parseMp3(u);return !!r&&r.sr===TARGET_SR&&r.v===3&&r.mode!==3;
}
// MIME from the file picker is untrusted (iOS often reports m4a as audio/mpeg).
// Bytes decide: only real MP3s are joined; other files get a type the player can use.
function sniffType(u,fallback){
 if(!u||u.length<12)return fallback||'application/octet-stream';
 if(isMp3(u))return 'audio/mpeg';
 const tag=String.fromCharCode(...u.subarray(0,12));
 if(tag.slice(4,8)==='ftyp')return 'audio/mp4';
 if(/^RIFF/.test(tag)&&String.fromCharCode(...u.subarray(8,12))==='WAVE')return 'audio/wav';
 if(/^fLaC/.test(tag))return 'audio/flac';
 if(/^OggS/.test(tag))return 'audio/ogg';
 if(/^FORM/.test(tag))return 'audio/aiff';
 if(/^caff/.test(tag))return 'audio/x-caf';
 return fallback||'application/octet-stream';
}
async function toPlayableBlob(b){
 const buf=b instanceof Blob?await b.arrayBuffer():b.buf;
 return new Blob([buf],{type:sniffType(new Uint8Array(buf),b&&b.type)});
}
function playErrorMessage(e){
 const m=(e&&(e.message||e.name))||'';
 if(/時間/.test(m))return '読み込みに時間がかかっています。曲を選び直してください';
 if(/見つかりません|中止/.test(m))return m;
 if(isDbGone(e))return idbErrorMessage(e);
 if(!m||/TypeError|undefined|null is not|Cannot read|AbortError|NotSupported|NotAllowed|The operation|EncodingError|DOMException|Failed to execute|IDBDatabase|IndexedDB|InvalidState|UnknownError/i.test(m))
  return '音楽ファイルを読み込めませんでした';
 // Never show raw English DOMException / browser engine text in #msg.
 if(/[A-Za-z]{6,}/.test(m)&&!/[ぁ-んァ-ン一-龥]/.test(m))return '音楽ファイルを読み込めませんでした';
 return m;
}
const MAX_CONVERT_SEC=30*60; // Decoded PCM of longer files can exhaust iPhone memory.
// Length in seconds without a media element for MP3s; falls back to getDuration.
async function lengthOf(u,type){const r=isMp3(u)?parseMp3(u):null;return r?r.dur:await getDuration(new Blob([u],{type}))}
let actx=null,encQ=Promise.resolve();
function audioCtx(){
 if(actx)return actx;
 const A=window.AudioContext||window.webkitAudioContext;if(!A)return null;
 // 44.1kHz output matches most MP3s, so converted songs join the same stream.
 try{actx=new A({sampleRate:TARGET_SR})}catch(e){try{actx=new A()}catch(e2){actx=null}}
 return actx;
}
let DECODE_TIMEOUT_MS=90000,RESAMPLE_TIMEOUT_MS=90000,SEGMENT_SEC=30;
let lastYield=0,lastPaint=0,cancelWork=false;
const tick=()=>new Promise(r=>setTimeout(r,0));
function throwIfCancelled(){if(cancelWork)throw new Error('中止')}
function withTimeout(p,ms,err){
 let t;
 return Promise.race([
  Promise.resolve(p),
  new Promise((_,rej)=>t=setTimeout(()=>rej(new Error(err||'timeout')),ms))
 ]).finally(()=>clearTimeout(t));
}
// Yield so iPhone Safari can paint progress and stay responsive during long JS loops.
async function yieldUi(force){
 throwIfCancelled();
 const now=performance.now();
 if(!force&&now-lastYield<24)return;
 lastYield=now;await tick();
 throwIfCancelled();
}
async function msgProgress(t){
 $('msg').textContent=t;
 const now=performance.now();
 if(now-lastPaint>=250){
  lastPaint=now;
  await new Promise(r=>requestAnimationFrame(()=>setTimeout(r,0)));
 }else await tick();
 lastYield=performance.now();
 throwIfCancelled();
}
function showWork(on){
 const c=$('cancel'),b=$('convert');
 if(c)c.hidden=!on;if(b)b.disabled=on;
}
// Bring any decoded buffer to TARGET_SR. Old iOS may refuse a 44.1kHz AudioContext
// (decodeAudioData then returns the device rate, e.g. 48kHz).
async function resampleTo(ab,rate){
 if(ab.sampleRate===rate)return ab;
 const nch=Math.min(ab.numberOfChannels,2),len=Math.max(1,Math.ceil(ab.duration*rate));
 const O=window.OfflineAudioContext||window.webkitOfflineAudioContext;
 // One OfflineAudioContext of a long song can exhaust iPhone memory; only use it for short buffers.
 if(O&&ab.duration<=SEGMENT_SEC){try{
  const oc=new O(nch,len,rate),src=oc.createBufferSource();
  // >2 channels are downmixed by Web Audio's speaker rules (keeps center/surround).
  src.buffer=ab;src.connect(oc.destination);src.start(0);
  try{
   const render=new Promise((res,rej)=>{let p;oc.oncomplete=e=>res(e.renderedBuffer);
    try{p=oc.startRendering()}catch(e){rej(e);return}if(p&&p.then)p.then(res,rej)});
   const out=await withTimeout(render,RESAMPLE_TIMEOUT_MS,'44.1kHzへの変換が時間切れになりました');
   if(out&&out.sampleRate===rate&&out.length)return out;
  }finally{try{if(oc.close)oc.close()}catch(e){}}
 }catch(e){console.warn(e)}}
 // Fallback: linear interpolation per channel.
 const ratio=ab.sampleRate/rate,chans=[];
 for(let c=0;c<ab.numberOfChannels;c++){const d=ab.getChannelData(c),o=new Float32Array(len);
  for(let i=0;i<len;i++){const x=i*ratio,k=Math.floor(x),f=x-k,a=d[Math.min(k,d.length-1)],b=d[Math.min(k+1,d.length-1)];o[i]=a+(b-a)*f;
   if((i&8191)===8191)await yieldUi()}
  chans.push(o);await yieldUi(true)}
 return{sampleRate:rate,length:len,numberOfChannels:chans.length,duration:len/rate,getChannelData:c=>chans[c]};
}
// ===== 音量をそろえる（小さい曲だけ上げる。再生は1本につなげるので、音量は変換時に書き込む） =====
// Integrated loudness per ITU-R BS.1770 (K-weighting, 400ms blocks, -70 LUFS and -10 LU gates).
const TARGET_LUFS=-14;   // Typical loudness of current pop masters / streaming references.
const MIN_BOOST_DB=1;    // Smaller differences are not worth a lossy re-encode.
const MAX_BOOST_DB=12;   // Never lift very quiet material (noise, silence-heavy tracks) further.
const CEILING_DB=-1.5;   // Sample-peak ceiling before MP3 encoding (decoded output stays near -1 dBFS).
const MAX_LIMIT_DB=6;    // Up to this much gain reduction on short peaks via the soft limiter.
const LOUD_VERSION=1;    // meta.loudChecked: analyzed once, never boosted twice.
function biquad(b0,b1,b2,a1,a2){let x1=0,x2=0,y1=0,y2=0;return x=>{const y=b0*x+b1*x1+b2*x2-a1*y1-a2*y2;x2=x1;x1=x;y2=y1;y1=y;return y}}
function kWeight(fs){
 // Coefficients for any rate (as in libebur128).
 let f0=1681.974450955533,G=3.999843853973347,Q=0.7071752369554196,K=Math.tan(Math.PI*f0/fs);
 const Vh=Math.pow(10,G/20),Vb=Math.pow(Vh,0.4996667741545416),a0=1+K/Q+K*K;
 const pre=biquad((Vh+Vb*K/Q+K*K)/a0,2*(K*K-Vh)/a0,(Vh-Vb*K/Q+K*K)/a0,2*(K*K-1)/a0,(1-K/Q+K*K)/a0);
 f0=38.13547087602444;Q=0.5003270373238773;K=Math.tan(Math.PI*f0/fs);const d=1+K/Q+K*K;
 const rlb=biquad(1,-2,1,2*(K*K-1)/d,(1-K/Q+K*K)/d);
 return x=>rlb(pre(x));
}
function loudnessAcc(fs){return{fl:kWeight(fs),fr:kWeight(fs),step:Math.round(fs/10),sub:[],peak:0,restL:null,restR:null,seen:0}}
async function loudnessPush(acc,L,R,onPct,totalHint){
 const fl=acc.fl,fr=acc.fr,step=acc.step;
 let L0=L,R0=R;
 if(acc.restL&&acc.restL.length){
  const p=acc.restL.length,n=p+L.length,a=new Float32Array(n),b=new Float32Array(n);
  a.set(acc.restL);a.set(L,p);b.set(acc.restR);b.set(R,p);L0=a;R0=b;acc.restL=acc.restR=null;
 }
 let o=0,len=L0.length;
 for(;o+step<=len;o+=step){let s=0;const e=o+step;
  for(let i=o;i<e;i++){const l=L0[i],r=R0[i],ap=l<0?-l:l,bp=r<0?-r:r;if(ap>acc.peak)acc.peak=ap;if(bp>acc.peak)acc.peak=bp;
   const x=fl(l),y=fr(r);s+=x*x+y*y}
  acc.sub.push(s/step);acc.seen+=step;
  if(performance.now()-lastYield>=24){if(onPct&&totalHint)await onPct(Math.min(99,Math.floor(100*acc.seen/totalHint)));else await yieldUi(true)}
 }
 if(o<len){acc.restL=L0.subarray(o);acc.restR=R0.subarray(o)}
}
function loudnessFinish(acc){
 if(acc.restL&&acc.restL.length){
  const L=acc.restL,R=acc.restR,fl=acc.fl,fr=acc.fr;let s=0;
  for(let i=0;i<L.length;i++){const l=L[i],r=R[i],ap=l<0?-l:l,bp=r<0?-r:r;if(ap>acc.peak)acc.peak=ap;if(bp>acc.peak)acc.peak=bp;
   const x=fl(l),y=fr(r);s+=x*x+y*y}
  acc.sub.push(s/Math.max(1,L.length));acc.restL=acc.restR=null;
 }
 const sub=acc.sub,step=acc.step,blocks=[];
 for(let k=0;k+4<=sub.length;k++)blocks.push((sub[k]+sub[k+1]+sub[k+2]+sub[k+3])/4);
 if(!blocks.length&&sub.length)blocks.push(sub.reduce((a,b)=>a+b,0)*step/Math.max(1,acc.seen||step));
 const lu=e=>-0.691+10*Math.log10(e),mean=a=>a.reduce((x,y)=>x+y,0)/a.length;
 const abs=blocks.filter(e=>e>0&&lu(e)>-70);
 let lufs=-Infinity;
 if(abs.length){const rel=lu(mean(abs))-10,g=abs.filter(e=>lu(e)>rel);lufs=lu(mean(g.length?g:abs))}
 return{lufs,peakDb:acc.peak>0?20*Math.log10(acc.peak):-Infinity};
}
async function measureLoudness(L,R,fs,onPct){
 const acc=loudnessAcc(fs);
 await loudnessPush(acc,L,R,onPct,L.length);
 return loudnessFinish(acc);
}
// Boost in dB (0 = leave as is). Only quiet songs are raised; loud ones are never turned down.
function boostFor(m){
 if(!Number.isFinite(m.lufs)||!Number.isFinite(m.peakDb))return 0; // Silence.
 let g=Math.min(TARGET_LUFS-m.lufs,MAX_BOOST_DB);if(g<MIN_BOOST_DB)return 0;
 g=Math.min(g,CEILING_DB-m.peakDb+MAX_LIMIT_DB); // Limiter takes at most MAX_LIMIT_DB off any peak.
 return g>=MIN_BOOST_DB?Math.round(g*10)/10:0;
}
async function limiterEnvFromReq(req,any,fs){
 if(!any)return null;
 const nb=req.length-1,env=new Float32Array(nb+1);
 // Neighbour minimum keeps the interpolated gain under each block's requirement.
 for(let b=0;b<=nb;b++){env[b]=Math.min(req[b],b>0?req[b-1]:1,b<nb?req[b+1]:1);if((b&16383)===16383)await yieldUi()}
 const att=1/Math.max(1,Math.round(0.010*fs/32)),rel=1/Math.max(1,Math.round(0.150*fs/32));
 for(let b=nb-1;b>=0;b--){env[b]=Math.min(env[b],env[b+1]+att);if((b&16383)===16383)await yieldUi()}
 for(let b=1;b<=nb;b++){env[b]=Math.min(env[b],env[b-1]+rel);if((b&16383)===16383)await yieldUi()}
 return env;
}
// Per-32-sample gain envelope (<=1) so |x*gain*env| never exceeds the ceiling:
// 10ms look-ahead attack, 150ms release, linear between blocks. No hard clipping.
async function limiterEnv(L,R,gain,fs){
 const B=32,len=L.length,nb=Math.ceil(len/B),ceil=Math.pow(10,CEILING_DB/20),req=new Float32Array(nb+1);
 let any=false;
 for(let b=0;b<nb;b++){let pk=0;const e=Math.min(len,(b+1)*B);
  for(let i=b*B;i<e;i++){const a=Math.abs(L[i]),c=Math.abs(R[i]);if(a>pk)pk=a;if(c>pk)pk=c}
  const r=pk*gain>ceil?ceil/(pk*gain):1;req[b]=r;if(r<1)any=true;
  if((b&2047)===2047)await yieldUi()}
 req[nb]=req[nb-1];
 return limiterEnvFromReq(req,any,fs);
}
async function limiterEnvFromPeaks(pks,gain,fs){
 const nb=pks.length,ceil=Math.pow(10,CEILING_DB/20),req=new Float32Array(nb+1);
 let any=false;
 for(let b=0;b<nb;b++){const r=pks[b]*gain>ceil?ceil/(pks[b]*gain):1;req[b]=r;if(r<1)any=true;
  if((b&2047)===2047)await yieldUi()}
 if(!any)return null;
 req[nb]=req[nb-1];
 return limiterEnvFromReq(req,true,fs);
}
function PeakCol(){this.pk=[];this.cur=0;this.n=0}
PeakCol.prototype.push=function(L,R){
 for(let i=0;i<L.length;i++){const a=L[i]<0?-L[i]:L[i],c=R[i]<0?-R[i]:R[i],p=a>c?a:c;if(p>this.cur)this.cur=p;this.n++;if((this.n&31)===0){this.pk.push(this.cur);this.cur=0}}
};
PeakCol.prototype.finish=function(){if(this.n&31)this.pk.push(this.cur);return this.pk};
// Stereo view of a decoded buffer: mono shares one array; >2ch mixed (even->L, odd->R).
async function stereoPcm(ab){
 const nch=ab.numberOfChannels,ch=[];for(let c=0;c<nch;c++)ch.push(ab.getChannelData(c));
 if(nch===1)return{L:ch[0],R:ch[0]};if(nch===2)return{L:ch[0],R:ch[1]};
 const len=ab.length,L=new Float32Array(len),R=new Float32Array(len),nl=Math.ceil(nch/2),nr=Math.floor(nch/2);
 for(let c=0;c<nch;c++){const d=ch[c],o=c%2?R:L;for(let i=0;i<len;i++){o[i]+=d[i];if((i&16383)===16383)await yieldUi()}}
 for(let i=0;i<len;i++){L[i]/=nl;R[i]/=nr;if((i&16383)===16383)await yieldUi()}
 return{L,R};
}
// Copy only the bytes we are about to decode (decodeAudioData detaches its input).
async function decodeBytes(ctx,bytes){
 throwIfCancelled();
 const copy=bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);
 return withTimeout(new Promise((res,rej)=>{let p;
  try{p=ctx.decodeAudioData(copy,res,e=>rej(e||new Error('decode')))}catch(e){rej(e);return}
  if(p&&p.then)p.then(res,rej)}),DECODE_TIMEOUT_MS,'音声の読み込みが時間切れになりました');
}
async function decodeRaw(ctx,raw){
 const u=raw instanceof Uint8Array?raw:new Uint8Array(raw);
 return decodeBytes(ctx,u);
}
async function pcmToTarget(L,R,sr){
 if(sr===TARGET_SR)return{L,R};
 const len=Math.max(1,Math.round(L.length*TARGET_SR/sr)),ratio=sr/TARGET_SR;
 const oL=new Float32Array(len),oR=new Float32Array(len);
 for(let i=0;i<len;i++){const x=i*ratio,k=Math.floor(x),f=x-k,a=Math.min(k,L.length-1),b=Math.min(k+1,L.length-1);
  oL[i]=L[a]+(L[b]-L[a])*f;oR[i]=R[a]+(R[b]-R[a])*f;if((i&8191)===8191)await yieldUi()}
 return{L:oL,R:oR};
}
function mp3Slice(u,parsed,i,j){
 const start=parsed.start+parsed.offsets[i];
 const end=j<parsed.offsets.length?parsed.start+parsed.offsets[j]:parsed.end;
 return u.subarray(start,end);
}
async function decodeMp3Frames(ctx,u,parsed,i,j){
 throwIfCancelled();
 let ab=await decodeBytes(ctx,mp3Slice(u,parsed,i,j));
 if(!ab||!ab.length)throw new Error('音声が空です');
 const sr=ab.sampleRate;let {L,R}=await stereoPcm(ab);ab=null;
 const expect=Math.max(1,Math.round((j-i)*parsed.smp*(sr/parsed.sr)));
 let delay=0;
 if(L.length>expect){const typ=Math.round(528*(sr/parsed.sr));delay=Math.min(L.length-expect,typ)}
 return{L,R,sr,delay};
}
function uniquePcm(dec,parsed,from,to,decFrom){
 const scale=dec.sr/parsed.sr,smp=parsed.smp;
 const start=dec.delay+Math.round((from-decFrom)*smp*scale);
 const count=Math.round((to-from)*smp*scale);
 const a=Math.max(0,start),b=Math.min(dec.L.length,Math.max(a,start+count));
 return{L:dec.L.subarray(a,b),R:dec.R.subarray(a,b),sr:dec.sr};
}
const s16=v=>v<=-1?-32768:v>=1?32767:Math.round(v<0?v*32768:v*32767);
async function encodePcm(L,R,gainDb,env,onPct,sampleBase,totalHint){
 const len=L.length,g=Math.pow(10,(gainDb||0)/20),BLOCK=1152*32,EB=32,OL=new Int16Array(BLOCK),OR=new Int16Array(BLOCK),out=[];
 const enc=arguments[7]||new lamejs.Mp3Encoder(2,TARGET_SR,192);
 const own=!arguments[7];
 for(let o=0;o<len;o+=BLOCK){
  const n=Math.min(BLOCK,len-o);
  for(let i=0;i<n;i++){const k=(sampleBase||0)+o+i;let a=g;
   if(env){const b=(k/EB)|0,f=(k-b*EB)/EB,e0=env[Math.min(b,env.length-1)],e1=env[Math.min(b+1,env.length-1)];a*=e0+(e1-e0)*f}
   OL[i]=s16(L[o+i]*a);OR[i]=s16(R[o+i]*a);
   if((i&8191)===8191)await yieldUi()}
  for(let f=0;f<n;f+=1152){const e=Math.min(n,f+1152);
   const mm=enc.encodeBuffer(OL.subarray(f,e),OR.subarray(f,e));if(mm.length)out.push(new Uint8Array(mm.buffer,mm.byteOffset,mm.length));
   if(performance.now()-lastYield>=24){if(onPct)await onPct('encode',Math.floor(100*Math.min(1,((sampleBase||0)+o+f)/(totalHint||len))));else await yieldUi(true)}}
 }
 return{enc,out,own};
}
async function processMp3Segments(ctx,u,parsed,{format,loud},onPct){
 const nF=parsed.offsets.length,fPer=Math.max(1,Math.round(SEGMENT_SEC*parsed.sr/parsed.smp));
 const over=Math.max(1,Math.round(0.5*parsed.sr/parsed.smp));
 const totalEst=Math.round(parsed.dur*TARGET_SR);
 const acc=loud?loudnessAcc(TARGET_SR):null,peaks=new PeakCol();
 let samplePos=0;
 const segs=[];
 for(let a=0;a<nF;a+=fPer)segs.push([a,Math.min(nF,a+fPer)]);
 async function segmentPcm(a,b){
  const d0=Math.max(0,a-over),d1=Math.min(nF,b+1);
  const dec=await decodeMp3Frames(ctx,u,parsed,d0,d1);
  const uq=uniquePcm(dec,parsed,a,b,d0);
  dec.L=dec.R=null;
  return pcmToTarget(uq.L,uq.R,uq.sr);
 }
 if(loud){
  for(let s=0;s<segs.length;s++){
   if(onPct)await onPct('measure',Math.floor(100*s/segs.length));
   const {L,R}=await segmentPcm(segs[s][0],segs[s][1]);
   await loudnessPush(acc,L,R,p=>onPct&&onPct('measure',p),totalEst);
   peaks.push(L,R);samplePos+=L.length;L.fill(0);R.fill(0);
   await yieldUi(true);
  }
 }
 const m=loud?loudnessFinish(acc):{lufs:null,peakDb:null},gainDb=loud?boostFor(m):0;
 if(!format&&!gainDb)return{buf:null,lufs:m.lufs,peakDb:m.peakDb,gainDb:0};
 if(!window.lamejs||!lamejs.Mp3Encoder)throw new Error('変換プログラムが読み込まれていません');
 const env=gainDb?await limiterEnvFromPeaks(peaks.finish(),Math.pow(10,gainDb/20),TARGET_SR):null;
 const enc=new lamejs.Mp3Encoder(2,TARGET_SR,192),chunks=[];
 let pos=0;
 for(let s=0;s<segs.length;s++){
  if(onPct)await onPct('encode',Math.floor(100*s/segs.length));
  const {L,R}=await segmentPcm(segs[s][0],segs[s][1]);
  const part=await encodePcm(L,R,gainDb,env,onPct,pos,totalEst,enc);
  chunks.push.apply(chunks,part.out);pos+=L.length;L.fill(0);R.fill(0);
  await yieldUi(true);
 }
 if(onPct)await onPct('encode',100);
 const mm=enc.flush();if(mm.length)chunks.push(new Uint8Array(mm.buffer,mm.byteOffset,mm.length));
 const buf=await new Blob(chunks,{type:'audio/mpeg'}).arrayBuffer();chunks.length=0;
 if(!isTargetMp3(new Uint8Array(buf)))throw new Error('変換結果が44.1kHzステレオのMP3になりませんでした');
 return{buf,lufs:m.lufs,peakDb:m.peakDb,gainDb};
}
// Decode, optionally measure loudness, and encode a 192kbps 44.1kHz stereo MP3 with the
// boost baked in. format: must re-encode (not target MP3). loud: analyze loudness.
// Returns {buf (null if unchanged), lufs, peakDb, gainDb}. Throws on failure.
async function processAudio(raw,{format=true,loud=true}={},onPct){
 if(format&&(!window.lamejs||!lamejs.Mp3Encoder))throw new Error('変換プログラムが読み込まれていません');
 const ctx=audioCtx();if(!ctx)throw new Error('この端末では変換できません');
 if(ctx.state==='suspended'){try{await ctx.resume()}catch(e){}}
 const u=raw instanceof Uint8Array?raw:new Uint8Array(raw);
 const parsed=isMp3(u)?parseMp3(u):null;
 if(parsed&&parsed.dur>MAX_CONVERT_SEC)throw new Error('長すぎる');
 // Long MP3s: decode ~30s frame slices so iOS never holds the whole PCM at once.
 if(parsed&&parsed.offsets.length&&parsed.dur>SEGMENT_SEC)
  return processMp3Segments(ctx,u,parsed,{format,loud},onPct);
 let ab=await decodeBytes(ctx,u);
 if(!ab||!ab.length)throw new Error('音声が空です');
 ab=await resampleTo(ab,TARGET_SR);
 if(ab.sampleRate!==TARGET_SR||!ab.length)throw new Error('44.1kHzにできませんでした');
 let {L,R}=await stereoPcm(ab);ab=null;const len=L.length;
 let m={lufs:null,peakDb:null},gainDb=0;
 if(loud){m=await measureLoudness(L,R,TARGET_SR,p=>onPct&&onPct('measure',p));gainDb=boostFor(m)}
 if(!format&&!gainDb){L=R=null;return{buf:null,lufs:m.lufs,peakDb:m.peakDb,gainDb:0}}
 if(!window.lamejs||!lamejs.Mp3Encoder)throw new Error('変換プログラムが読み込まれていません');
 const env=gainDb?await limiterEnv(L,R,Math.pow(10,gainDb/20),TARGET_SR):null;
 const part=await encodePcm(L,R,gainDb,env,onPct,0,len);
 if(part.own){const mm=part.enc.flush();if(mm.length)part.out.push(new Uint8Array(mm.buffer,mm.byteOffset,mm.length))}
 L=R=null;
 const buf=await new Blob(part.out,{type:'audio/mpeg'}).arrayBuffer();part.out.length=0;
 if(!isTargetMp3(new Uint8Array(buf)))throw new Error('変換結果が44.1kHzステレオのMP3になりませんでした');
 return{buf,lufs:m.lufs,peakDb:m.peakDb,gainDb};
}
async function toMp3(raw,onPct){return (await processAudio(raw,{format:true,loud:false},(k,p)=>onPct&&onPct(p))).buf}
// One job at a time to limit iPhone memory.
function processOne(raw,opts,onPct){const job=encQ.then(()=>processAudio(raw,opts,onPct));encQ=job.catch(()=>{});return job}
const loudMeta=r=>({loudChecked:LOUD_VERSION,lufs:Number.isFinite(r.lufs)?Math.round(r.lufs*10)/10:null,gainDb:r.gainDb||0});
const IMPORT_HINT='。形式や音量をそろえる場合は「形式と音量をそろえる」を押してください';
async function convertLibrary(){
 const btn=$('convert');if(btn.disabled)return;
 cancelWork=false;
 // Resume inside the tap gesture; awaiting IndexedDB first would drop it on iOS.
 const ctx=audioCtx();if(ctx&&ctx.state==='suspended'){try{ctx.resume()}catch(e){}}
 showWork(true);
 let stopped=false;
 try{
  msg('形式と音量を調べる曲を探しています…');
  const todo=[];
  for(const s of songs.slice()){
   throwIfCancelled();
   const b=await tx('blobs','readonly',st=>st.get(s.id));if(!b)continue;
   const u=new Uint8Array(b instanceof Blob?await b.arrayBuffer():b.buf);
   const type=b.type||'';
   if(isTargetMp3(u)){
    // Already 44.1kHz stereo MP3: re-encode only for a needed boost; fix a wrong stored type.
    if(type!=='audio/mpeg'&&type!=='audio/mp3')await tx('blobs','readwrite',st=>st.put({buf:u.buffer.byteLength===u.length?u.buffer:u.slice().buffer,type:'audio/mpeg'},s.id));
    if(s.loudChecked||s.duration>MAX_CONVERT_SEC)continue; // Long songs are silently left alone.
    todo.push({id:s.id,format:false});await yieldUi();continue}
   todo.push({id:s.id,format:true});await yieldUi();
  }
  if(!todo.length){msg('そろえる必要がある曲はありません');return}
  let fmtN=0,boostN=0,n=0;const failed=[];let curChanged=false;
  for(const {id,format} of todo){
   const s=songs.find(x=>x.id===id);if(!s)continue;n++;
   const label='そろえています… '+n+'/'+todo.length+'曲 '+s.title;await msgProgress(label);
   try{
    const b=await tx('blobs','readonly',st=>st.get(id));if(!b)continue;
    if(s.duration>MAX_CONVERT_SEC)throw new Error('長すぎる');
    const raw=b instanceof Blob?await b.arrayBuffer():b.buf;
    const r=await processOne(raw,{format,loud:!s.loudChecked},(k,p)=>msgProgress(
     (k==='measure'?'音量を調べています… ':'そろえています… ')+n+'/'+todo.length+'曲 '+s.title+'（'+p+'%）'));
    const now=songs.find(x=>x.id===id);if(!now)continue; // Deleted meanwhile.
    const meta=Object.assign({},now,s.loudChecked?{}:loudMeta(r));
    if(r.buf){
     const pm=parseMp3(new Uint8Array(r.buf));
     Object.assign(meta,{size:r.buf.byteLength,duration:pm?pm.dur:now.duration});
     await tx('blobs','readwrite',st=>st.put({buf:r.buf,type:'audio/mpeg'},id));parseCache.delete(id);
     if(format)fmtN++;if(r.gainDb)boostN++;
     if(cur>=0&&songs[cur]&&songs[cur].id===id)curChanged=true;
    }
    await tx('meta','readwrite',st=>st.put(meta));
    Object.assign(now,meta);
   }catch(e){
    if(e&&e.message==='中止'){stopped=true;break}
    console.warn(e);failed.push(s.title)
   }
   await yieldUi(true);
  }
  const parts=[];
  if(fmtN)parts.push(fmtN+'曲を44.1kHzステレオのMP3にそろえました');
  if(boostN)parts.push(boostN+'曲の音量を上げました');
  let t;
  if(stopped)t='中止しました'+(parts.length?'（'+parts.join('。')+'）':'');
  else{
   if(!parts.length)parts.push(failed.length?'そろえられた曲はありません':'音量を調べました。変える必要がある曲はありませんでした');
   t=parts.join('。');
   if(failed.length)t+='（そろえられなかった曲: '+failed.join('、')+'。Apple Musicなど保護された曲や長すぎる曲は元のまま残しています）';
  }
  const done=fmtN+boostN;
  render();usage();makeOrder();dropPre();
  if(done&&cur>=0&&(C||curChanged)&&!loadJob){
   // Rejoin with the new MP3s; only resumes if it was already playing.
   const playing=!audio.paused,pos=rel();await load(cur,playing,pos,'seek');
  }else if(done)prefetch();
  msg(t);
 }catch(e){
  if(e&&e.message==='中止'){msg('中止しました');}
  else{console.warn(e);msg(isDbGone(e)?idbErrorMessage(e):'そろえ処理に失敗しました。もう一度試してください')}
 }finally{showWork(false);cancelWork=false}
}

async function addFiles(files){
 const hadSongs=!!songs.length;
 let n=0,ok=0;const total=files.length;
 for(const f of files){
  n++;
  const label='取り込み中… '+n+'/'+total+'曲 '+f.name;await msgProgress(label);
  const ext=(f.name.split('.').pop()||'').toLowerCase();
  const types={mp3:'audio/mpeg',m4a:'audio/mp4',aac:'audio/aac',wav:'audio/wav',mp4:'audio/mp4',caf:'audio/x-caf',aif:'audio/aiff',aiff:'audio/aiff',flac:'audio/flac',ogg:'audio/ogg',opus:'audio/ogg'};
  if(!(f.type||'').startsWith('audio/')&&!types[ext]){msg('音楽ファイルではないのでスキップ: '+f.name);continue}
  let buf;try{buf=await f.arrayBuffer()}catch(e){msg('読み込めませんでした（iCloudにあるファイルは先にダウンロードしてください）: '+f.name);continue}
  const declared=(f.type&&f.type.startsWith('audio/'))?f.type:(types[ext]||'audio/mpeg');
  const type=sniffType(new Uint8Array(buf),declared);
  const blob=new Blob([buf],{type});
  const id=Date.now().toString(36)+Math.random().toString(36).slice(2,7);
  const meta={id,title:f.name.replace(/\.[^.]+$/,''),duration:await getDuration(blob),size:blob.size,added:Date.now()};
  try{await tx('blobs','readwrite',s=>s.put({buf,type},id));await tx('meta','readwrite',s=>s.put(meta));songs.push(meta)}
  catch(e){console.warn(e);msg('保存できませんでした: '+f.name+'。もう一度試してください');ok=-1;break}
  buf=null;render();ok++;await yieldUi(true);
 }
 let t='';if(ok>=0){t=ok+'曲を取り込みました';if(ok)t+=IMPORT_HINT;msg(t)}
 usage();makeOrder();if(C)rebuild();else prefetch();
 // Keep the import result visible after the first song finishes loading.
 if(!hadSongs&&cur<0&&songs.length){if(await load(0,false)&&t)msg(t)}
}
async function del(i){
 const s=songs[i];if(!confirm('「'+s.title+'」を削除しますか？'))return;
 try{
  await tx('blobs','readwrite',st=>st.delete(s.id));await tx('meta','readwrite',st=>st.delete(s.id));parseCache.delete(s.id);
 }catch(e){console.warn(e);msg(isDbGone(e)?idbErrorMessage(e):'削除できませんでした。もう一度試してください');return}
 const wasCur=i===cur;songs.splice(i,1);
 if(wasCur){pausePlayback();audio.removeAttribute('src');try{audio.load()}catch(e){}
  dropC();if(urlNow){revokeUnused(urlNow);urlNow=null}
  if('mediaSession' in navigator){try{navigator.mediaSession.metadata=null}catch(e){}}
  cur=-1;lastAbs=0;if(songs.length)await load(Math.min(i,songs.length-1),false);else{idleScreen();ui()}}
 else if(i<cur)cur--;
 makeOrder();render();usage();dropPre();if(C&&!wasCur)rebuild();else prefetch();
}
function loadById(id,play=false,pos=0){
 const i=songs.findIndex(x=>x.id===id);
 if(i<0){msg('曲が見つかりません');return Promise.resolve(false)}
 return load(i,play,pos);
}
function delById(id){const i=songs.findIndex(x=>x.id===id);if(i>=0)return del(i)}
function render(){
 const ul=$('list');ul.innerHTML='';
 songs.forEach((s,i)=>{const li=document.createElement('li');if(i===cur)li.className='cur';
  const t=document.createElement('div');t.className='t';t.textContent=(i===cur?'▶ ':'')+s.title+'（'+fmt(s.duration)+'）';t.onclick=()=>loadById(s.id,true);
  const b=document.createElement('button');b.textContent='🗑';b.setAttribute('aria-label','削除');b.onclick=()=>delById(s.id);
  li.append(t,b);ul.append(li)});
}
async function usage(){
 let t='保存曲数: '+songs.length;
 if(navigator.storage&&navigator.storage.estimate){const e=await navigator.storage.estimate();
  t+=' ／ 使用量 '+(e.usage/1048576).toFixed(1)+'MB'+(e.quota?' / 上限目安 '+(e.quota/1048576/1024).toFixed(1)+'GB':'')}
 $('usage').textContent=t;
}
function makeOrder(){pendingOrder=null;order=songs.map((_,i)=>i);if(S.shuffle)for(let i=order.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[order[i],order[j]]=[order[j],order[i]]}}
// ===== 連続再生モード（MP3を1本につなげて再生） =====
// Blob slices keep the joined audio out of JS memory (only one song is parsed at
// a time). Xing's byte counter is 32-bit; reserve room for its metadata frame.
// iOS/Safari often maps MP3 currentTime from the first frame's bitrate or a
// coarse Xing TOC, so a long mixed-bitrate blob makes the displayed title drift
// from the audio (a 72-song library can show the wrong song by minutes). Prefer
// a correct title over a longer gapless run: only join identical CBR bitrates,
// and cap a chunk at 12 songs or 90 minutes. The next chunk starts when this
// one ends. 「形式と音量をそろえる」 re-encodes to 192kbps CBR so those songs join.
const CAP=0xFFFFFFFF-4096;
const MAX_JOIN_SONGS=12;
const MAX_JOIN_SEC=90*60;
let C=null,segIdx=-1,lastAbs=0,userPaused=true,building=0,pendingOrder=null,freshStart=0,freshGuard=0,mediaReady=false;
const BR1=[0,32,40,48,56,64,80,96,112,128,160,192,224,256,320],BR2=[0,8,16,24,32,40,48,56,64,80,96,112,128,144,160],SR=[44100,48000,32000];
function hdr(u,p){if(u[p]!==0xFF||(u[p+1]&0xE0)!==0xE0)return null;const v=(u[p+1]>>3)&3,l=(u[p+1]>>1)&3;if(v===1||l!==1)return null;
 const bi=u[p+2]>>4,si=(u[p+2]>>2)&3;if(bi===0||bi===15||si===3)return null;const v1=v===3;
 const sr=SR[si]/(v1?1:v===2?2:4),br=(v1?BR1:BR2)[bi]*1000,pad=(u[p+2]>>1)&1;
 return{len:Math.floor((v1?144:72)*br/sr)+pad,sr,smp:v1?1152:576,v,mode:u[p+3]>>6,br}}
function synchsafe(u,o){return ((u[o]&127)<<21)|((u[o+1]&127)<<14)|((u[o+2]&127)<<7)|(u[o+3]&127)}
function skipId3(u,p){
 p=p||0;
 while(p+10<=u.length&&u[p]===0x49&&u[p+1]===0x44&&u[p+2]===0x33){
  const n=10+synchsafe(u,p+6)+((u[p+5]&0x10)?10:0);
  if(n<10||p+n>u.length)break;
  p+=n;
 }
 return p;
}
function stripTail(u,end){
 end=end||u.length;
 for(;;){
  if(end>=128&&u[end-128]===0x54&&u[end-127]===0x41&&u[end-126]===0x47){end-=128;continue}
  if(end>=32&&u[end-32]===0x41&&u[end-31]===0x50&&u[end-30]===0x45&&u[end-29]===0x54&&
     u[end-28]===0x41&&u[end-27]===0x47&&u[end-26]===0x45&&u[end-25]===0x58){
   const size=u[end-20]|(u[end-19]<<8)|(u[end-18]<<16)|(u[end-17]<<24);
   const flags=u[end-12]|(u[end-11]<<8)|(u[end-10]<<16)|(u[end-9]<<24);
   let n=size;if(flags&0x20000000)n+=32;
   if(n>=32&&n<=end){end-=n;continue}
   break;
  }
  if(end>=15){
   const tag=String.fromCharCode(u[end-9],u[end-8],u[end-7],u[end-6],u[end-5],u[end-4],u[end-3],u[end-2],u[end-1]);
   if(tag==='LYRICS200'){
    const num=String.fromCharCode(u[end-15],u[end-14],u[end-13],u[end-12],u[end-11],u[end-10]);
    const sz=parseInt(num,10);
    if(sz>0&&end>=15+sz){end-=15+sz;continue}
   }
  }
  break;
 }
 return end;
}
function sameFmt(a,b){return a&&b&&a.sr===b.sr&&a.v===b.v&&(a.mode===3)===(b.mode===3)}
function tripleAt(u,q,end){
 const a=hdr(u,q);if(!a||q+a.len>end)return null;
 const b=hdr(u,q+a.len);if(!sameFmt(a,b)||q+a.len+b.len>end)return null;
 const c=hdr(u,q+a.len+b.len);if(!sameFmt(a,c)||q+a.len+b.len+c.len>end)return null;
 return a;
}
function isXingAt(u,q,end){
 const h=hdr(u,q);if(!h||q+h.len>end)return false;
 const at=q+4+(h.v===3?(h.mode===3?17:32):(h.mode===3?9:17));
 if(at+4<=end){
  const tag=String.fromCharCode(u[at],u[at+1],u[at+2],u[at+3]);
  if(/^(Xing|Info|VBRI)$/.test(tag))return true;
 }
 if(q+40<=end&&String.fromCharCode(u[q+36],u[q+37],u[q+38],u[q+39])==='VBRI')return true;
 return false;
}
function walkFrames(u,q,end){
 // Strip every leading Xing/Info/VBRI (encoder delay/padding tags). A mid-stream
 // tag ends the walk so we never leave a hole inside a contiguous slice.
 while(q+4<=end){
  const h=hdr(u,q);if(!h||q+h.len>end||!isXingAt(u,q,end))break;
  q+=h.len;
 }
 let header=null,start=q,last=q,br=0,cbr=true;
 const offsets=[];
 const h0=hdr(u,q);if(!h0||q+h0.len>end)return null;
 const sr=h0.sr,v=h0.v,mono=h0.mode===3;
 while(q+4<=end){
  const f=hdr(u,q);
  if(!f||q+f.len>end||f.sr!==sr||f.v!==v||(f.mode===3)!==mono)break;
  if(isXingAt(u,q,end))break;
  if(!header){header=u.slice(q,q+4);br=f.br}
  else if(f.br!==br)cbr=false;
  offsets.push(q-start);last=q+f.len;q+=f.len;
 }
 if(offsets.length<3||!header)return null;
 const audio=hdr(header,0),smp=audio?audio.smp:h0.smp;
 return{start,end:last,dur:offsets.length*smp/sr,sr,header,offsets,v:(header[1]>>3)&3,mode:header[3]>>6,smp,br,cbr,frames:offsets.length};
}
function parseMp3(u){
 // Skip every leading ID3v2 tag by its declared size (and the optional v2.4 footer),
 // strip trailing ID3v1 / APEv2 / Lyrics3, then prefer a real Xing/Info/VBRI origin.
 // Sync requires 3 consecutive frames so mid-tag 0xFFFB-like bytes are not treated as audio.
 let base=skipId3(u,0);
 const end=stripTail(u,u.length);
 while(base<end&&u[base]===0)base++;
 if(end-base<16)return null;
 const lim=Math.min(end-4,base+2097152);
 let best=null;
 const consider=r=>{if(r&&(!best||r.dur>best.dur))best=r};
 for(let p=base;p<lim;p++){
  if(!isXingAt(u,p,end))continue;
  const h=hdr(u,p);if(!h)continue;
  const after=p+h.len;
  if(!tripleAt(u,after,end))continue;
  consider(walkFrames(u,after,end));
 }
 if(best&&best.offsets.length>=3)return best;
 best=null;
 for(let p=base;p<lim;p++){
  if(!tripleAt(u,p,end))continue;
  consider(walkFrames(u,p,end));
  if(best&&best.dur>=1)break;
 }
 return best;
}
// One Xing frame describes the whole joined stream, not each original file.
// Counts exclude this metadata frame; byte count and TOC include its bytes.
function brIndex(v1,kbps){
 const tab=v1?BR1:BR2;let best=1,d=1e9;
 for(let i=1;i<=14;i++){const x=Math.abs(tab[i]-kbps);if(x<d){d=x;best=i}}
 return best;
}
function xingFrame(header,offsets,bytes,br){
 const frameHeader=header.slice();frameHeader[1]|=1; // No CRC in this new frame.
 const v=(frameHeader[1]>>3)&3,mono=(frameHeader[3]>>6)===3,v1=v===3;
 // Match the joined CBR bitrate so iOS/Safari's first-frame / average-bitrate
 // clock stays linear. Raise the index only if the frame would be too small for the TOC.
 const side=v1?(mono?17:32):(mono?9:17),need=4+side+4+4+4+4+100;
 let bi=brIndex(v1,br?br/1000:(v1?128:64));
 for(;;){
  frameHeader[2]=(frameHeader[2]&0x0D)|(bi<<4);
  const trial=hdr(frameHeader,0);
  if(trial&&trial.len>=need)break;
  if(bi>=14)break;
  bi++;
 }
 const h=hdr(frameHeader,0);if(!h)return null;const size=h.len,frame=new Uint8Array(size);
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
// Parsed frame tables per song (bytes never change for an id without a size change;
// conversion and delete also drop the entry). Avoids re-reading/parsing on every skip.
const parseCache=new Map();
function frameTableOk(r,s){
 if(!r||r.offsets.length<3||r.dur<=0)return false;
 if(s&&s.duration>2){
  const md=+s.duration;
  if(Math.abs(r.dur-md)>Math.max(1,md*0.12)+0.25)return false;
 }
 return true;
}
async function songFrames(id,b){
 const size=b instanceof Blob?b.size:b.buf.byteLength,c=parseCache.get(id);
 if(c&&c.size===size)return c.r;
 const u=new Uint8Array(b instanceof Blob?await b.arrayBuffer():b.buf);
 if(!isMp3(u)){parseCache.set(id,{size,r:null});return null}
 const r=parseMp3(u);
 const s=songs.find(x=>x.id===id);
 // A frame table that does not match the file's own (browser) duration is not
 // used for joining: the original file is played instead.
 if(r&&!frameTableOk(r,s)){parseCache.set(id,{size,r:null});return null}
 if(r){r.offsets=Int32Array.from(r.offsets);parseCache.set(id,{size,r})}
 else parseCache.set(id,{size,r:null});
 return r||null;
}
// The chunk always begins exactly at the requested song position (frame-accurate, byte 0),
// so a skip/seek never relies on the browser seeking inside a long joined stream: Safari
// and Chromium map time->byte via the 100-entry, 1/256-resolution Xing TOC or an average
// bitrate, which can land seconds (or a whole song) off in a multi-hour stream.
// With a native loop and a mid-song start, the skipped head of the first song is appended
// at the end so the loop continues seamlessly.
function joinCompat(r,prev){
 if(!r||!r.cbr||!r.br||r.frames<3)return false;
 if(!prev)return true;
 return r.sr===prev.sr&&r.v===prev.v&&(r.mode===3)===(prev.mode===3)&&r.br===prev.br;
}
async function buildChunk(i,pos=0,gen){
 const k=order.indexOf(i);if(k<0)return null;const parts=[],segs=[],offsets=[];let bytes=0,t=0,header=null,count=0,head=null,prev=null;
 const stale=()=>gen!=null&&building!==gen;
 for(let j=0;j<order.length;j++){
  if(stale())return null;
  if((j>0&&S.repeat==='one')||(k+j>=order.length&&S.repeat!=='all'))break;
  if(count>=MAX_JOIN_SONGS||(count&&t>=MAX_JOIN_SEC))break;
  const idx=order[(k+j)%order.length],s=songs[idx];
  const b=await tx('blobs','readonly',st=>st.get(s.id));if(!b)break;
  if(stale())return null;
  const type=b instanceof Blob?b.type:b.type;
  // MIME is only a hint: iOS may label m4a as audio/mpeg. songFrames requires real MP3 bytes.
  if(type&&type!=='audio/mpeg'&&type!=='audio/mp3'&&type!=='audio/x-mpeg'){if(j===0)return null;break}
  const r=await songFrames(s.id,b);
  let f=0,included=r?r.dur:0;
  if(r&&j===0&&pos>0){f=Math.min(r.offsets.length-1,Math.max(0,Math.floor(pos*r.sr/r.smp)));included=(r.offsets.length-f)*r.smp/r.sr}
  const span=r?r.end-r.start-(r.offsets[f]||0):0;
  if(!joinCompat(r,prev)||(count&&t+included>MAX_JOIN_SEC)||(bytes+span>CAP)){if(j===0)return null;break}
  prev=r;if(!header)header=r.header;
  const from=r.offsets[f];
  for(let n=f;n<r.offsets.length;n++)offsets.push(bytes+r.offsets[n]-from);
  parts.push(toBlob(b).slice(r.start+from,r.end));bytes+=r.end-r.start-from;segs.push({i:idx,id:s.id,start:t,dur:included,frames:r.offsets.length-f});t+=included;count++;
  if(f>0)head={i:idx,id:s.id,b,r,f,off:f*r.smp/r.sr};
  if(j)await tick();
 }
 if(stale()||segs.length<2)return null;
 const loop=S.repeat==='one'||(S.repeat==='all'&&count===order.length);
 if(loop&&head){
  const {r,f,off}=head;
  for(let n=0;n<f;n++)offsets.push(bytes+r.offsets[n]);
  parts.push(toBlob(head.b).slice(r.start,r.start+r.offsets[f]));bytes+=r.offsets[f];
  segs.push({i:head.i,id:head.id,start:t,dur:off,head:true,frames:f});t+=off;
 }
 if(stale()||segs.length<2||segs[0].id!==songs[i].id)return null;
 const xf=xingFrame(header,offsets,bytes,prev&&prev.br);if(!xf)return null;
 if(stale())return null;
 const blob=new Blob([xf,...parts],{type:'audio/mpeg'});
 const probe=parts[0]?parts[0].slice(0,Math.min(parts[0].size||0,400000)):null;
 return{url:URL.createObjectURL(blob),blob,segs,total:t,count,loop,probe,songId:segs[0].id}}
function mediaLive(el){
 // Ignore events from a src or <audio> we just tore down, or from a load not yet verified.
 el=el||audio;
 return mediaReady&&!loadJob&&!initializing&&urlNow&&el===audio&&el._gen===audioGen&&(el.src===urlNow||el.currentSrc===urlNow);
}
function showDiag(s,kind){
 const el=$('diag');if(!el)return;
 if(!s){el.textContent='';return}
 el.textContent='再生中: '+s.title+'（'+(kind==='join'?'連結':'単曲')+'）';
}
let nextJoin=null;
function dropNextJoin(){
 if(!nextJoin)return;
 if(nextJoin.ch&&nextJoin.ch.url&&nextJoin.ch.url!==urlNow)revokeUnused(nextJoin.ch.url);
 nextJoin=null;
}
function bindAudio(el){
 el.playsInline=true;el.preload='auto';
 const live=()=>mediaLive(el);
 el.addEventListener('loadedmetadata',()=>{
  if(el!==audio||el._gen!==audioGen)return;
  for(const url of retiredURLs)revokeUnused(url);
  if(live()){updateProgress();posState()}
 });
 el.addEventListener('durationchange',()=>{if(live())posState()});
 el.addEventListener('ratechange',()=>{if(live())posState()});
 el.addEventListener('playing',()=>{if(!live())return;clearTimeout(pauseTimer);playbackState('playing');startTicker()});
 el.addEventListener('seeked',()=>{if(!live())return;updateProgress();if(!el.paused)startTicker()});
 el.addEventListener('timeupdate',()=>{if(el===audio)updateProgress()});
 el.onplay=()=>{
  if(!live())return;
  clearTimeout(pauseTimer);$('play').textContent='⏸';$('play').setAttribute('aria-label','一時停止');
  playbackState('playing');startTicker();
 };
 el.onpause=()=>{
  stopTicker();clearTimeout(pauseTimer);
  if(!live())return;
  if(userPaused){markPaused();return}
  const g=building;
  pauseTimer=setTimeout(()=>{if(g!==building||!mediaLive())return;if(audio.paused)markPaused();else startTicker()},300);
 };
 el.onended=()=>{if(!live())return;stopTicker();clearTimeout(pauseTimer);onEnded();if(audio.paused)playbackState('paused')};
}
function swapAudio(){
 mediaReady=false;
 const old=audio,prevUrl=urlNow,prevC=C;
 ++audioGen;
 const el=document.createElement('audio');
 el.id='audio';el._gen=audioGen;
 bindAudio(el);
 if(old){
  try{old.pause();old.onplay=old.onpause=old.onended=null;old.removeAttribute('src');old.load()}catch(e){}
  if(old.parentNode)old.replaceWith(el);else document.body.append(el);
 }else{
  const slot=$('audio');
  if(slot&&slot!==el)slot.replaceWith(el);else document.body.append(el);
 }
 audio=el;
 urlNow=null;C=null;segIdx=-1;
 if(prevC&&prevC.url)revokeUnused(prevC.url);
 if(prevUrl&&prevUrl!==(prevC&&prevC.url))revokeUnused(prevUrl);
 dropPre();dropNextJoin();
 return el;
}
async function prepareNextJoin(){
 if(C||S.repeat==='one'||userPaused||loadJob)return;
 const n=nextIdx();if(n<0)return;
 const id=songs[n].id;
 if(nextJoin&&nextJoin.id===id&&(nextJoin.ch||nextJoin.pending))return;
 dropNextJoin();
 const gen=building,tok=nextJoin={i:n,id,ch:null,pending:true,gen};
 try{
  const ch=await buildChunk(n,0,gen);
  if(nextJoin!==tok||building!==gen){if(ch)revokeUnused(ch.url);return}
  tok.ch=ch;tok.pending=false;
 }catch(e){if(nextJoin===tok)nextJoin=null}
}
function findSeg(t){if(!C)return -1;let r=0;for(let n=0;n<C.segs.length;n++)if(t>=C.segs[n].start-0.05)r=n;return r}
function setSeg(n){
 if(!C||n<0||!C.segs[n])return;
 if(C.gen!=null&&C.gen!==building)return;
 if(!loadJob&&!mediaReady)return;
 const g=C.segs[n];segIdx=n;cur=songs.findIndex(x=>x.id===g.id);const s=cur>=0?songs[cur]:null;if(!s)return;
 $('title').textContent=s.title;$('seek').max=Math.floor(g.dur)||0;$('dur').textContent=fmt(g.dur);S.lastId=s.id;persistPos(true);render();session(s);posState();showDiag(s,'join')}
function seekAbs(t){freshStart=0;if(!audio.getAttribute('src'))return;try{audio.currentTime=t;lastAbs=t}catch(e){}}
function rel(){return C&&segIdx>=0?audio.currentTime-C.segs[segIdx].start:audio.currentTime}
function dropC(){if(C){const u=C.url;C=null;segIdx=-1;if(u!==urlNow)revokeUnused(u)}}
async function rebuild(){
 const i=curIdx();if(i<0)return;
 const r=loadJob?loadJob.pos:curPos();
 dropNextJoin();
 // Shuffle/repeat must not remount a joined stream at the current song.
 // Keep or reload this song as its original file; joining resumes when it ends.
 if(!C&&!loadJob){prefetch();return}
 await load(i,playingNow(),r,'seek');
}
let pre=null;
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
  if(pre!==tok||!blob)return;tok.url=URL.createObjectURL(await toPlayableBlob(blob))}catch(e){if(pre===tok)pre=null}
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
function resetMedia(){
 mediaReady=false;
 try{audio.removeAttribute('src');audio.load()}catch(e){}
}
// Attach a chunk that already starts at the play head. Never seek inside the long
// joined stream: on iOS, currentTime= can land a whole song off via the Xing TOC,
// and Safari may also keep the previous currentTime after a src change.
function waitChunkMeta(url,job){
 return new Promise((resolve,reject)=>{
  let settled=false,timer;
  const current=()=>!job||(loadJob===job&&job.generation===building);
  const clean=()=>{clearTimeout(timer);audio.removeEventListener('loadedmetadata',ok);audio.removeEventListener('error',fail);if(job&&job.cancel===cancel)job.cancel=null};
  const done=(v,err)=>{if(settled)return;settled=true;clean();if(err)reject(err);else resolve(v)};
  const cancel=()=>done(false);
  const fail=()=>done(false,new Error('音楽ファイルを読み込めませんでした'));
  const ok=()=>{
   if(!current()){done(false);return}
   if(audio.readyState<1||audio.currentSrc!==url)return;
   done(true);
  };
  audio.addEventListener('loadedmetadata',ok);
  audio.addEventListener('error',fail);
  if(job)job.cancel=cancel;
  timer=setTimeout(()=>done(false,new Error('読み込みに時間がかかっています。曲を選び直してください')),20000);
  if(audio.src!==url)audio.src=url;
  if(audio.readyState>=1&&audio.currentSrc===url)ok();
 });
}
async function attachChunk(ch,job){
 lastAbs=0;
 let u=ch.url;
 for(let attempt=0;attempt<2;attempt++){
  if(loadJob!==job)return {ok:false,url:u};
  resetMedia();
  let ok=false;
  try{ok=await waitChunkMeta(u,job)}catch(e){if(loadJob!==job)return {ok:false,url:u};if(attempt===1)throw e}
  if(loadJob!==job)return {ok:false,url:u};
  if(ok){
   // Give a kept-over currentTime (setTimeout after src change) a moment to appear.
   await tick();await tick();
   await new Promise(r=>setTimeout(r,32));
   if(loadJob!==job)return {ok:false,url:u};
   const t=audio.currentTime||0,g=ch.segs&&ch.segs[0];
   const lim=Math.min(0.2,Math.max(0.05,((g&&g.dur)||1)*0.25));
   if(t<=lim&&(!g||g.id===job.id)){lastAbs=t;return {ok:true,url:u}}
  }
  // Browser started mid-stream (kept the old currentTime). Remount a fresh blob; do not seek.
  if(attempt===0&&ch.blob){
   const nu=URL.createObjectURL(ch.blob);
   if(u&&u!==nu)revokeUnused(u);
   u=nu;ch.url=u;
  }
 }
 return {ok:false,url:u,misplaced:true};
}
function chunkLooksLikeSong(ch,job,pos){
 if(!ch||!ch.segs||!ch.segs[0]||!job)return false;
 const g=ch.segs[0];
 if(g.id!==job.id||(ch.songId&&ch.songId!==job.id))return false;
 const s=songs.find(x=>x.id===job.id);
 if(pos<0.5&&s&&s.duration>2&&(g.dur<s.duration*0.5||g.dur>s.duration*1.5+1))return false;
 return true;
}
async function decodeHeadHz(blob,take){
 const Ctx=window.OfflineAudioContext||window.webkitOfflineAudioContext;if(!Ctx||!blob)return null;
 try{
  const buf=await blob.slice(0,take||400000).arrayBuffer();
  const ac=new Ctx(1,Math.max(8192,44100),44100);
  const ab=await ac.decodeAudioData(buf);
  const ch=ab.getChannelData(0);if(!ch||ch.length<8000)return null;
  const sr=ab.sampleRate||44100;
  const start=Math.min(Math.floor(sr*0.05),Math.max(0,ch.length-5000));
  const n=Math.min(Math.floor(sr*0.1),ch.length-start);
  if(n<2000)return null;
  let z=0,last=0,e=0;
  for(let i=start;i<start+n;i++){const v=ch[i];e+=v*v;if(v>=0&&last<0)z++;last=v}
  if(e/n<1e-8)return null;
  return z*(sr/n);
 }catch(e){return null}
}
async function attachedMatchesSong(ch,job){
 // Decode the audio that is actually attached and compare it with a fresh slice
 // of the tapped song's own file (the probe taken from that file when the chunk
 // was built). A stale object URL or leftover joined blob will not match.
 try{
  if(!ch||!job||!ch.probe)return null;
  const srcBlob=(!audio.src||audio.src===ch.url||audio.currentSrc===ch.url)?ch.blob:null;
  const playing=srcBlob||(audio.src?await(await fetch(audio.src)).blob():null);
  if(!playing)return null;
  const a=await decodeHeadHz(playing,500000);
  const b=await decodeHeadHz(ch.probe,400000);
  if(a==null||b==null)return null;
  const d=Math.abs(a-b);
  if(d>150&&d>0.3*Math.max(a,b))return false;
  return true;
 }catch(e){return null}
}
function applySingle(i,url){
 dropC();cur=i;urlNow=url;ui();
 const song=songs[i];if(!song)return;
 $('title').textContent=song.title;$('seek').max=Math.floor(song.duration)||0;
 $('dur').textContent=fmt(song.duration);session(song);render();showDiag(song,'single');
}
async function singleUrl(id,i){
 // Only reuse a prefetched object URL if it is still this exact song (id AND slot).
 if(pre&&pre.url&&pre.id===id&&pre.i===i&&songs[i]&&songs[i].id===id){const u=pre.url;pre=null;return u}
 const blob=await tx('blobs','readonly',st=>st.get(id));
 if(!blob)throw new Error('保存した曲が見つかりません');
 return URL.createObjectURL(await toPlayableBlob(blob));
}
async function load(i,play=false,pos=0,how){
 if(i<0||i>=songs.length)return false;
 pos=Math.max(0,pos||0);
 // 'single' (list tap / default at offset 0): the song's own file, never a joined chunk, never a seek.
 // 'step': same, but keep the current <audio> (lock-screen next/prev may not get a new-element gesture).
 // 'seek': the song's own file at a position (seek bar / restore). Safe: the file is short.
 // 'join': unattended only — a joined chunk that starts at this (following) song at offset 0.
 if(!how)how=pos>0?'seek':'single';
 const previous=loadJob,my=++building;
 if(previous&&previous.cancel)previous.cancel();
 const job=loadJob={generation:my,id:songs[i].id,pos,how,cancel:null};
 mediaReady=false;freshStart=0;++playAttempt;
 cur=i;
 if(pendingOrder&&pendingOrder[0]===i){order=pendingOrder;pendingOrder=null}
 if(play){wantPlay=true;userPaused=false}
 stopTicker();clearTimeout(pauseTimer);
 $('title').textContent=songs[i].title;msg('読み込み中…');
 const swap=how==='single'&&pos===0;
 const reuse=how==='join'&&pos===0&&nextJoin&&nextJoin.id===job.id&&nextJoin.ch?nextJoin:null;
 if(reuse)nextJoin=null;else dropNextJoin();
 if(swap){
  lastAbs=0;
  swapAudio();
 }else{
  lastAbs=how==='seek'?pos:0;
  try{audio.pause()}catch(e){}
  const prevUrl=urlNow,prevC=C;
  resetMedia();
  urlNow=null;C=null;segIdx=-1;
  if(prevC&&prevC.url)revokeUnused(prevC.url);
  if(prevUrl&&prevUrl!==(prevC&&prevC.url))revokeUnused(prevUrl);
  dropPre();
 }
 if(window.__holdBuild)await window.__buildGate;
 if(loadJob!==job)return false;
 let ch=null;
 try{
  if(how==='join'&&pos===0){
   if(reuse&&reuse.ch)ch=reuse.ch;
   else{try{ch=await buildChunk(i,0,my)}catch(e){console.warn(e);ch=null}}
   if(loadJob!==job){if(ch)revokeUnused(ch.url);return false}
   if(ch&&!chunkLooksLikeSong(ch,job,0)){console.warn('chunk does not start as the next song');revokeUnused(ch.url);ch=null}
  }
  let url,ok=false,err=null;
  if(ch){
   dropPre();ch.gen=my;C=ch;url=ch.url;urlNow=url;ui();
   try{
    const att=await attachChunk(ch,job);
    url=att.url;urlNow=url;if(C)C.url=url;
    ok=!!att.ok;
    if(!ok&&att.misplaced)err=new Error('misplaced');
    if(ok){const like=await attachedMatchesSong(ch,job);if(like===false){ok=false;err=new Error('mismatch')}}
   }catch(e){err=e;ok=false}
  }else{
   url=await singleUrl(job.id,i);
   if(loadJob!==job){revokeUnused(url);return false}
   applySingle(i,url);
   try{
    if(pos>0)ok=await restorePosition(pos,job,url);
    else{
     // Original file at offset 0. If the element kept a leftover currentTime, seeking
     // this short file back to 0 is safe (unlike seeking a long joined stream).
     lastAbs=0;
     ok=await waitChunkMeta(url,job);
     if(ok){
      await tick();await tick();await new Promise(r=>setTimeout(r,32));
      if((audio.currentTime||0)>0.15){try{seekAbs(0)}catch(e){}await tick()}
      if((audio.currentTime||0)>0.25)ok=false;
     }
    }
   }catch(e){err=e;ok=false}
  }
  if(!ok&&ch&&loadJob===job){
   console.warn(err||'chunk load failed');
   revokeUnused(ch.url);ch=null;dropC();
   url=await singleUrl(job.id,i);
   if(loadJob!==job){revokeUnused(url);return false}
   urlNow=url;applySingle(i,url);
   try{ok=await waitChunkMeta(url,job);if(ok){await tick();if((audio.currentTime||0)>0.15){try{seekAbs(0)}catch(e){}await tick()}if((audio.currentTime||0)>0.25)ok=false}}catch(e){err=e;ok=false}
  }
  if(loadJob!==job)return false;
  if(ok&&ch){
   setSeg(0);
   const g=ch.segs[0],tm=audio.currentTime||0;
   const lim=Math.min(0.2,Math.max(0.05,((g&&g.dur)||1)*0.25));
   if(tm>lim||!g||g.id!==job.id||(songs[cur]&&songs[cur].id!==job.id)||findSeg(tm)!==0)ok=false;
  }
  if(!ok&&ch&&loadJob===job){
   console.warn('chunk did not start at the following song');
   revokeUnused(ch.url);ch=null;dropC();
   url=await singleUrl(job.id,i);
   if(loadJob!==job){revokeUnused(url);return false}
   urlNow=url;applySingle(i,url);
   try{ok=await waitChunkMeta(url,job);if(ok){await tick();if((audio.currentTime||0)>0.15){try{seekAbs(0)}catch(e){}await tick()}if((audio.currentTime||0)>0.25)ok=false}}catch(e){err=e;ok=false}
  }
  if(loadJob!==job)return false;
  if(!ok)throw err||new Error('音楽ファイルを読み込めませんでした');
  if(ch){ch.gen=my;C=ch;setSeg(0)}
  if(!songs[cur]||songs[cur].id!==job.id)throw new Error('曲を読み込めませんでした');
  lastAbs=audio.currentTime||0;freshStart=ch?performance.now():0;
  mediaReady=true;loadJob=null;
  S.lastId=songs[cur].id;S.lastPos=rel();lastSave=Date.now();save();updateProgress();
  showDiag(songs[cur],ch?'join':'single');
  msg(ch?'連続再生モード（'+ch.count+'曲をつなげて再生中）':'単曲再生（この曲のファイルを再生中）');
  if(!ch){const g=building;setTimeout(()=>{if(g===building&&!loadJob)prefetch()},0)}
  if(wantPlay&&!initializing){wantPlay=false;startAudio()}
  return true;
 }catch(e){
  if(loadJob===job){loadJob=null;wantPlay=false;userPaused=true;msg(playErrorMessage(e));playbackState('paused')}
  return false;
 }
}
// Song the user is on, counting a skip/seek that is still loading.
function curIdx(){if(loadJob){const n=songs.findIndex(x=>x.id===loadJob.id);if(n>=0)return n}return cur}
function curPos(){return loadJob?loadJob.pos:rel()}
const playingNow=()=>!audio.paused||wantPlay;
// Next/prev (buttons and lock screen) play that song's original file at offset 0.
// Joining of what follows starts when that file ends.
function step(d){
 if(!songs.length)return;
 const c=curIdx();
 if(c<0){msg('曲を選んでください');return}
 if(d>0){const save=cur;cur=c;const n=nextIdx();cur=save;if(n<0){if(!loadJob)audio.pause();return}load(n,true,0,'step');return}
 let k=order.indexOf(c)+d;
 if(k<0)k=S.repeat==='all'?order.length-1:0;
 load(order[k],true,0,'step');
}
// Seek inside the current song (seconds from its start). Never seek a long joined
// stream: switch to that song's original file at the requested position instead.
function seekSong(t){
 const c=curIdx();if(c<0||!audio.getAttribute('src')&&!loadJob)return;
 const g=C&&segIdx>=0&&!loadJob?C.segs[segIdx]:null;
 const d=g?g.dur:loadJob?(songs[c].duration||0):(Number.isFinite(audio.duration)?audio.duration:0);
 t=Math.max(0,d>0?Math.min(t,Math.max(0,d-0.05)):t);
 if(C||loadJob){load(c,playingNow(),t,'seek');return}
 seekAbs(t);posState();
}
function onEnded(){
 if(!mediaLive())return;
 // iOS (and a src change) can fire 'ended' on the new element at t≈0. Only advance when
 // the current file or joined stream has actually reached its end.
 const t=audio.currentTime||0,d=audio.duration;
 if(Number.isFinite(d)&&d>1&&t<Math.max(0.5,d-0.75))return;
 if(C){
  if(S.repeat==='one'){load(cur,true,0,'single');return}
  const last=C.segs[C.segs.length-1].i,k=order.indexOf(last)+1;
  if(k<order.length){load(order[k],true,0,'join');return}
  if(S.repeat==='all'&&order.length){load(order[0],true,0,'join');return}
  userPaused=true;return
 }
 if(S.repeat==='one'){seekAbs(0);startAudio();return}
 const n=nextIdx();if(n<0){audio.pause();userPaused=true;return}
 load(n,true,0,'join');
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
function resumeJoined(){
 // If a joined stream jumped back to 0, do not seek inside it. Play the current
 // song's original file at the saved position instead.
 const wrap=C&&C.loop&&lastAbs>C.total-3;
 if(!(C&&lastAbs>1&&audio.currentTime<1&&!wrap))return false;
 if(loadJob)return true;
 const n=findSeg(lastAbs),g=n>=0?C.segs[n]:null;
 const i=g?songs.findIndex(s=>s.id===g.id):cur;
 const pos=g?Math.max(0,lastAbs-g.start):0;
 if(i<0)return false;
 load(i,true,pos,'seek');
 return true;
}
let playAttempt=0;
function playbackState(state){if('mediaSession' in navigator)navigator.mediaSession.playbackState=state;posState()}
function playbackSession(){
 try{if(navigator.audioSession)navigator.audioSession.type='playback'}catch(e){}
}
function pausePlayback(){
 ++playAttempt;wantPlay=false;userPaused=true;audio.pause();stopTicker();clearTimeout(pauseTimer);
 // Do not clear src or metadata: Control Center must still be able to resume.
 if(!loadJob)persistPos(true);playbackState('paused');
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
 // iOS sometimes resets currentTime to 0. For a joined stream, do not seek back
 // inside the long blob (Xing TOC can land in a different song): rebuild a chunk
 // that starts at the saved position instead.
 if(resumeJoined())return Promise.resolve();
 // A single file is safe to seek. iOS sometimes resets currentTime to 0 on play().
 if(!C&&lastAbs>1&&(audio.currentTime||0)<1){try{seekAbs(lastAbs)}catch(e){}}
 const attempt=++playAttempt;userPaused=false;clearTimeout(pauseTimer);playbackSession();
 // This must be synchronous in the Media Session action's call stack. No DB awaits.
 let playing;try{playing=audio.play()}catch(e){playing=Promise.reject(e)}
 const i=cur,position=rel();
 const source=audio.src,generation=building;
 return Promise.resolve(playing).then(()=>{
  if(attempt!==playAttempt||generation!==building||source!==audio.src||loadJob||userPaused)return;
  if(songs[cur])session(songs[cur]);playbackState('playing');startTicker();
 }).catch(async e=>{
  if(attempt!==playAttempt||userPaused||source!==audio.src||generation!==building||loadJob)return;
  playbackState('paused');
  if(!recover||!songs[i]){msg('再生できませんでした。アプリを開いて再生ボタンを押してください。');return}
  // Only a failed direct resume may rebuild a discarded/invalid media resource.
  try{
   const restored=await load(i,false,Math.max(0,position||0),'seek');
   if(!restored||attempt!==playAttempt||generation!==building||userPaused||!audio.paused)return;
   await startAudio(false);
  }catch(error){msg('曲を読み直せませんでした。アプリで曲を選び直してください。')}
 });
}
if('mediaSession' in navigator){const m=navigator.mediaSession,h=(a,f)=>{try{m.setActionHandler(a,f)}catch(e){}};
 h('play',()=>startAudio());h('pause',pausePlayback);h('stop',pausePlayback);h('previoustrack',()=>step(-1));h('nexttrack',()=>step(1));
 h('seekto',e=>seekSong(e.seekTime));h('seekbackward',e=>seekSong(curPos()-(e.seekOffset||10)));h('seekforward',e=>seekSong(curPos()+(e.seekOffset||10)));}
function ui(){
 $('shuffle').classList.toggle('on',S.shuffle);
 $('repeat').classList.toggle('on',S.repeat!=='none');
 $('repeat').textContent={none:'🔁 リピートなし',all:'🔁 全曲リピート',one:'🔂 1曲リピート'}[S.repeat];
 document.body.classList.toggle('dark',S.dark);$('dark').textContent=S.dark?'☀️':'🌙';
 audio.loop=C?C.loop:S.repeat==='one';
}
$('play').onclick=()=>{if(loadJob||initializing){if(wantPlay)pausePlayback();else startAudio();return}if(audio.paused)startAudio();else pausePlayback()};
$('prev').onclick=()=>{if(curIdx()>=0&&curPos()>3)seekSong(0);else step(-1)};
$('next').onclick=()=>step(1);
$('shuffle').onclick=()=>{S.shuffle=!S.shuffle;makeOrder();save();ui();if(C||loadJob)rebuild();else prefetch()};
$('repeat').onclick=()=>{S.repeat={none:'all',all:'one',one:'none'}[S.repeat];save();ui();if(C||loadJob)rebuild();else prefetch()};
$('dark').onclick=()=>{S.dark=!S.dark;save();ui()};
$('convert').onclick=()=>{const ctx=audioCtx();if(ctx&&ctx.state==='suspended'){try{ctx.resume()}catch(e){}}convertLibrary()};
$('cancel').onclick=()=>{cancelWork=true;msg('中止しています…')};
$('file').onchange=e=>{const f=[...e.target.files];e.target.value='';if(f.length)addFiles(f)};
// Dragging only previews the time; releasing seeks once (one rebuild, not one per pixel).
let dragging=false;
$('seek').oninput=e=>{dragging=true;$('cur').textContent=fmt(+e.target.value)};
$('seek').onchange=e=>{dragging=false;seekSong(+e.target.value)};
let ticker=null,pauseTimer=null,lastSave=0;
function persistPos(force){
 if(cur<0||loadJob)return;
 const n=Date.now();
 if(!force&&n-lastSave<5000)return;
 lastSave=n;S.lastPos=rel();if(songs[cur])S.lastId=songs[cur].id;save();
}
function stopTicker(){if(ticker!==null){clearInterval(ticker);ticker=null}}
function startTicker(){
 if(audio.paused)return;
 if(ticker===null)ticker=setInterval(updateProgress,250);
 updateProgress();
}
function markPaused(){
 $('play').textContent='▶';$('play').setAttribute('aria-label','再生');
 if(!loadJob)persistPos(true);playbackState('paused');
}
function updateProgress(){
 if(!mediaLive())return;
 let t=audio.currentTime;if(!Number.isFinite(t)||t<0)return;
 if(C&&freshStart){
  const elapsed=(performance.now()-freshStart)/1000;
  if(elapsed<1&&lastAbs<1&&t>elapsed+0.4){
   freshStart=0;
   const g=C.segs[0],i=g?songs.findIndex(s=>s.id===g.id):cur;
   if(i>=0&&!loadJob&&freshGuard++<1)load(i,playingNow(),0,'single');
   return;
  }
  if(elapsed>=1){freshStart=0;freshGuard=0}
 }
 if(C){
  if(t<1&&lastAbs>5&&!(C.loop&&lastAbs>C.total-3)&&!audio.paused&&document.hidden){}else{
   const n=findSeg(t);if(n!==segIdx)setSeg(n);lastAbs=t}
  const g=C.segs[Math.max(0,Math.min(segIdx,C.segs.length-1))];if(!g)return;
  const r=Math.max(0,Math.min(g.dur,t-g.start));
  if(!dragging){$('cur').textContent=fmt(r);$('seek').value=Math.floor(r)}
 }else{
  if(!dragging){$('cur').textContent=fmt(t);$('seek').value=Math.floor(t)}lastAbs=t;
  if(Number.isFinite(audio.duration)&&audio.duration>0){$('seek').max=Math.floor(audio.duration);$('dur').textContent=fmt(audio.duration)}
  // Build the following joined stream while this original file is still playing,
  // so the src can be swapped on 'ended' even if iOS later throttles JS.
  const rem=audio.duration-t;
  if(!userPaused&&audio.duration>2&&rem>0&&rem<6)prepareNextJoin();
 }
 posState(); // Every tick; only persistence is throttled.
 persistPos(false)
}
document.addEventListener('visibilitychange',()=>{
 if(!document.hidden)wakeDB();
 if(loadJob||initializing)return;
 if(document.hidden){persistPos(true);return}
 if(cur<0||userPaused)return;
 if(resumeJoined())return;
 // Only seek a leftover currentTime on a single file, never inside a joined stream.
 if(!C&&audio.currentTime<1&&lastAbs>1){try{seekAbs(lastAbs)}catch(e){}}
 if(audio.paused)startAudio();else startTicker();
});

window.addEventListener('pagehide',()=>{persistPos(true);posState()});
window.addEventListener('pageshow',wakeDB);
window.addEventListener('focus',wakeDB);


const APP_VERSION='v25';
$('update').onclick=async()=>{
 if(!confirm('アプリを最新版に更新します（曲は消えません）。よろしいですか？'))return;
 msg('更新中…');
 try{if('serviceWorker' in navigator)for(const r of await navigator.serviceWorker.getRegistrations())await r.unregister();
  if(window.caches)for(const k of await caches.keys())if(k.startsWith('hatake-player-'))await caches.delete(k)}catch(e){}
 location.replace(location.pathname+'?u='+Date.now());
};
async function resetPlayback(){
 if(loadJob&&loadJob.cancel)loadJob.cancel();
 loadJob=null;wantPlay=false;userPaused=true;mediaReady=false;
 ++building;++playAttempt;freshStart=0;freshGuard=0;lastAbs=0;cur=-1;segIdx=-1;
 dropC();dropPre();dropNextJoin();
 parseCache.clear();
 S.lastId=null;S.lastPos=0;save();
 const prevUrl=urlNow;urlNow=null;
 swapAudio();
 if(prevUrl)revokeUnused(prevUrl);
 for(const u of [...retiredURLs]){retiredURLs.delete(u);try{URL.revokeObjectURL(u)}catch(e){}}
 dropDB();
 try{await openDB()}catch(e){console.warn(e)}
 idleScreen();render();ui();showDiag(null);
 msg('再生をリセットしました');
}
$('reset').onclick=()=>resetPlayback();
if(audio){audio._gen=audioGen;bindAudio(audio)}
(async()=>{
 ui();$('ver').textContent=APP_VERSION;msg('読み込み中…');
 try{
  await openDB();
  songs=(await tx('meta','readonly',s=>s.getAll())).sort((a,b)=>a.added-b.added);
  makeOrder();render();usage();
  if(navigator.storage&&navigator.storage.persist)navigator.storage.persist().then(p=>{window.__persisted=p});
  idleScreen();msg('');
 }catch(e){console.warn(e);idleScreen();msg('曲の一覧を読み込めませんでした。アプリを開き直してください')}
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
 window.__parseInfo=async function(id){
  const s=id?songs.find(x=>x.id===id):songs[cur];
  if(!s)return null;
  const b=await tx('blobs','readonly',st=>st.get(s.id));
  if(!b)return {id:s.id,title:s.title,metaDur:s.duration,error:'no blob'};
  const u=new Uint8Array(b instanceof Blob?await b.arrayBuffer():b.buf);
  const r=parseMp3(u);
  return {id:s.id,title:s.title,metaDur:s.duration,parseDur:r&&+r.dur.toFixed(3),start:r&&r.start,end:r&&r.end,frames:r&&r.offsets.length,sr:r&&r.sr,isMp3:isMp3(u),size:u.length};
 };
 window.__ready=true;
})();
