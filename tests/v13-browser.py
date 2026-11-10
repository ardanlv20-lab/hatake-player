"""m4a -> MP3 conversion (Chromium + ffmpeg). Run: python3 tests/v13-browser.py"""
import functools, http.server, os, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    t=pathlib.Path(tmp)
    m4a=str(t/'field-song.m4a');mp3=str(t/'plain.mp3');old=str(t/'old.m4a');mono=str(t/'mono.wav');bad=str(t/'protected.m4a')
    ff('-f','lavfi','-i','sine=frequency=440:duration=2','-ar','44100','-ac','2','-c:a','aac','-b:a','128k','-y',m4a)
    ff('-f','lavfi','-i','sine=frequency=550:duration=2','-af','volume=12dB','-ar','44100','-ac','2','-b:a','192k','-y',mp3)  # loud: v15 never re-encodes it
    ff('-f','lavfi','-i','sine=frequency=660:duration=3','-ar','48000','-ac','2','-c:a','aac','-b:a','128k','-y',old)
    ff('-f','lavfi','-i','sine=frequency=330:duration=1','-ar','22050','-ac','1','-y',mono)
    data=bytearray(open(m4a,'rb').read());data[200:]=os.urandom(len(data)-200);open(bad,'wb').write(data)  # undecodable stand-in for DRM
    mp3size=os.path.getsize(mp3)
    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v15'
    assert page.locator('#convert').inner_text()=='形式と音量をそろえる'
    assert page.evaluate("getComputedStyle(document.getElementById('convert')).minHeight")=='64px'
    page.set_input_files('#file',[m4a,mp3])
    page.wait_for_function("songs.length===2 && cur===0 && !loadJob && audio.readyState>=1",timeout=60000)
    info=page.evaluate("""async () => {
      const out=[];
      for(const s of songs){const b=await tx('blobs','readonly',st=>st.get(s.id));const u=new Uint8Array(b.buf);const r=parseMp3(u);
        out.push({title:s.title,type:b.type,size:u.length,meta:s.size,mp3:!!r&&isMp3(u),sr:r&&r.sr,dur:s.duration})}
      return {out,msg:document.getElementById('msg').textContent,segs:C?C.segs.length:0};
    }""")
    a,b=info['out']
    assert a['title']=='field-song' and a['type']=='audio/mpeg' and a['mp3'] and a['meta']==a['size'] and 1.8<a['dur']<2.5,info
    assert b['type']=='audio/mpeg' and b['mp3'] and b['size']==mp3size,('mp3 re-encoded?',info,mp3size)
    assert info['segs']==2,('converted m4a should join the mp3 stream',info)
    assert '2曲を取り込みました（うち1曲を44.1kHzステレオのMP3に変換しました）' in page.evaluate("document.getElementById('msg').textContent")
    # Simulate an old (pre-v13) m4a import already stored in IDB, then use the button.
    page.evaluate("""async (bytes) => {
      const buf=new Uint8Array(bytes).buffer,id='old-m4a';
      await tx('blobs','readwrite',st=>st.put({buf,type:'audio/mp4'},id));
      const meta={id,title:'昔の曲',duration:3,size:buf.byteLength,added:Date.now()};
      await tx('meta','readwrite',st=>st.put(meta));songs.push(meta);render();makeOrder();
    }""",list(open(old,'rb').read()))
    page.click('#convert')
    page.wait_for_function("!document.getElementById('convert').disabled && document.getElementById('msg').textContent.includes('曲を44.1kHzステレオのMP3にそろえました')",timeout=60000)
    res=page.evaluate("""async () => {
      const s=songs.find(x=>x.id==='old-m4a');const b=await tx('blobs','readonly',st=>st.get('old-m4a'));const u=new Uint8Array(b.buf);
      const m=(await tx('meta','readonly',st=>st.get('old-m4a')));
      return {title:s.title,mtitle:m.title,type:b.type,mp3:isMp3(u),size:u.length,msize:m.size,dur:m.duration,n:songs.length,msg:document.getElementById('msg').textContent,cur,paused:audio.paused,sr:parseMp3(u).sr}
    }""")
    assert res['title']=='昔の曲'==res['mtitle'] and res['type']=='audio/mpeg' and res['mp3'] and res['size']==res['msize'] and 2.8<res['dur']<3.5 and res['n']==3,res
    assert res['msg'].startswith('1曲を44.1kHzステレオのMP3にそろえました'),res
    assert res['cur']==0 and res['paused'],('no surprise autoplay',res)
    page.wait_for_function("!loadJob && C && C.segs.length===3",timeout=20000)  # 48k m4a decoded at 44.1k joins
    page.click('#convert')
    page.wait_for_function("document.getElementById('msg').textContent==='そろえる必要がある曲はありません'",timeout=20000)
    # Non-empty idle library: adding converts but must not auto-load. Undecodable file is kept.
    page.evaluate("sessionStorage.removeItem('holdBuild')");page.reload();page.wait_for_function('window.__ready')
    assert page.evaluate('cur')==-1
    page.set_input_files('#file',[mono,bad])
    page.wait_for_function("songs.length===5 && document.getElementById('msg').textContent.includes('曲を取り込みました')",timeout=60000)
    r2=page.evaluate("""async () => {
      const out=[];for(const s of songs.slice(3)){const b=await tx('blobs','readonly',st=>st.get(s.id));out.push({t:s.title,type:b.type,mp3:isMp3(new Uint8Array(b.buf))})}
      return {out,cur,msg:document.getElementById('msg').textContent,title:document.getElementById('title').textContent}
    }""")
    assert r2['cur']==-1 and r2['title']=='曲を選んでください',r2
    assert r2['out'][0]['mp3'] and r2['out'][0]['type']=='audio/mpeg',r2
    assert not r2['out'][1]['mp3'] and r2['out'][1]['type']!='audio/mpeg',r2
    assert 'うち1曲を44.1kHzステレオのMP3に変換しました' in r2['msg'] and 'protected.m4a' in r2['msg'],r2
    # Button: failed song stays, message says so; MP3s untouched.
    sizes=page.evaluate("songs.slice(0,4).map(s=>s.size)")
    page.click('#convert')
    page.wait_for_function("!document.getElementById('convert').disabled && document.getElementById('msg').textContent.includes('そろえられなかった曲')",timeout=30000)
    assert page.evaluate("songs.slice(0,4).map(s=>s.size)")==sizes
    # Offline: the encoder must come from the service-worker cache, and still convert.
    ctx2=browser.new_context();pg=ctx2.new_page();pg.on('pageerror',lambda e:errors.append(str(e)))
    pg.goto(url);pg.wait_for_function('window.__ready');pg.wait_for_function('navigator.serviceWorker.controller!==null',timeout=20000)
    ctx2.set_offline(True);pg.reload();pg.wait_for_function('window.__ready')
    assert pg.evaluate("typeof lamejs==='function' && !!lamejs.Mp3Encoder"),'encoder not cached offline'
    pg.set_input_files('#file',[m4a])
    pg.wait_for_function("songs.length===1 && document.getElementById('msg').textContent.includes('うち1曲を44.1kHzステレオのMP3に変換しました')",timeout=60000)
    ctx2.close()
    assert not errors,errors
    print('PASS v13: m4a converted on import (empty-library cur===0, joins mp3 stream), mp3 not re-encoded, old m4a converted in place by button (same id/title, no autoplay), nothing-to-convert message, mono wav converted, undecodable file kept, idle add no autoload, offline encoder from SW cache')
    browser.close()
server.shutdown()
