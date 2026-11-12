"""Accurate skip / rewind / seek in the joined MP3 stream (Chromium + ffmpeg).
Run: python3 tests/v16-browser.py

Each song k is a tone at BASE+100k Hz that steps up 5 Hz every 10 s, so decoding the first
half-second of the current chunk tells which song AND roughly which position it starts at.
Bitrates are mixed (CBR 320/128/96, VBR -q:a 7) plus two m4a converted by the app (192k lame)."""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
N,DUR,BASE=14,120,300
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)
def tone(k):return f"aevalsrc=0.5*sin(2*PI*({BASE+100*k}+5*floor(t/10))*t):s=44100:d={DUR}"
def freq(k,pos):return BASE+100*k+5*(int(pos)//10)
HELPERS="""() => {
  // Zero-crossing frequency of the first decoded audio of the current chunk (skips the Xing frame).
  window.chunkFreq=async (from=0.1,len=0.5)=>{const b=await (await fetch(C.url)).blob();
    const ac=new OfflineAudioContext(2,44100,44100);const ab=await ac.decodeAudioData(await b.slice(0,600000).arrayBuffer());
    const d=ab.getChannelData(0),s=Math.floor(from*ab.sampleRate),e=s+Math.floor(len*ab.sampleRate);let z=0;
    for(let i=s+1;i<e;i++)if((d[i-1]<0)!==(d[i]<0))z++;return z/2/len};
  window.settled=()=>new Promise(r=>{const iv=setInterval(()=>{if(!loadJob&&audio.readyState>=1){clearInterval(iv);r()}},20)});
  window.state=async ()=>{await settled();return {cur,rel:rel(),first:C.segs[0].i,start:C.segs[0].start,count:C.count,segs:C.segs.length,loop:C.loop,
    paused:audio.paused,f:await chunkFreq(),src:audio.src}};
}"""
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    t=pathlib.Path(tmp);names=[]
    rates=[['-b:a','320k'],['-q:a','7'],['-b:a','96k'],['-b:a','128k']]
    for k in range(N):
        out=t/f's{k:02d}.mp3';ff('-f','lavfi','-i',tone(k),'-ac','2','-ar','44100',*rates[k%4],'-y',str(out));names.append(out.name)
    m4a=[]
    for k in (N,N+1):
        out=t/f's{k:02d}.m4a';ff('-f','lavfi','-i',tone(k),'-ac','2','-ar','44100','-c:a','aac','-b:a','160k','-y',str(out));m4a.append(str(out))
    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    context.add_init_script("""window.actions={};const register=navigator.mediaSession.setActionHandler.bind(navigator.mediaSession);
      navigator.mediaSession.setActionHandler=(a,f)=>{actions[a]=f;register(a,f)};""")
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.route('**/fixture/*',lambda r:r.fulfill(path=str(t/r.request.url.split('/')[-1]),content_type='audio/mpeg'))
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v16'
    # Pre-existing library (as if imported earlier), then two m4a imported -> converted by the app.
    page.evaluate("""async (names)=>{for(const [k,n] of names.entries()){const buf=await (await fetch('fixture/'+n)).arrayBuffer();const id='s'+k;
      await tx('blobs','readwrite',st=>st.put({buf,type:'audio/mpeg'},id));const r=parseMp3(new Uint8Array(buf));
      await tx('meta','readwrite',st=>st.put({id,title:n,duration:r.dur,size:buf.byteLength,added:k,loudChecked:1,gainDb:0}));
      songs.push({id,title:n,duration:r.dur,size:buf.byteLength,added:k,loudChecked:1,gainDb:0})}render();makeOrder()}""",names)
    page.set_input_files('#file',m4a)
    page.wait_for_function(f"songs.length==={N+2} && document.getElementById('msg').textContent.includes('{N+2-N}曲を取り込みました')",timeout=120000)
    page.evaluate(HELPERS)
    page.evaluate("() => { load(0,false) }")
    s=page.evaluate('state()')
    assert s['count']==N+2 and s['first']==0 and s['paused'],s
    def check(s,k,pos,tol=0.15,label=''):
        exp=freq(k,pos)
        assert s['cur']==k and s['first']==k,(label,'wrong song',s,k)
        assert abs(s['rel']-pos)<=tol and abs(-s['start']-pos)<0.03,(label,'wrong position',s,pos)
        assert abs(s['f']-exp)<2.5,(label,'chunk does not start with the target audio',s,exp)
    # Next button x3 (songs with different bitrates), then lock-screen next/prev.
    for k in (1,2,3):
        page.click('#next');s=page.evaluate('state()');check(s,k,0,0.6,'next');assert not s['paused']
    page.evaluate('actions.nexttrack()');s=page.evaluate('state()');check(s,4,0,0.6,'nexttrack')
    page.evaluate('actions.previoustrack()');s=page.evaluate('state()');check(s,3,0,0.6,'previoustrack')
    # Audible check (real playback through Web Audio): the tone heard right after a skip.
    page.evaluate("""()=>{const ac=new AudioContext();const src=ac.createMediaElementSource(audio);const an=ac.createAnalyser();an.fftSize=16384;src.connect(an);an.connect(ac.destination);
      window.heard=()=>{const d=new Float32Array(an.frequencyBinCount);an.getFloatFrequencyData(d);let m=1;for(let i=1;i<d.length;i++)if(d[i]>d[m])m=i;return m*ac.sampleRate/an.fftSize}}""")
    page.click('#next');page.evaluate('state()');page.wait_for_timeout(600)
    h=page.evaluate('heard()');assert abs(h-freq(4,0))<8,('heard previous/other song after skip',h,freq(4,0))
    # Seek bar inside a deep song: release at 60s -> chunk starts at 60s of that song.
    page.evaluate("() => { load(12,true) }");page.evaluate('state()')
    page.evaluate("()=>{const e=document.getElementById('seek');e.value=60;e.dispatchEvent(new Event('input'));e.dispatchEvent(new Event('change'))}")
    s=page.evaluate('state()');check(s,12,60,0.6,'seek bar')
    # Rewind 10s / forward 10s / seekto from the lock screen.
    page.evaluate('actions.pause()')
    page.evaluate('actions.seekbackward({})');s=page.evaluate('state()');check(s,12,50,0.6,'seekbackward');assert s['paused'],'rewind while paused must stay paused'
    page.evaluate('actions.seekforward({seekOffset:25})');s=page.evaluate('state()');check(s,12,75,0.6,'seekforward')
    page.evaluate('actions.seekto({seekTime:101})');s=page.evaluate('state()');check(s,12,101,0.1,'seekto')
    # Rapid presses while loading count from the pending target, not the old song.
    page.evaluate("()=>{actions.nexttrack();actions.nexttrack()}");s=page.evaluate('state()');check(s,14,0,0.6,'double next')
    assert s['first']==14 and page.evaluate("songs[14].title")=='s14','converted m4a song'
    # Prev after >3s restarts the same song at its first frame; prev near the start goes back.
    page.evaluate('seekSong(30)');page.evaluate('state()')
    page.click('#prev');s=page.evaluate('state()');check(s,14,0,0.6,'prev restart')
    page.click('#prev');s=page.evaluate('state()');check(s,13,0,0.6,'prev to previous')
    # Unattended playback still crosses song boundaries inside one stream (no src change).
    page.evaluate(f'seekSong({DUR}-1.0)');s=page.evaluate('state()');src=s['src']
    page.evaluate('startAudio()')
    page.wait_for_function(f"cur===14 && rel()>0.5 && !audio.paused",timeout=10000)
    assert page.evaluate('audio.src')==src,'auto-advance changed src'
    # Repeat one with a mid-song start: head of the song appended so the native loop is seamless.
    page.click('#repeat');page.click('#repeat')   # none -> all -> one
    page.evaluate('state()');page.evaluate('seekSong(40)');s=page.evaluate('state()')
    check(s,14,40,0.6,'repeat-one seek');assert s['loop'] and s['segs']==2 and s['count']==1,s
    info=page.evaluate("""async ()=>{const b=await (await fetch(C.url)).blob();const ac=new OfflineAudioContext(2,44100,44100);
      const ab=await ac.decodeAudioData(await b.arrayBuffer());return {dur:ab.duration,total:C.total,head:C.segs[1]}}""")
    assert abs(info['total']-DUR)<0.1 and abs(info['dur']-DUR)<0.2 and info['head']['head'],info
    page.evaluate(f'seekAbs(C.total-0.6)');page.wait_for_function('audio.currentTime<1 && audio.currentTime>0.2 && !audio.paused',timeout=10000)
    assert page.evaluate('cur')==14 and abs(page.evaluate('rel()')-40.4)<1.0,page.evaluate('[cur,rel()]')
    # Repeat all: chunk covers every song, starts at the selected one, wraps through the head part.
    page.click('#repeat')   # one -> none
    page.click('#repeat')   # none -> all
    s=page.evaluate('state()');assert s['loop'] and s['count']==N+2 and s['segs']==N+3 and s['first']==14,s
    page.evaluate('actions.pause()')
    assert not errors,errors
    # Offline: cached v16 shell; skipping works.
    ctx2=browser.new_context();pg=ctx2.new_page();pg.on('pageerror',lambda e:errors.append(str(e)))
    pg.goto(url);pg.wait_for_function('window.__ready');pg.wait_for_function('navigator.serviceWorker.controller!==null',timeout=20000)
    ctx2.set_offline(True);pg.reload();pg.wait_for_function('window.__ready')
    assert pg.locator('#ver').inner_text()=='v16'
    small=[]
    for k in (0,1):
        out=t/f'small{k}.mp3';ff('-f','lavfi','-i',f"aevalsrc=0.5*sin(2*PI*{BASE+100*k}*t):s=44100:d=6",'-ac','2','-b:a','128k','-y',str(out));small.append(str(out))
    pg.set_input_files('#file',small);pg.wait_for_function('songs.length===2 && cur===0 && !loadJob && C && C.count===2',timeout=60000)
    pg.evaluate(HELPERS);pg.click('#next');s=pg.evaluate('state()')
    assert s['cur']==1 and s['first']==1 and abs(s['f']-(BASE+100))<2.5,s
    ctx2.close()
    assert not errors,errors
    print('PASS v16: next/prev buttons, lock-screen next/prev, seek bar, rewind/forward 10s, seekto all land on the target song frame (decoded chunk start + audible tone), rapid double-next, prev restart, auto-advance keeps one src, repeat-one/all seamless head wrap, converted m4a, offline')
    browser.close()
server.shutdown()
