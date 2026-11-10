"""Normalize library to 44.1kHz stereo MPEG-1 MP3 (Chromium + ffmpeg). Run: python3 tests/v14-browser.py"""
import functools, http.server, os, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)
MSG="document.getElementById('msg').textContent"
INSPECT="""async (ids) => {
  const out={};
  for(const s of songs){if(ids&&!ids.includes(s.id))continue;
    const b=await tx('blobs','readonly',st=>st.get(s.id));const u=new Uint8Array(b.buf);const r=isMp3(u)?parseMp3(u):null;
    out[s.title]={id:s.id,type:b.type,size:u.length,meta:s.size,dur:s.duration,sr:r&&r.sr,v:r&&r.v,mode:r&&r.mode,target:isTargetMp3(u),bytes:Array.from(u.subarray(0,64))}}
  return out;
}"""
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    t=pathlib.Path(tmp)
    def mk(name,freq,dur,ar,ac,*extra):
        f=str(t/name);ff('-f','lavfi','-i',f'sine=frequency={freq}:duration={dur}','-ar',str(ar),'-ac',str(ac),*extra,'-y',f);return f
    s44=mk('s44.mp3',440,2,44100,2,'-af','volume=12dB','-b:a','192k')  # loud: v15 never re-encodes it
    s48=mk('s48.mp3',550,2,48000,2,'-b:a','192k')
    m44=mk('m44.mp3',660,2,44100,1,'-b:a','128k')
    v22=mk('v22.mp3',330,2,22050,2,'-b:a','64k')       # MPEG-2
    m4a=mk('song.m4a',770,2,44100,2,'-c:a','aac','-b:a','128k')
    s48b=mk('late48.mp3',880,2,48000,2,'-b:a','192k')
    raw={k:open(f,'rb').read() for k,f in dict(s44=s44,s48=s48,m44=m44,v22=v22,m4a=m4a).items()}
    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v15'
    assert page.locator('#convert').inner_text()=='形式と音量をそろえる'
    assert page.evaluate("getComputedStyle(document.getElementById('convert')).minHeight")=='64px'
    # Encoder output: 44.1kHz MPEG-1, 2ch never mono; record lamejs channel mode.
    enc=page.evaluate("""async (b) => {const out=new Uint8Array(await toMp3(new Uint8Array(b).buffer));const r=parseMp3(out);return {sr:r.sr,v:r.v,mode:r.mode}}""",list(raw['m44']))
    assert enc['sr']==44100 and enc['v']==3 and enc['mode']!=3,enc
    print('lamejs 2ch output channel mode =',enc['mode'],'(0 stereo, 1 joint stereo)')
    # Old iOS: AudioContext not at 44.1kHz -> OfflineAudioContext resample, and linear fallback.
    fb=page.evaluate("""async (b) => {
      const keep=actx;actx=new AudioContext({sampleRate:48000});const res={};
      let o=new Uint8Array(await toMp3(new Uint8Array(b).buffer));let r=parseMp3(o);res.offline={sr:r.sr,mode:r.mode,dur:r.dur};
      const O=window.OfflineAudioContext;window.OfflineAudioContext=undefined;
      try{o=new Uint8Array(await toMp3(new Uint8Array(b).buffer));r=parseMp3(o);res.linear={sr:r.sr,mode:r.mode,dur:r.dur}}finally{window.OfflineAudioContext=O}
      await actx.close();actx=keep;return res}""",list(raw['s48']))
    for k in ('offline','linear'):
        assert fb[k]['sr']==44100 and fb[k]['mode']!=3 and 1.9<fb[k]['dur']<2.3,fb
    # Empty library import: every song normalized, joined into one chunk, cur===0.
    page.set_input_files('#file',[s44,s48,m44,v22,m4a])
    page.wait_for_function("songs.length===5 && cur===0 && !loadJob && audio.readyState>=1",timeout=90000)
    page.wait_for_function("C && C.segs.length===5",timeout=20000)
    info=page.evaluate(INSPECT,None)
    assert info['s44']['size']==len(raw['s44']) and info['s44']['bytes']==list(raw['s44'][:64]),'44.1k stereo mp3 must stay byte-identical'
    for k in ('s48','m44','v22','song'):
        x=info[k];assert x['target'] and x['type']=='audio/mpeg' and x['sr']==44100 and x['v']==3 and x['mode']!=3 and x['size']==x['meta'] and 1.8<x['dur']<2.5,(k,x)
    assert '5曲を取り込みました（うち4曲を44.1kHzステレオのMP3に変換しました）' in page.evaluate(MSG),page.evaluate(MSG)
    assert page.evaluate('audio.paused')
    # Pre-v14 library: raw non-target files stored as-is, then the button.
    page.evaluate("""async (items) => {
      for(const [id,title,bytes,type] of items){const buf=new Uint8Array(bytes).buffer;
        await tx('blobs','readwrite',st=>st.put({buf,type},id));
        const meta={id,title,duration:2,size:buf.byteLength,added:Date.now()};
        await tx('meta','readwrite',st=>st.put(meta));songs.push(meta)}
      render();makeOrder();
    }""",[['old-s44','昔44',list(raw['s44']),'audio/mpeg'],['old-s48','昔48',list(raw['s48']),'audio/mpeg'],
          ['old-m44','昔モノラル',list(raw['m44']),'audio/mpeg'],['old-v22','昔22k',list(raw['v22']),'audio/mpeg'],['old-m4a','昔m4a',list(raw['m4a']),'audio/mp4']])
    page.click('#convert')
    page.wait_for_function("!document.getElementById('convert').disabled && "+MSG+".includes('曲を44.1kHzステレオのMP3にそろえました')",timeout=90000)
    res=page.evaluate(INSPECT,['old-s44','old-s48','old-m44','old-v22','old-m4a'])
    m=page.evaluate(MSG)
    assert m.startswith('4曲を44.1kHzステレオのMP3にそろえました'),m
    assert res['昔44']['size']==len(raw['s44']) and res['昔44']['bytes']==list(raw['s44'][:64]),res['昔44']
    for k in ('昔48','昔モノラル','昔22k','昔m4a'):
        x=res[k];assert x['target'] and x['type']=='audio/mpeg' and x['size']==x['meta'] and 1.8<x['dur']<2.5,(k,x)
    order_titles=page.evaluate("songs.map(s=>s.title)")
    assert order_titles==['s44','s48','m44','v22','song','昔44','昔48','昔モノラル','昔22k','昔m4a'],order_titles
    assert page.evaluate('cur')==0 and page.evaluate('audio.paused'),'no surprise autoplay'
    page.wait_for_function("!loadJob && C && C.segs.length===songs.length && songs.length===10",timeout=20000)
    page.click('#convert')
    page.wait_for_function(MSG+"==='そろえる必要がある曲はありません'",timeout=20000)
    # Idle add into non-empty library: normalized, no autoload.
    page.evaluate("sessionStorage.removeItem('holdBuild')");page.reload();page.wait_for_function('window.__ready')
    assert page.evaluate('cur')==-1
    page.set_input_files('#file',[s48b])
    page.wait_for_function("songs.length===11 && "+MSG+".includes('曲を取り込みました')",timeout=60000)
    r2=page.evaluate("""async () => {const s=songs[10];const b=await tx('blobs','readonly',st=>st.get(s.id));
      return {target:isTargetMp3(new Uint8Array(b.buf)),cur,msg:document.getElementById('msg').textContent,title:document.getElementById('title').textContent}}""")
    assert r2['target'] and r2['cur']==-1 and r2['title']=='曲を選んでください' and 'うち1曲を44.1kHzステレオのMP3に変換しました' in r2['msg'],r2
    page.evaluate("load(0,false)")
    page.wait_for_function("!loadJob && C && C.segs.length===11",timeout=20000)
    # Offline: encoder from SW cache; normalizes a 48k MP3.
    ctx2=browser.new_context();pg=ctx2.new_page();pg.on('pageerror',lambda e:errors.append(str(e)))
    pg.goto(url);pg.wait_for_function('window.__ready');pg.wait_for_function('navigator.serviceWorker.controller!==null',timeout=20000)
    ctx2.set_offline(True);pg.reload();pg.wait_for_function('window.__ready')
    assert pg.locator('#ver').inner_text()=='v15'
    pg.set_input_files('#file',[s48,s44])
    pg.wait_for_function("songs.length===2 && cur===0 && !loadJob && C && C.segs.length===2",timeout=60000)
    assert 'うち1曲を44.1kHzステレオのMP3に変換しました' in pg.evaluate(MSG)
    ctx2.close()
    assert not errors,errors
    print('PASS v14: 48k/mono/MPEG-2 MP3 + m4a normalized to 44.1kHz stereo MPEG-1 on import and by button, 44.1k stereo MP3 byte-identical, single joined chunk across all songs, order/titles kept, no autoplay, nothing-to-do message, 48k-context resample (Offline + linear), idle add no autoload, empty-library cur===0, offline')
    browser.close()
server.shutdown()
