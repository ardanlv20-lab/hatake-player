"""Boost quiet songs (loudness normalization baked into the MP3). Chromium + ffmpeg + numpy.
Run: python3 tests/v15-browser.py"""
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
# In-page analysis of decoded audio: overall RMS (dBFS), sample peak, clipped samples, and RMS in windows.
DECODE="""async ([bytes,wins]) => {
  const c=new AudioContext({sampleRate:44100});const ab=await c.decodeAudioData(new Uint8Array(bytes).buffer);await c.close();
  const L=ab.getChannelData(0),R=ab.getChannelData(ab.numberOfChannels-1);let pk=0,clip=0;
  const rms=(a,b)=>{let s=0;for(let i=a;i<b;i++)s+=L[i]*L[i]+R[i]*R[i];return 10*Math.log10(s/(2*(b-a)))};
  for(let i=0;i<L.length;i++){const m=Math.max(Math.abs(L[i]),Math.abs(R[i]));if(m>pk)pk=m;if(m>=0.999)clip++}
  const s=Math.floor(0.3*44100),e=L.length-Math.floor(0.3*44100);
  return {rms:rms(s,e),peak:20*Math.log10(pk),clip,wins:wins.map(([a,b])=>rms(Math.floor(a*44100),Math.floor(b*44100)))};
}"""
STORED="""async (title) => {const s=songs.find(x=>x.title===title);const b=await tx('blobs','readonly',st=>st.get(s.id));
  return {bytes:Array.from(new Uint8Array(b.buf)),meta:s,target:isTargetMp3(new Uint8Array(b.buf))}}"""
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    t=pathlib.Path(tmp)
    quiet=mp3(t,'quiet',sine(440,10**(-30/20),4))                 # peak -30 dBFS
    loud=mp3(t,'loud',sine(330,0.5,4))                             # peak -6 dBFS, ~-6 LUFS
    pk=sine(550,0.06,4.2);
    for k in range(4):                                             # 1ms transients near 0 dBFS at k+0.5 s
        a=int((k+0.5)*SR);pk[a:a+44]=0.95*np.sin(2*np.pi*1000*np.arange(44)/SR)
    peaky=mp3(t,'peaky',pk)
    quiet48=mp3(t,'quiet48',sine(660,10**(-28/20),3,48000),48000)
    raw={k:open(f,'rb').read() for k,f in dict(quiet=quiet,loud=loud,peaky=peaky,quiet48=quiet48).items()}
    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v16'
    assert page.locator('#convert').inner_text()=='形式と音量をそろえる'
    assert page.evaluate("getComputedStyle(document.getElementById('convert')).minHeight")=='64px'
    # Import path (empty library): quiet + peaky boosted, loud byte-identical, all joined, cur===0.
    page.set_input_files('#file',[loud,quiet,peaky])
    page.wait_for_function("songs.length===3 && cur===0 && !loadJob && C && C.segs.length===3",timeout=90000)
    m=page.evaluate(MSG)
    assert m.startswith('3曲を取り込みました') and '（2曲の音量を上げました）' in m,m
    assert page.evaluate('audio.paused')
    L=page.evaluate(STORED,'loud');Q=page.evaluate(STORED,'quiet');P=page.evaluate(STORED,'peaky')
    assert bytes(L['bytes'])==raw['loud'] and L['meta']['loudChecked']==1 and L['meta']['gainDb']==0,'loud song must stay byte-identical'
    print('measured LUFS: loud',L['meta']['lufs'],'quiet',Q['meta']['lufs'],'peaky',P['meta']['lufs'],'; gain quiet',Q['meta']['gainDb'],'peaky',P['meta']['gainDb'])
    assert Q['meta']['gainDb']==12 and Q['target'] and Q['meta']['size']==len(Q['bytes']),Q['meta']   # capped at +12 dB
    qa=page.evaluate(DECODE,[list(raw['quiet']),[]]);qb=page.evaluate(DECODE,[Q['bytes'],[]])
    rise=qb['rms']-qa['rms'];print('quiet: rms rise %.2f dB, peak %.2f dBFS'%(rise,qb['peak']))
    assert 11.3<rise<12.7 and qb['peak']<=-1.0 and qb['clip']==0,(qa,qb)
    # Quiet song with loud peaks: limiter allows at most 6 dB on the peaks; between peaks full gain.
    g=P['meta']['gainDb'];assert 4<g<6 and P['target'],P['meta']
    wins=[[k+0.05,k+0.35] for k in range(1,4)]
    pa=page.evaluate(DECODE,[list(raw['peaky']),wins]);pb=page.evaluate(DECODE,[P['bytes'],wins])
    rises=[b-a for a,b in zip(pa['wins'],pb['wins'])];print('peaky: gain',g,'window rises',['%.2f'%r for r in rises],'peak %.2f -> %.2f dBFS'%(pa['peak'],pb['peak']))
    assert all(abs(r-g)<0.6 for r in rises) and pb['peak']<=-1.0 and pb['clip']==0,(pa,pb,rises)
    # Pre-v15 library (no loudness meta): button analyzes target MP3s, converts + boosts 48k quiet one.
    page.evaluate("""async (items) => {
      for(const [id,title,bytes] of items){const buf=new Uint8Array(bytes).buffer;
        await tx('blobs','readwrite',st=>st.put({buf,type:'audio/mpeg'},id));
        const meta={id,title,duration:3,size:buf.byteLength,added:Date.now()};
        await tx('meta','readwrite',st=>st.put(meta));songs.push(meta)}
      render();makeOrder();
    }""",[['old-quiet','昔小さい',list(raw['quiet'])],['old-loud','昔大きい',list(raw['loud'])],['old-48','昔48小さい',list(raw['quiet48'])]])
    page.click('#convert')
    page.wait_for_function("!document.getElementById('convert').disabled && "+MSG+".includes('曲の音量を上げました')",timeout=90000)
    m=page.evaluate(MSG);assert m=='1曲を44.1kHzステレオのMP3にそろえました。2曲の音量を上げました',m
    OL=page.evaluate(STORED,'昔大きい');OQ=page.evaluate(STORED,'昔小さい');O48=page.evaluate(STORED,'昔48小さい')
    assert bytes(OL['bytes'])==raw['loud'] and OL['meta']['loudChecked']==1 and OL['meta']['gainDb']==0,OL['meta']
    assert OQ['meta']['gainDb']==12 and OQ['meta']['id']=='old-quiet' and OQ['meta']['size']==len(OQ['bytes']),OQ['meta']
    assert O48['target'] and O48['meta']['gainDb']>=10,O48['meta']
    assert page.evaluate("songs.map(s=>s.title)")==['loud','quiet','peaky','昔小さい','昔大きい','昔48小さい']
    assert page.evaluate('cur')==0 and page.evaluate('audio.paused'),'no surprise autoplay'
    page.wait_for_function("!loadJob && C && C.segs.length===songs.length && songs.length===6",timeout=20000)
    # Second press: nothing to do, nothing boosted twice.
    sizes=page.evaluate("songs.map(s=>s.size)")
    page.click('#convert')
    page.wait_for_function(MSG+"==='そろえる必要がある曲はありません'",timeout=20000)
    assert page.evaluate("songs.map(s=>s.size)")==sizes
    # Even if the meta flag were lost, a boosted song is not raised again by much (re-measure only).
    re=page.evaluate("""async () => {const b=await tx('blobs','readonly',st=>st.get('old-quiet'));
      const r=await processAudio(b.buf,{format:false,loud:true});return {lufs:r.lufs}}""")
    print('re-measured boosted quiet song LUFS',re['lufs'])
    # A target MP3 that only needs analysis: analyzed, unchanged.
    page.evaluate("""async (bytes) => {const buf=new Uint8Array(bytes).buffer,id='old-loud2';
      await tx('blobs','readwrite',st=>st.put({buf,type:'audio/mpeg'},id));
      const meta={id,title:'昔大きい2',duration:4,size:buf.byteLength,added:Date.now()};
      await tx('meta','readwrite',st=>st.put(meta));songs.push(meta);render();makeOrder()}""",list(raw['loud']))
    page.click('#convert')
    page.wait_for_function(MSG+"==='音量を調べました。変える必要がある曲はありませんでした'",timeout=30000)
    assert bytes(page.evaluate(STORED,'昔大きい2')['bytes'])==raw['loud']
    page.evaluate("() => { load(0,false) }")  # injected song was added behind the app's back; rejoin
    page.wait_for_function("!loadJob && C && C.segs.length===7",timeout=20000)
    # Offline: import boosts using the SW-cached encoder.
    ctx2=browser.new_context();pg=ctx2.new_page();pg.on('pageerror',lambda e:errors.append(str(e)))
    pg.goto(url);pg.wait_for_function('window.__ready');pg.wait_for_function('navigator.serviceWorker.controller!==null',timeout=20000)
    ctx2.set_offline(True);pg.reload();pg.wait_for_function('window.__ready')
    assert pg.locator('#ver').inner_text()=='v16'
    pg.set_input_files('#file',[quiet,loud])
    pg.wait_for_function("songs.length===2 && cur===0 && !loadJob && C && C.segs.length===2",timeout=60000)
    assert '（1曲の音量を上げました）' in pg.evaluate(MSG),pg.evaluate(MSG)
    ctx2.close()
    assert not errors,errors
    print('PASS v15: quiet song boosted (+12 dB cap, peak <= -1 dBFS, no clipping), loud song byte-identical, peaky song soft-limited, button converts+boosts pre-v15 songs once (same id/order, no autoplay), second press no-op, analysis-only message, all songs one chunk, import boost, offline')
    browser.close()
server.shutdown()
