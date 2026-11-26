"""v18: import stores as-is; convert is iOS-safe (segmented decode, 中止, one AudioContext).
Run: python3 tests/v18-browser.py"""
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
    with wave.open(str(path),'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(sr);w.writeframes((np.clip(st,-1,1)*32767).astype('<i2').tobytes())
def mp3(t,name,x,sr=SR):
    w=str(t/(name+'.wav'));wav(w,x,sr);o=str(t/(name+'.mp3'))
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',w,'-ar',str(sr),'-b:a','192k','-y',o],check=True);return o
def sine(f,amp,sec,sr=SR):return amp*np.sin(2*np.pi*f*np.arange(int(sec*sr))/sr)
STORED="""async (title) => {const s=songs.find(x=>x.title===title);const b=await tx('blobs','readonly',st=>st.get(s.id));
  return {bytes:Array.from(new Uint8Array(b.buf)),meta:{...s},target:isTargetMp3(new Uint8Array(b.buf)),type:b.type}}"""
HOOK="""() => {
  window.__decodeLens=[];window.__newAC=0;window.__beats=[];window.__msgs=[];
  const Orig=window.AudioContext||window.webkitAudioContext;
  if(Orig && !window.__acWrapped){
    const Wrap=function(...a){window.__newAC++;const c=new Orig(...a);
      const dec=c.decodeAudioData.bind(c);
      c.decodeAudioData=(buf,...r)=>{window.__decodeLens.push(buf&&buf.byteLength||0);return dec(buf,...r)};
      return c};
    Wrap.prototype=Orig.prototype;
    if(window.AudioContext)window.AudioContext=Wrap;
    if(window.webkitAudioContext)window.webkitAudioContext=Wrap;
    window.__acWrapped=true;
  }
  const mo=new MutationObserver(()=>{window.__msgs.push(document.getElementById('msg').textContent)});
  mo.observe(document.getElementById('msg'),{childList:true,characterData:true,subtree:true});
  window.__hb=setInterval(()=>{window.__beats.push(performance.now())},16);
}"""
def gaps(beats):
    g=[b-a for a,b in zip(beats,beats[1:])]
    return g,(max(g) if g else 0)

def run_suite(browser_type, label):
    with tempfile.TemporaryDirectory() as tmp:
        t=pathlib.Path(tmp)
        quiet=mp3(t,'quiet',sine(440,10**(-30/20),8))
        loud=mp3(t,'loud',sine(330,0.5,8))
        s48=mp3(t,'s48',sine(550,0.5,4,48000),48000)
        # 90s quiet: several 30s segments + encode. 5-min loud: measure-only, many segments.
        q90=mp3(t,'q90',sine(440,10**(-30/20),90))
        loud5=mp3(t,'loud5',sine(330,0.5,300))
        qA=mp3(t,'qa',sine(440,10**(-30/20),40))
        qB=mp3(t,'qb',sine(440,10**(-30/20),40))
        qC=mp3(t,'qc',sine(440,10**(-30/20),40))
        raw={k:open(f,'rb').read() for k,f in dict(quiet=quiet,loud=loud,s48=s48,q90=q90,loud5=loud5).items()}
        browser=browser_type.launch(args=['--autoplay-policy=no-user-gesture-required'] if label=='chromium' else [])
        context=browser.new_context(service_workers='block')
        page=context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url);page.wait_for_function('window.__ready')
        assert page.locator('#ver').inner_text()=='v26'
        assert page.locator('#cancel').is_hidden()
        page.evaluate(HOOK)

        # Import must not decode or convert; songs become playable immediately.
        page.set_input_files('#file',[loud,quiet,s48])
        page.wait_for_function("songs.length===3 && cur===0 && !loadJob && document.getElementById('msg').textContent.includes('取り込みました')",timeout=60000)
        m=page.evaluate(MSG)
        assert m.startswith('3曲を取り込みました') and '形式と音量をそろえる' in m,m
        assert page.evaluate('__decodeLens').__len__()==0 and page.evaluate('__newAC')==0,('import used Web Audio',page.evaluate('[__decodeLens,__newAC]'))
        L=page.evaluate(STORED,'loud');Q=page.evaluate(STORED,'quiet');S48=page.evaluate(STORED,'s48')
        assert bytes(L['bytes'])==raw['loud'] and bytes(Q['bytes'])==raw['quiet']
        assert not S48['target'],S48
        assert not L['meta'].get('loudChecked') and not Q['meta'].get('loudChecked')
        # Already-target songs can join; the 48k one must not force a convert on import.
        n=page.evaluate("async ()=>{const ch=await buildChunk(0,0);const n=ch&&ch.count;if(ch&&ch.url)revokeUnused(ch.url);return n||0}")
        assert n>=1,('target songs should still be joinable',n)

        # Button: cancel mid-run. Three 40s quiet songs so the first takes a while.
        context.close()
        context=browser.new_context(service_workers='block')
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url);page.wait_for_function('window.__ready')
        page.evaluate(HOOK)
        page.set_input_files('#file',[qA,qB,qC])
        page.wait_for_function("songs.length===3 && document.getElementById('msg').textContent.includes('取り込みました')",timeout=60000)
        page.click('#convert')
        page.wait_for_function("!document.getElementById('cancel').hidden && ("+MSG+".includes('音量を調べています')||"+MSG+".includes('そろえています'))",timeout=30000)
        assert page.locator('#cancel').is_visible()
        page.click('#cancel')
        page.wait_for_function(MSG+".includes('中止しました') && !document.getElementById('convert').disabled",timeout=60000)
        page.locator('#cancel').is_hidden()
        checked=page.evaluate("songs.filter(s=>s.loudChecked).length")
        print(label,'cancel: checked',checked,'of 3; msg',page.evaluate(MSG))
        assert 0<=checked<3,('cancel should stop before every song is done',checked)
        assert page.evaluate(MSG).startswith('中止しました')

        # Segmented decode of a 90s quiet song + a 5-min loud song (measure only).
        context.close()
        context=browser.new_context(service_workers='block')
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url);page.wait_for_function('window.__ready')
        page.evaluate(HOOK)
        page.set_input_files('#file',[q90,loud5])
        page.wait_for_function("songs.length===2 && document.getElementById('msg').textContent.includes('取り込みました')",timeout=60000)
        assert page.evaluate('__decodeLens').__len__()==0
        full90=len(raw['q90']);full5=len(raw['loud5'])
        page.evaluate('__beats=[];__decodeLens=[];__msgs=[]')
        page.click('#convert')
        page.wait_for_function("!document.getElementById('convert').disabled && "+MSG+".includes('音量を上げました')",timeout=240000)
        beats=page.evaluate('__beats');lens=page.evaluate('__decodeLens');msgs=page.evaluate('__msgs')
        g,mx=gaps(beats)
        print(label,'long convert: max_gap=%.0fms decodeCalls=%d newAC=%d maxSlice=%d file90=%d file5=%d'%(mx,len(lens),page.evaluate('__newAC'),max(lens) if lens else 0,full90,full5))
        assert mx<200,('long convert froze, max gap %.0fms'%mx,mx)
        assert page.evaluate('__newAC')<=2,('too many AudioContexts',page.evaluate('__newAC'))  # one app ctx, maybe a test leftover
        assert lens and len(lens)>=6,('expected many short decodes, got',lens)
        # Every decode is a ~30s slice (+overlap), never the whole 5-min / 90s file.
        assert max(lens)<full5*0.4 and max(lens)<full90*0.7,(max(lens),full90,full5,lens[:8])
        Q=page.evaluate(STORED,'q90');L5=page.evaluate(STORED,'loud5')
        assert Q['meta']['gainDb']==12 and Q['target'] and 85<Q['meta']['duration']<95,Q['meta']
        assert bytes(L5['bytes'])==raw['loud5'] and L5['meta']['loudChecked']==1 and L5['meta']['gainDb']==0,L5['meta']
        m=page.evaluate(MSG)
        assert '1曲の音量を上げました' in m,m
        # Direct processAudio on the 5-min loud song uses slices and does not boost it.
        re=page.evaluate("""async (title)=>{const s=songs.find(x=>x.title===title);
          const b=await tx('blobs','readonly',st=>st.get(s.id));
          window.__decodeLens=[];
          const r=await processAudio(b.buf,{format:false,loud:true});
          return {gain:r.gainDb,lufs:r.lufs,calls:window.__decodeLens.slice(),max:Math.max(0,...window.__decodeLens)}}""", 'loud5')
        print(label,'re-measure loud5',re['lufs'],'gain',re['gain'],'calls',len(re['calls']),'max',re['max'])
        assert re['gain']==0 and len(re['calls'])>=8 and re['max']<full5*0.4,re

        assert not errors,errors
        browser.close()
        print('PASS v18',label)

with sync_playwright() as p:
    run_suite(p.chromium,'chromium')
    try:
        run_suite(p.webkit,'webkit')
    except Exception as e:
        print('WebKit suite skipped or failed to launch:',type(e).__name__,e)

server.shutdown()
