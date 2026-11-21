"""v22: a second tap right after playback starts must play the newly tapped
song, even if iOS fires 'ended' while the previous src is torn down, and
even with a large library / slow IDB / CPU throttle. Chromium + ffmpeg.
Run: python3 tests/v22-browser.py"""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)

# Distinct tones for the first 8 tracks (400, 600, ... 1800 Hz). The rest are fillers.
TONES=[400+i*200 for i in range(8)]
HZ="""async () => {
  if(!audio.getAttribute('src')&&!audio.currentSrc)return {err:'no src',cur,title:document.getElementById('title').textContent};
  const b=await (await fetch(audio.src)).blob();
  const ac=new OfflineAudioContext(2,44100,44100);
  const ab=await ac.decodeAudioData(await b.slice(0,800000).arrayBuffer());
  const ch=ab.getChannelData(0);
  const start=2000; let z=0,last=0;
  for(let i=start;i<start+4410;i++){const v=ch[i];if(v>=0&&last<0)z++;last=v}
  const t=audio.currentTime||0;
  const n=(C&&typeof findSeg==='function')?findSeg(t):-1;
  const g=(C&&n>=0)?C.segs[n]:null;
  return {hz:+(z*(44100/4410)).toFixed(1), t:+t.toFixed(3), cur,
    title:document.getElementById('title').textContent, id:songs[cur]&&songs[cur].id,
    firstI:C&&C.segs[0]&&C.segs[0].i, segI:g&&g.i, ready:!!mediaReady, job:!!loadJob};
}"""

def near(hz, expect, tol=90):
    return abs(hz-expect)<=tol

with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    td=pathlib.Path(tmp)
    blobs=[]
    for i,f0 in enumerate(TONES):
        fn=str(td/f's{i}.mp3')
        ff('-f','lavfi','-i',f'sine=frequency={f0}:duration=3','-ar','44100','-ac','2','-b:a','96k','-y',fn)
        blobs.append(open(fn,'rb').read())
    filler=blobs[-1]
    N=72

    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    page=context.new_page()
    session=context.new_cdp_session(page)
    session.send('Emulation.setCPUThrottlingRate',{'rate':4})
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v24'

    page.evaluate("""async ([firsts,filler,n])=>{
      S.shuffle=false;S.repeat='none';save();ui();
      window.__txDelay=20;
      for(let i=0;i<n;i++){
        const bytes=i<firsts.length?firsts[i]:filler;
        const buf=new Uint8Array(bytes).buffer,id='id'+i;
        await tx('blobs','readwrite',st=>st.put({buf,type:'audio/mpeg'},id));
        const meta={id,title:'song-'+i,duration:3,size:buf.byteLength,added:Date.now()+i};
        await tx('meta','readwrite',st=>st.put(meta));songs.push(meta);
      }
      render();makeOrder();
    }""",[[list(b) for b in blobs],list(filler),N])
    assert page.evaluate('songs.length')==N

    def wait(cur=None,timeout=90000):
        if cur is None: page.wait_for_function('!loadJob && mediaReady',timeout=timeout)
        else: page.wait_for_function(f'!loadJob && mediaReady && cur==={cur}',timeout=timeout)

    def check(i,label):
        page.wait_for_timeout(120)
        s=page.evaluate(HZ)
        assert s.get('err') is None,(label,s)
        assert s['cur']==i and s['title']=='song-'+str(i) and s['id']=='id'+str(i),(label,'identity',s)
        if s.get('firstI') is not None:
            assert s['firstI']==i and s['segI']==i,(label,'segment',s)
        assert s['t']<1.2,(label,'must start at the beginning',s)
        assert near(s['hz'],TONES[i]),(label,'audible',s,TONES[i])
        return s

    def load_wait(i):
        page.evaluate('i=>load(i,true)',i)
        wait(i)
        return check(i,'load-'+str(i))

    # iOS fires 'ended' when the previous src is cleared mid-load. That used to
    # auto-advance to the next song (tap 5, hear song-1).
    load_wait(0)
    page.evaluate("""async ()=>{
      const p=load(5,true);
      audio.dispatchEvent(new Event('ended'));
      audio.dispatchEvent(new Event('emptied'));
      await p;
    }""")
    wait(5)
    s=check(5,'ended-during-load-of-5')
    print('ended during load',s)

    # Second tap at 0, 50, 100, 200, 400, 800 ms after the first, including while
    # the first load is still building (slow IDB + 72 songs + CPU x4).
    for ms in (0,50,100,200,400,800):
        page.evaluate('()=>load(1,true)')
        if ms: page.wait_for_timeout(ms)
        page.evaluate('()=>load(6,true)')
        # And a late ended, as iOS may deliver it after the second tap too.
        page.evaluate("()=>{audio.dispatchEvent(new Event('ended'))}")
        wait(6,timeout=120000)
        s=check(6,f'tap-1-then-6-at-{ms}ms')
        print(f'delay {ms}ms',s)

    # Another pair: first just started playing, then tap a middle song.
    load_wait(2)
    page.wait_for_timeout(50)
    page.evaluate("""async ()=>{
      const p=load(4,true);
      audio.dispatchEvent(new Event('ended'));
      await p;
    }""")
    wait(4)
    s=check(4,'just-playing-then-tap-4')
    print('just playing',s)

    page.evaluate('()=>{window.__txDelay=0}')
    print('PASS v22: second tap at 0/50/100/200/400/800ms, ended/emptied during load, 72-song library, slow IDB, CPU x4 — title, cur, first segment and audible tone always match the last tapped song')
    browser.close()
server.shutdown()
