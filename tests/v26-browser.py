"""v26: a list tap starts a joined chunk at the tapped song (offset 0), so
background playback can continue through later songs. After 「形式と音量をそろえる」
mixed/VBR/m4a files become 192kbps CBR and join (up to 12). Incompatible songs
fall back to 単曲・形式未統一. Chromium + ffmpeg. Run: python3 tests/v26-browser.py"""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)

N,DUR=14,3.0
TONES=[400+i*80 for i in range(N)]  # 14 x 192k CBR
T128,T320,TVBR=2400,2600,2800

AT="""async (t) => {
  const url=audio.src||audio.currentSrc;if(!url)return {err:'no src'};
  const b=await (await fetch(url)).blob();
  const ac=new OfflineAudioContext(2,44100,44100);
  const ab=await ac.decodeAudioData(await b.arrayBuffer());
  const ch=ab.getChannelData(0),sr=ab.sampleRate;
  const s=Math.max(0,Math.floor(t*sr));
  const e=Math.min(ch.length,s+Math.floor(0.35*sr));
  if(e-s<sr*0.2)return {err:'short',t,len:ch.length/sr};
  let z=0,last=ch[s];
  for(let i=s+1;i<e;i++){const v=ch[i];if(v>=0&&last<0)z++;last=v}
  const hz=+(z*(sr/(e-s))).toFixed(1);
  const ct=audio.currentTime||0;
  const n=(C&&typeof findSeg==='function')?findSeg(ct):-1;
  const g=(C&&n>=0)?C.segs[n]:null;
  const diag=(document.getElementById('diag')||{}).textContent||'';
  return {hz, t:+t.toFixed(3), ct:+ct.toFixed(3), cur,
    title:document.getElementById('title').textContent,
    id:songs[cur]&&songs[cur].id, firstI:C&&C.segs[0]&&C.segs[0].i,
    segI:g&&g.i, mode:C?'join':'single', count:C&&C.count,
    diag, msg:document.getElementById('msg').textContent, paused:audio.paused};
}"""

def near(hz, expect, tol=70):
    return hz is not None and abs(hz-expect)<=tol

with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    td=pathlib.Path(tmp)
    files=[]
    for i,f0 in enumerate(TONES):
        fn=str(td/f'c192-{i:02d}.mp3')
        ff('-f','lavfi','-i',f'sine=frequency={f0}:duration={DUR}','-ar','44100','-ac','2','-b:a','192k','-y',fn)
        files.append(('c192-%02d'%i, open(fn,'rb').read(), f0, '192'))
    for title,freq,args,kind in (
        ('cbr128',T128,['-b:a','128k'],'128'),
        ('cbr320',T320,['-b:a','320k'],'320'),
        ('vbr',TVBR,['-q:a','6'],'vbr'),
    ):
        fn=str(td/f'{title}.mp3')
        ff('-f','lavfi','-i',f'sine=frequency={freq}:duration={DUR}','-ar','44100','-ac','2',*args,'-y',fn)
        files.append((title, open(fn,'rb').read(), freq, kind))
    m4a=str(td/'conv.m4a')
    ff('-f','lavfi','-i',f'sine=frequency=3000:duration={DUR}','-ar','44100','-ac','2','-c:a','aac','-b:a','128k','-y',m4a)
    s48=str(td/'s48.mp3')
    ff('-f','lavfi','-i','sine=frequency=3100:duration=2','-ar','48000','-ac','2','-b:a','192k','-y',s48)

    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v26'

    items=[[f'id{i}',title,list(blob)] for i,(title,blob,_,__) in enumerate(files)]
    page.evaluate("""async (items)=>{
      for(const [id,title,bytes] of items){
        const buf=new Uint8Array(bytes).buffer;
        const u=new Uint8Array(buf),r=parseMp3(u);
        await tx('blobs','readwrite',st=>st.put({buf,type:'audio/mpeg'},id));
        const meta={id,title,duration:r?r.dur:3,size:buf.byteLength,added:Date.now()};
        await tx('meta','readwrite',st=>st.put(meta));
        songs.push(Object.assign({},meta));
      }
      render();makeOrder();
    }""",items)
    assert page.evaluate('songs.length')==len(files)

    def wait(i,timeout=30000):
        page.wait_for_function(f'!loadJob && mediaReady && cur==={i}',timeout=timeout)

    def tap(i,how=None):
        if how: page.evaluate('([i,h])=>load(i,true,0,h)',[i,how])
        else: page.evaluate('i=>load(i,true)',i)
        wait(i)
        page.wait_for_timeout(80)
        return page.evaluate(AT, 0.35)

    def click_title(title,i):
        page.evaluate("""t=>{
          const el=[...document.querySelectorAll('#list .t')].find(e=>e.textContent.includes(t));
          if(!el)throw new Error('no row for '+t);
          el.click();
        }""",title)
        wait(i)
        page.wait_for_timeout(80)
        return page.evaluate(AT, 0.35)

    # A title tap on a 192k CBR run starts a joined chunk at that song (not a single file).
    s=click_title('c192-02',2)
    print('tap-2',s)
    assert s['mode']=='join' and s['firstI']==2 and s['cur']==2 and s['count']==12,s
    assert '連結 12曲' in s['diag'] and 'c192-02' in s['diag'] and '12曲' in s['msg'],s
    assert s['t']<1.2 and near(s['hz'],TONES[2]),('tapped song must be heard from the start',s,TONES[2])

    # Later songs in the same stream: title matches the decoded tone.
    for k in (3,7,13):
        page.evaluate("k=>{const g=C.segs.find(s=>s.i===k);seekAbs(g.start+0.4);updateProgress()}",k)
        page.wait_for_timeout(80)
        s=page.evaluate(AT, page.evaluate('audio.currentTime'))
        print('in-chunk',k,s)
        assert s['mode']=='join' and s['cur']==k and s['segI']==k and s['firstI']==2,s
        assert files[k][0] in s['title'] and files[k][0] in s['diag'],s
        assert near(s['hz'],TONES[k]),(k,s,TONES[k])

    # Next/prev (step) also start a joined chunk at that song, keeping continuity of the title.
    s=tap(4,'step')
    print('step-4',s)
    assert s['mode']=='join' and s['firstI']==4 and s['cur']==4 and near(s['hz'],TONES[4]),s
    assert '連結' in s['diag'] and s['count']==10,s  # songs 4..13 = 10

    # Incompatible neighbors: 128k / 320k / VBR fall back and the diag says 形式未統一.
    for i,title,freq in ((14,'cbr128',T128),(15,'cbr320',T320),(16,'vbr',TVBR)):
        s=click_title(title,i)
        print('unaligned',title,s)
        assert s['mode']=='single' and s['cur']==i and title in s['title'],s
        assert '単曲・形式未統一' in s['diag'],s
        assert near(s['hz'],freq),(title,s,freq)

    # Convert a mixed leftover library (128/320/VBR + m4a + 48k) to 192k CBR and join them all.
    page.click('#reset')
    page.wait_for_function("document.getElementById('msg').textContent==='再生をリセットしました' && cur===-1",timeout=20000)
    # Replace library with mixed sources (keep a couple of already-192k files that must stay join-target).
    already192=files[0][1]
    page.evaluate("""async (payload)=>{
      const [keep,c128,c320,vbr,m4a,s48]=payload;
      songs=[];parseCache.clear();
      const put=async (id,title,bytes,type)=>{
        const buf=new Uint8Array(bytes).buffer;
        await tx('blobs','readwrite',st=>st.put({buf,type},id));
        const u=new Uint8Array(buf),r=typeof parseMp3==='function'?parseMp3(u):null;
        const meta={id,title,duration:r?r.dur:2,size:buf.byteLength,added:Date.now()};
        await tx('meta','readwrite',st=>st.put(meta));songs.push(Object.assign({},meta));
      };
      // 6 already-192 + mixed that convert must rewrite
      for(let i=0;i<6;i++) await put('k'+i,'keep-'+i,keep,'audio/mpeg');
      await put('a','was-128',c128,'audio/mpeg');
      await put('b','was-320',c320,'audio/mpeg');
      await put('c','was-vbr',vbr,'audio/mpeg');
      await put('d','was-m4a',m4a,'audio/mp4');
      await put('e','was-48k',s48,'audio/mpeg');
      render();makeOrder();
    }""",[list(already192),list(files[14][1]),list(files[15][1]),list(files[16][1]),
          list(open(m4a,'rb').read()),list(open(s48,'rb').read())])
    assert page.evaluate('songs.length')==11

    before=page.evaluate("""async ()=>{
      const out=[];
      for(const s of songs){
        const b=await tx('blobs','readonly',st=>st.get(s.id));
        const u=new Uint8Array(b.buf||b);
        const r=parseMp3(u);
        out.push({id:s.id,title:s.title,target:isTargetMp3(u),cbr:!!(r&&r.cbr),br:r&&r.br,sr:r&&r.sr});
      }
      return out;
    }""")
    print('before convert',before)
    assert all(x['target'] and x['br']==192000 for x in before if x['id'].startswith('k')),before
    assert all((not x['target']) for x in before if x['id'] in 'abcde'),before

    page.click('#convert')
    page.wait_for_function("!document.getElementById('convert').disabled && document.getElementById('msg').textContent.includes('そろえました')",timeout=120000)
    print('convert msg',page.evaluate("document.getElementById('msg').textContent"))

    after=page.evaluate("""async ()=>{
      const out=[];
      for(const s of songs){
        const b=await tx('blobs','readonly',st=>st.get(s.id));
        const u=new Uint8Array(b.buf||b);
        const r=parseMp3(u);
        out.push({id:s.id,title:s.title,target:isTargetMp3(u),cbr:!!(r&&r.cbr),br:r&&r.br,sr:r&&r.sr,frames:r&&r.frames});
      }
      const ch=await buildChunk(0,0);
      const n=ch&&ch.count; if(ch&&ch.url)revokeUnused(ch.url);
      return {songs:out,n};
    }""")
    print('after convert',after)
    assert after['n']==11,('converted library must join as one chunk',after)
    for row in after['songs']:
        assert row['target'] and row['cbr'] and row['br']==192000 and row['sr']==44100,row

    s=click_title('keep-0',0)
    print('tap after convert',s)
    assert s['mode']=='join' and s['firstI']==0 and s['cur']==0 and s['count']==11,s
    assert '連結 11曲' in s['diag'],s
    assert near(s['hz'],TONES[0]),s

    # Mid-chunk title still matches after convert (was-vbr is index 8).
    page.evaluate("()=>{const g=C.segs[8];seekAbs(g.start+0.4);updateProgress()}")
    page.wait_for_timeout(80)
    s=page.evaluate(AT, page.evaluate('audio.currentTime'))
    print('converted mid',s)
    assert s['cur']==8 and s['segI']==8 and 'was-vbr' in s['title'],s
    assert near(s['hz'],TVBR),s

    # リセット then a tap still starts a join at the chosen song.
    page.click('#reset')
    page.wait_for_function("document.getElementById('msg').textContent==='再生をリセットしました' && cur===-1 && !C",timeout=20000)
    s=click_title('keep-2',2)
    print('after reset',s)
    assert s['mode']=='join' and s['firstI']==2 and s['cur']==2 and near(s['hz'],TONES[0]),s
    # keep-2 is a copy of song-0's 400Hz tone (already192 bytes). TONES[0]==400.
    assert '連結' in s['diag'] and s['count']==9,s  # ids 2..10

    assert not errors,errors
    print('PASS v26: list taps start a joined chunk at the tapped song (offset 0, later songs included); title matches decoded audio mid-chunk; next/prev start a join at that song; incompatible 128/320/VBR show 単曲・形式未統一; convert makes mixed/VBR/m4a/48k into 192k CBR that all join; リセット then tap still works')
    browser.close()
server.shutdown()
