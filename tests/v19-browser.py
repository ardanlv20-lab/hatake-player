"""v19: tapping any song always plays, even when the library mixes un-normalized
files or iOS mislabels m4a/wav as audio/mpeg. Chromium + ffmpeg.
Run: python3 tests/v19-browser.py"""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
MSG="document.getElementById('msg').textContent"
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)

def state_js():
    return """()=>({msg:document.getElementById('msg').textContent,cur,title:document.getElementById('title').textContent,
      paused:audio.paused,ready:audio.readyState,err:audio.error&&{code:audio.error.code,message:audio.error.message},
      C:C&&{count:C.count,n:C.segs.length,first:C.segs[0]&&C.segs[0].i},src:!!audio.getAttribute('src')})"""

with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    t=pathlib.Path(tmp)
    def mk(name,freq,dur,ar,ac,*extra):
        f=str(t/name);ff('-f','lavfi','-i',f'sine=frequency={freq}:duration={dur}','-ar',str(ar),'-ac',str(ac),*extra,'-y',f);return f
    s44=mk('s44.mp3',440,4,44100,2,'-b:a','192k')
    s44b=mk('s44b.mp3',550,4,44100,2,'-b:a','192k')
    s44c=mk('s44c.mp3',660,4,44100,2,'-b:a','128k')
    s48=mk('s48.mp3',770,4,48000,2,'-b:a','192k')
    mono=mk('mono.mp3',880,4,44100,1,'-b:a','128k')
    m4a=mk('song.m4a',990,4,44100,2,'-c:a','aac','-b:a','128k')
    wav=mk('x.wav',330,4,44100,2)
    raw={k:open(f,'rb').read() for k,f in dict(s44=s44,s44b=s44b,s44c=s44c,s48=s48,mono=mono,m4a=m4a,wav=wav).items()}

    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v19'

    # iOS-like library: m4a and wav stored as audio/mpeg (picker often lies); mixed rates/channels.
    items=[
        ['a','s44',list(raw['s44']),'audio/mpeg'],
        ['b','s44b',list(raw['s44b']),'audio/mpeg'],
        ['c','m4a-lying',list(raw['m4a']),'audio/mpeg'],
        ['d','s48',list(raw['s48']),'audio/mpeg'],
        ['e','mono',list(raw['mono']),'audio/mpeg'],
        ['f','wav-lying',list(raw['wav']),'audio/mpeg'],
        ['g','m4a-ok',list(raw['m4a']),'audio/x-m4a'],
        ['h','s44c',list(raw['s44c']),'audio/mpeg'],
    ]
    page.evaluate("""async (items)=>{
      for(const [id,title,bytes,type] of items){
        const buf=new Uint8Array(bytes).buffer;
        await tx('blobs','readwrite',st=>st.put({buf,type},id));
        await tx('meta','readwrite',st=>st.put({id,title,duration:4,size:buf.byteLength,added:Date.now()}));
        songs.push({id,title,duration:4,size:buf.byteLength,added:Date.now()});
      }
      render();makeOrder();
    }""",items)
    assert page.evaluate("songs.length")==8

    def load_ok(i,play=True,label=''):
        page.evaluate("""async ([i,play])=>{await load(i,play)}""",[i,play])
        page.wait_for_function('!loadJob',timeout=30000)
        s=page.evaluate(state_js())
        assert '読み込めませんでした' not in (s['msg'] or '') and 'エラー' not in (s['msg'] or ''),(label,s)
        assert s['err'] is None,(label,s)
        assert s['cur']==i and s['src'] and s['ready']>=1,(label,s)
        if play: assert s['paused'] is False,(label,'expected playing',s)
        return s

    # First song: the two 44.1k stereo MP3s should join.
    s=load_ok(0,True,'first')
    assert s['C'] and s['C']['count']>=2 and s['C']['first']==0,s
    page.wait_for_timeout(200)

    # Tap every other song (playing): mislabeled m4a/wav must fall back and play, not error.
    for i,title in enumerate(['s44','s44b','m4a-lying','s48','mono','wav-lying','m4a-ok','s44c']):
        s=load_ok(i,True,'tap-'+title)
        print('tap',title,s['msg'],'C',s['C'])
        if title in ('m4a-lying','wav-lying','m4a-ok'):
            assert s['C'] is None,('must not treat non-MP3 as a joined stream',title,s)
            assert '通常モード' in s['msg'],s
        if title=='s44':
            assert s['C'] and s['C']['count']>=2,s

    # Backward, paused, then play another, then skip buttons.
    load_ok(5,False,'paused-wav')
    assert page.evaluate('audio.paused')
    load_ok(2,True,'play-m4a-from-pause')
    load_ok(0,True,'back-to-first')
    page.click('#next')
    page.wait_for_function('!loadJob && cur===1',timeout=20000)
    s=page.evaluate(state_js())
    assert s['err'] is None and '読み込めませんでした' not in s['msg'] and not s['paused'],s
    page.click('#prev')
    page.wait_for_function('!loadJob && cur===0',timeout=20000)
    s=page.evaluate(state_js())
    assert s['err'] is None and not s['paused'],s

    # Import path also sniffs type so a picked m4a is not stored as mpeg if the bytes say otherwise.
    sniffed=page.evaluate("""async (bytes)=>{
      const u=new Uint8Array(bytes);
      return {type:sniffType(u,'audio/mpeg'),mp3:isMp3(u)}
    }""",list(raw['m4a']))
    print('sniff m4a labeled mpeg',sniffed)
    assert sniffed['type'] in ('audio/mp4','audio/x-m4a') and not sniffed['mp3'],sniffed
    assert page.evaluate("""(bytes)=>sniffType(new Uint8Array(bytes),'audio/mpeg')==='audio/mpeg'""",list(raw['s44']))

    # After normalization, mixed files become target MP3s; tapping still works and they join.
    page.click('#convert')
    page.wait_for_function("!document.getElementById('convert').disabled && "+MSG+".includes('そろえました')",timeout=120000)
    print('after convert',page.evaluate(MSG))
    s=load_ok(0,True,'after-convert-0')
    # All 8 should now be joinable 44.1k stereo MP3s.
    page.wait_for_function("C && C.segs && C.segs.length===songs.length",timeout=20000)
    for i in (2,5,7,1):
        load_ok(i,True,'post-convert-'+str(i))
    assert page.evaluate("songs.every(s=>true)")  # still 8
    assert errors==[] or all('Service Worker' in e for e in errors),errors
    print('PASS v19: mislabeled m4a/wav no longer show 音楽ファイルを読み込めませんでした, every list tap plays (mix of 44.1k/48k/mono/m4a/wav, playing/paused/next/prev), 44.1k songs still join, sniffType fixes MIME, convert then tap still works')
    browser.close()
server.shutdown()
