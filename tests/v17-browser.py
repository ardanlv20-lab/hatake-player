"""Keep the UI responsive during loudness check / import (v17). Chromium + ffmpeg + numpy.
Run: python3 tests/v17-browser.py"""
import functools, http.server, pathlib, subprocess, tempfile, threading, wave
import numpy as np
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
MSG="document.getElementById('msg').textContent"
SR=44100
def wav(path,x,sr=SR):
    st=np.stack([x,x],1) if x.ndim==1 else x
    with wave.open(path,'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(sr);w.writeframes((np.clip(st,-1,1)*32767).astype('<i2').tobytes())
def mp3(t,name,x,sr=SR):
    w=str(t/(name+'.wav'));wav(w,x,sr);o=str(t/(name+'.mp3'))
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',w,'-ar',str(sr),'-b:a','192k','-y',o],check=True);return o
def sine(f,amp,sec,sr=SR):return amp*np.sin(2*np.pi*f*np.arange(int(sec*sr))/sr)
STORED="""async (title) => {const s=songs.find(x=>x.title===title);const b=await tx('blobs','readonly',st=>st.get(s.id));
  return {bytes:Array.from(new Uint8Array(b.buf)),meta:s,target:isTargetMp3(new Uint8Array(b.buf))}}"""
HEART="""() => {
  window.__beats=[];window.__msgs=[];
  const mo=new MutationObserver(()=>{window.__msgs.push(document.getElementById('msg').textContent)});
  mo.observe(document.getElementById('msg'),{childList:true,characterData:true,subtree:true});
  window.__hb=setInterval(()=>{window.__beats.push(performance.now())},16);
}"""
def gaps(beats):
    g=[b-a for a,b in zip(beats,beats[1:])]
    return g, (max(g) if g else 0)

with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    t=pathlib.Path(tmp)
    quiet=mp3(t,'quiet',sine(440,10**(-30/20),25))
    loud=mp3(t,'loud',sine(330,0.5,25))
    s48=mp3(t,'s48',sine(550,0.5,8,48000),48000)
    raw={k:open(f,'rb').read() for k,f in dict(quiet=quiet,loud=loud,s48=s48).items()}
    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v17'
    assert page.locator('#convert').inner_text()=='形式と音量をそろえる'
    page.evaluate(HEART)
    page.set_input_files('#file',[loud,quiet,s48])
    page.wait_for_function("songs.length===3 && cur===0 && !loadJob && document.getElementById('msg').textContent.includes('取り込みました')",timeout=120000)
    beats=page.evaluate('__beats');msgs=page.evaluate('__msgs')
    g,mx=gaps(beats)
    over=sum(1 for x in g if x>200)
    print('import heartbeat: n=%d max_gap=%.0fms gaps>200ms=%d msgs=%d'%(len(beats),mx,over,len(msgs)))
    for m in msgs:
        if '調べています' in m or '変換しています' in m or '上げています' in m:
            print('  progress:',m)
    assert mx<200,('UI froze during import, max heartbeat gap %.0fms'%mx,mx)
    joined=' | '.join(msgs)
    assert '音量を調べています… 1/3曲' in joined or '音量を調べています… 1/3曲 loud.mp3' in joined,msgs
    assert any('音量を調べています…' in m and '/3曲' in m and '%' in m for m in msgs),msgs
    assert any('変換しています…' in m and '/3曲' in m for m in msgs),msgs
    assert any('音量を上げています…' in m and '/3曲' in m for m in msgs),msgs
    m=page.evaluate(MSG)
    assert m.startswith('3曲を取り込みました') and '（うち1曲を44.1kHzステレオのMP3に変換しました）' in m and '（1曲の音量を上げました）' in m,m
    L=page.evaluate(STORED,'loud');Q=page.evaluate(STORED,'quiet');S48=page.evaluate(STORED,'s48')
    assert bytes(L['bytes'])==raw['loud'] and L['meta']['loudChecked']==1 and L['meta']['gainDb']==0,L['meta']
    assert Q['meta']['gainDb']==12 and Q['meta']['loudChecked']==1 and Q['target'],Q['meta']
    assert S48['target'] and S48['meta']['loudChecked']==1 and S48['meta']['gainDb']==0,S48['meta']

    # Button path stays responsive and still skips already-checked songs.
    page.evaluate('__beats=[];__msgs=[]')
    page.click('#convert')
    page.wait_for_function(MSG+"==='そろえる必要がある曲はありません'",timeout=20000)
    beats=page.evaluate('__beats');g,mx=gaps(beats)
    print('second-press heartbeat max_gap=%.0fms'%mx)
    assert mx<200,mx

    # Hung decodeAudioData: time out instead of freezing, then restore the original.
    hung=page.evaluate("""async (bytes) => {
      const keep=DECODE_TIMEOUT_MS; DECODE_TIMEOUT_MS=600;
      const ctx=audioCtx(); const orig=ctx.decodeAudioData.bind(ctx);
      ctx.decodeAudioData=()=>new Promise(()=>{});
      const t0=performance.now(); let err=null;
      try{await processAudio(new Uint8Array(bytes).buffer,{format:false,loud:true})}catch(e){err=String(e&&e.message||e)}
      ctx.decodeAudioData=orig; DECODE_TIMEOUT_MS=keep;
      return {err,ms:performance.now()-t0};
    }""",list(raw['loud']))
    print('decode timeout',hung)
    assert hung['err'] and '時間切れ' in hung['err'] and 400<=hung['ms']<=4000,hung

    # Hung OfflineAudioContext: fall back to linear interpolation and still emit a target MP3.
    fb=page.evaluate("""async (bytes) => {
      const keepA=actx,keepT=RESAMPLE_TIMEOUT_MS; RESAMPLE_TIMEOUT_MS=600;
      actx=new AudioContext({sampleRate:48000});
      const O=window.OfflineAudioContext, W=window.webkitOfflineAudioContext;
      function wrap(Base){return function(n,l,r){const oc=new Base(n,l,r);oc.startRendering=()=>new Promise(()=>{});return oc}}
      window.OfflineAudioContext=wrap(O); if(W) window.webkitOfflineAudioContext=wrap(W);
      const t0=performance.now(); let out=null,err=null;
      try{out=new Uint8Array(await toMp3(new Uint8Array(bytes).buffer));}catch(e){err=String(e&&e.message||e)}
      window.OfflineAudioContext=O; if(W) window.webkitOfflineAudioContext=W;
      await actx.close(); actx=keepA; RESAMPLE_TIMEOUT_MS=keepT;
      const r=out&&parseMp3(out);
      return {err,ms:performance.now()-t0,sr:r&&r.sr,mode:r&&r.mode,dur:r&&r.dur,len:out&&out.length};
    }""",list(raw['s48']))
    print('resample fallback',fb)
    assert not fb['err'] and fb['sr']==44100 and fb['mode']!=3 and 7.5<fb['dur']<8.6,fb
    assert 400<=fb['ms'],fb  # waited for the hung OfflineAudioContext before falling back

    # CPU throttle (phone-like): heartbeat must still stay under ~200ms on a 12s quiet song.
    q12=mp3(t,'q12',sine(440,10**(-30/20),12))
    ctx2=browser.new_context(service_workers='block')
    pg=ctx2.new_page();pg.on('pageerror',lambda e:errors.append(str(e)))
    pg.goto(url);pg.wait_for_function('window.__ready')
    cdp=pg.context.new_cdp_session(pg)
    cdp.send('Emulation.setCPUThrottlingRate',{'rate':4})
    pg.evaluate(HEART)
    pg.set_input_files('#file',[q12])
    pg.wait_for_function("songs.length===1 && document.getElementById('msg').textContent.includes('取り込みました')",timeout=120000)
    beats=pg.evaluate('__beats');g,mx=gaps(beats)
    print('cpu-x4 import heartbeat: n=%d max_gap=%.0fms gaps>200ms=%d'%(len(beats),mx,sum(1 for x in g if x>200)))
    assert mx<200,('throttled UI froze, max heartbeat gap %.0fms'%mx,mx)
    assert '（1曲の音量を上げました）' in pg.evaluate(MSG),pg.evaluate(MSG)
    ctx2.close()

    assert not errors,errors
    print('PASS v17: import stays responsive (heartbeat gap < 200ms, also under 4x CPU throttle), per-song progress (音量を調べています… N/10曲), quiet song boosted once, loud song byte-identical, hung decode times out, hung OfflineAudioContext falls back to linear resample, second convert is a no-op')
    browser.close()
server.shutdown()
