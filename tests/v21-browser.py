"""v21: tapping a song always starts THAT song (shuffle off too).
A leftover currentTime/lastAbs must not land in another segment of a joined
chunk, and a tap must never seek inside the long stream. Chromium + ffmpeg.
Run: python3 tests/v21-browser.py"""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)

# Skip the Xing frame and measure zero-crossing frequency of the first audio.
HZ="""async () => {
  if(!audio.getAttribute('src')&&!audio.currentSrc)return {err:'no src'};
  const b=await (await fetch(audio.src)).blob();
  const ac=new OfflineAudioContext(2,44100,44100);
  const ab=await ac.decodeAudioData(await b.slice(0,800000).arrayBuffer());
  const ch=ab.getChannelData(0);
  const start=2000; let z=0,last=0;
  for(let i=start;i<start+4410;i++){const v=ch[i];if(v>=0&&last<0)z++;last=v}
  const hz=z*(44100/4410);
  const t=audio.currentTime||0;
  const n=(C&&typeof findSeg==='function')?findSeg(t):-1;
  const g=(C&&n>=0)?C.segs[n]:null;
  return {hz:+hz.toFixed(1), t:+t.toFixed(3), cur,
    title:document.getElementById('title').textContent,
    id:songs[cur]&&songs[cur].id, segI:g&&g.i, firstI:C&&C.segs[0]&&C.segs[0].i,
    lastAbs, paused:audio.paused, mode:C?'join':'single', count:C&&C.count};
}"""

def near(hz, expect, tol=80):
    return abs(hz-expect)<=tol

with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    td=pathlib.Path(tmp)
    # Distinct tones so the first audio of a tap identifies the song.
    specs=[
        ('song-0',400,44100,'mp3'),
        ('song-1',800,44100,'mp3'),
        ('song-2-m4a',1200,44100,'m4a'),
        ('song-3',1600,44100,'mp3'),
        ('song-4',2000,44100,'mp3'),
        ('song-5-48k',2400,48000,'mp3'),
        ('song-6',2800,44100,'mp3'),
    ]
    raws=[]
    for name,f0,ar,fmt in specs:
        fn=str(td/f'{name}.{fmt}')
        extra=['-c:a','aac'] if fmt=='m4a' else ['-b:a','128k']
        ff('-f','lavfi','-i',f'sine=frequency={f0}:duration=4','-ar',str(ar),'-ac','2',*extra,'-y',fn)
        raws.append((name,f0,fmt,open(fn,'rb').read()))
    extra_name,extra_hz='song-imported',3200
    fn=str(td/'imp.mp3')
    ff('-f','lavfi','-i',f'sine=frequency={extra_hz}:duration=4','-ar','44100','-ac','2','-b:a','128k','-y',fn)
    extra_raw=open(fn,'rb').read()

    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    page=browser.new_context(service_workers='block').new_page()
    page.on('dialog',lambda d:d.accept())
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v21'

    items=[[f'id{i}',name,list(data),'audio/mpeg' if fmt=='mp3' else 'audio/mp4'] for i,(name,f0,fmt,data) in enumerate(raws)]
    page.evaluate("""async (items)=>{
      S.shuffle=false;S.repeat='none';save();ui();
      for(const [id,title,bytes,type] of items){
        const buf=new Uint8Array(bytes).buffer;
        await tx('blobs','readwrite',st=>st.put({buf,type},id));
        const meta={id,title,duration:4,size:buf.byteLength,added:Date.now()};
        await tx('meta','readwrite',st=>st.put(meta));songs.push(meta);
      }
      render();makeOrder();
    }""",items)
    expect=[{'title':n,'hz':hz} for n,hz,fmt,_ in raws]

    def wait(cur=None,timeout=30000):
        if cur is None: page.wait_for_function('!loadJob',timeout=timeout)
        else: page.wait_for_function(f'!loadJob && cur==={cur}',timeout=timeout)

    def check(i,label,play=True):
        s=page.evaluate(HZ)
        assert s.get('err') is None, (label,s)
        assert s['cur']==i and s['title']==expect[i]['title'] and s['id']==f'id{i}',(label,'identity',s)
        if s['mode']=='join':
            assert s['firstI']==i and s['segI']==i,(label,'segment',s)
        assert s['t']<1.0,(label,'must start at the beginning, not mid-stream',s)
        assert near(s['hz'],expect[i]['hz']), (label,'audible',s,expect[i]['hz'])
        if play: assert s['paused'] is False,(label,'paused',s)
        return s

    def tap(i,label,play=True):
        page.evaluate("""async ([i,play])=>{await load(i,play)}""",[i,play])
        wait(i)
        page.wait_for_timeout(150)
        return check(i,label,play=play)

    # --- shuffle OFF, repeat OFF: tap every title while the previous is playing ---
    s=tap(0,'first')
    assert s['mode']=='join' and s['count']>=2,s
    print('first',s)
    for i in range(1,len(expect)):
        s=tap(i,'tap-while-playing-'+expect[i]['title'])
        print('tap',i,s['title'],s['hz'],s['mode'],'t',s['t'])

    # Paused tap, then another
    page.evaluate('()=>pausePlayback()')
    tap(0,'from-pause-0',play=True)
    page.evaluate('()=>pausePlayback()')
    tap(4,'from-pause-4',play=True)

    # --- iOS-like currentTime carryover into the new joined src (would seek into song 5 of the old stream) ---
    tap(0,'setup-carry')
    page.evaluate("""()=>{
      window.__carry=8; // 8s = two songs into a joined 4s-per-song stream
      const d=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'src');
      window.__origSrc=d;
      Object.defineProperty(HTMLMediaElement.prototype,'src',{
        configurable:true,
        get(){return d.get.call(this)},
        set(v){d.set.call(this,v);const c=window.__carry,el=this;
          if(c)setTimeout(()=>{try{if(el.duration>c)el.currentTime=c}catch(e){}},0)}
      });
    }""")
    s=tap(3,'carryover-8s-tap-3')
    print('carryover',s)
    # Restore the native setter
    page.evaluate("""()=>{
      window.__carry=0;
      if(window.__origSrc) Object.defineProperty(HTMLMediaElement.prototype,'src',window.__origSrc);
    }""")

    # --- leftover lastAbs from a previous stream must not jump the new tap ---
    tap(0,'setup-lastAbs')
    page.evaluate('()=>{lastAbs=8; try{audio.currentTime=8}catch(e){}}')
    page.wait_for_timeout(80)
    s=tap(4,'tap-4-after-lastAbs-8')
    page.evaluate('()=>startAudio()')
    page.wait_for_timeout(250)
    s=check(4,'startAudio-after-tap-4')
    print('stale lastAbs',s)

    # Rapid taps: last one wins and is the song that plays
    for i in (1,6,0,3):
        page.evaluate('i=>{load(i,true)}',i)
    wait(3,timeout=60000)
    page.wait_for_timeout(200)
    s=check(3,'rapid-last-is-3')
    print('rapid',s)

    # --- shuffle ON: a list tap still plays the tapped library song, not order[i] ---
    page.evaluate("""()=>{S.shuffle=true;makeOrder();save();ui();
      // Force a permutation that is not identity, if the random shuffle happened to be one.
      if(order.every((v,i)=>v===i)){order=[2,0,4,6,1,5,3]}
    }""")
    print('shuffle order',page.evaluate('order'))
    for i in (0,2,5,6):
        s=tap(i,'shuffle-on-'+str(i))
        print('shuffle tap',i,s['title'],s['hz'])
    page.evaluate('()=>{S.shuffle=false;makeOrder();save();ui()}')

    # Repeat modes: tap is still the tapped song
    for mode in ('all','one','none'):
        page.evaluate('m=>{S.repeat=m;save();ui()}',mode)
        s=tap(1,'repeat-'+mode)
        print('repeat',mode,s['title'],s['hz'],s['mode'])
    page.evaluate("()=>{S.repeat='none';save();ui()}")

    # Import a new song and tap it; delete one and tap another
    imported=page.evaluate("""async (bytes)=>{
      const f=new File([new Uint8Array(bytes)],'song-imported.mp3',{type:'audio/mpeg'});
      await addFiles([f]);
      return songs.length;
    }""",list(extra_raw))
    page.wait_for_function('!loadJob',timeout=30000)
    assert imported==len(expect)+1,imported
    expect.append({'title':'song-imported','hz':extra_hz})
    # imported id is not idN — check by title/index
    page.evaluate('i=>load(i,true)',len(expect)-1)
    wait(len(expect)-1)
    page.wait_for_timeout(150)
    s=page.evaluate(HZ)
    assert s['title']=='song-imported' and near(s['hz'],extra_hz) and s['t']<1,(s,)
    print('imported',s)
    # Delete the imported song, tap song 3
    page.evaluate('i=>del(i)',len(expect)-1)
    page.wait_for_function('!loadJob',timeout=30000)
    expect.pop()
    s=tap(3,'after-delete-tap-3')
    print('after delete',s)

    print('PASS v21: every list tap (shuffle on/off, repeat modes, playing/paused, rapid, carryover currentTime, leftover lastAbs, import/delete, mixed m4a/48k) starts the tapped song at offset 0 with matching title and audible tone')
    browser.close()
server.shutdown()
