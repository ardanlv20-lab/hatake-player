"""v25: joined-stream title must match the decoded audio at several currentTimes.
Mixed CBR bitrates and VBR must not share one chunk (iOS currentTime would drift).
Same-CBR songs join (capped at 12). Shuffle + repeat-all still maps title to tone.
Chromium + ffmpeg. Run: python3 tests/v25-browser.py"""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)

N192,DUR=22,3.0
# Distinct tones so a drifted segment table cannot accidentally match a neighbor.
TONES=[400+i*80 for i in range(N192)]  # 400..2080
T128,T320,TVBR=2400,2600,2800

AT="""async (t) => {
  const url=audio.src||audio.currentSrc;if(!url)return {err:'no src'};
  const b=await (await fetch(url)).blob();
  const ac=new OfflineAudioContext(2,44100,44100);
  const ab=await ac.decodeAudioData(await b.arrayBuffer());
  const ch=ab.getChannelData(0),sr=ab.sampleRate;
  const s=Math.max(0,Math.floor(t*sr));
  const e=Math.min(ch.length,s+Math.floor(0.4*sr));
  if(e-s<sr*0.2)return {err:'short',t,len:ch.length/sr};
  let z=0,last=ch[s];
  for(let i=s+1;i<e;i++){const v=ch[i];if(v>=0&&last<0)z++;last=v}
  const hz=+(z*(sr/(e-s))).toFixed(1);
  const ct=audio.currentTime||0;
  const n=(C&&typeof findSeg==='function')?findSeg(ct):-1;
  const g=(C&&n>=0)?C.segs[n]:null;
  const diag=(document.getElementById('diag')||{}).textContent||'';
  const title=document.getElementById('title').textContent;
  return {hz, t:+t.toFixed(3), ct:+ct.toFixed(3), cur, title,
    id:songs[cur]&&songs[cur].id, firstI:C&&C.segs[0]&&C.segs[0].i,
    segI:g&&g.i, segStart:g&&+g.start.toFixed(3), segDur:g&&+g.dur.toFixed(3),
    mode:C?'join':'single', count:C&&C.count, loop:!!(C&&C.loop),
    diag, srcKind:C?'連結':'単曲',
    msg:document.getElementById('msg').textContent,
    duration:audio.duration, total:C&&C.total};
}"""

def near(hz, expect, tol=70):
    return hz is not None and abs(hz-expect)<=tol

with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    td=pathlib.Path(tmp)
    files=[]
    for i,f0 in enumerate(TONES):
        fn=str(td/f'c192-{i:02d}.mp3')
        ff('-f','lavfi','-i',f'sine=frequency={f0}:duration={DUR}','-ar','44100','-ac','2','-b:a','192k','-y',fn)
        files.append(('c192-%02d'%i, open(fn,'rb').read(), f0, '192cbr'))
    for title,freq,br,mode in (('cbr128',T128,'128k','128cbr'),('cbr320',T320,'320k','320cbr')):
        fn=str(td/f'{title}.mp3')
        ff('-f','lavfi','-i',f'sine=frequency={freq}:duration={DUR}','-ar','44100','-ac','2','-b:a',br,'-y',fn)
        files.append((title, open(fn,'rb').read(), freq, mode))
    fn=str(td/'vbr.mp3')
    ff('-f','lavfi','-i',f'sine=frequency={TVBR}:duration={DUR}','-ar','44100','-ac','2','-q:a','6','-y',fn)
    files.append(('vbr', open(fn,'rb').read(), TVBR, 'vbr'))

    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v25'

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

    # Parse: 192/128/320 CBR flagged correctly; VBR is not CBR; durations from frame counts.
    info=page.evaluate("""async ()=>{
      const out=[];
      for(const s of songs){
        const b=await tx('blobs','readonly',st=>st.get(s.id));
        const r=parseMp3(new Uint8Array(b.buf||b));
        out.push({id:s.id,title:s.title,cbr:!!(r&&r.cbr),br:r&&r.br,dur:r&&+r.dur.toFixed(3),frames:r&&r.frames,sr:r&&r.sr});
      }
      return out;
    }""")
    for i,row in enumerate(info):
        print('parse',row)
        assert row['frames']>=3 and 2.7<row['dur']<3.3,(i,row)
        if i<N192:
            assert row['cbr'] is True and row['br']==192000,(i,row)
        elif row['title']=='cbr128':
            assert row['cbr'] is True and row['br']==128000,row
        elif row['title']=='cbr320':
            assert row['cbr'] is True and row['br']==320000,row
        else:
            assert row['title']=='vbr' and row['cbr'] is False,row

    def chunk_at(i):
        return page.evaluate("""async (i)=>{
          const ch=await buildChunk(i,0);
          if(!ch)return {n:0};
          const ids=ch.segs.map(g=>g.id), br=ch.segs.map(g=>songs[g.i].title);
          const total=ch.total, count=ch.count, url=ch.url;
          // Decode each segment start and compare to that song's expected identity.
          const b=await (await fetch(ch.url)).blob();
          const xf=new Uint8Array(await b.slice(0,8).arrayBuffer());
          const ac=new OfflineAudioContext(2,44100,44100);
          const ab=await ac.decodeAudioData(await b.arrayBuffer());
          const ch0=ab.getChannelData(0),sr=ab.sampleRate;
          const hzAt=t=>{
            const s=Math.max(0,Math.floor(t*sr)),e=Math.min(ch0.length,s+Math.floor(0.35*sr));
            let z=0,last=ch0[s]||0;for(let i=s+1;i<e;i++){const v=ch0[i];if(v>=0&&last<0)z++;last=v}
            return +(z*(sr/(e-s))).toFixed(1);
          };
          const at=ch.segs.map(g=>({i:g.i,id:g.id,start:+g.start.toFixed(3),dur:+g.dur.toFixed(3),hz:hzAt(g.start+0.3)}));
          const header=new Uint8Array(await b.slice(0,800).arrayBuffer());
          const h=hdr(header,0),side=4+(h.v===3?(h.mode===3?17:32):(h.mode===3?9:17));
          const tag=String.fromCharCode(...header.slice(side,side+4));
          const view=new DataView(header.buffer);
          const xing={tag,br:h.br,frames:view.getUint32(side+8),bytes:view.getUint32(side+12),size:b.size};
          revokeUnused(ch.url);
          return {n:count,total,ids,at,xing};
        }""",i)

    c0=chunk_at(0)
    print('chunk0',c0['n'],c0['xing'],[round(x['hz']) for x in c0['at']])
    assert c0['n']==12,c0
    assert c0['ids']==[f'id{i}' for i in range(12)],c0['ids']
    assert c0['xing']['tag']=='Xing' and c0['xing']['br']==192000,c0['xing']
    assert c0['xing']['bytes']==c0['xing']['size'],c0['xing']
    for j,seg in enumerate(c0['at']):
        assert near(seg['hz'],TONES[j]),('boundary',j,seg,TONES[j])
        assert abs(seg['start']-j*seg['dur'])<0.15 or abs(seg['start']-c0['at'][j-1]['start']-c0['at'][j-1]['dur'])<0.08,(j,seg)

    c12=chunk_at(12)
    print('chunk12',c12['n'],c12['ids'])
    assert c12['n']==10 and c12['ids']==[f'id{i}' for i in range(12,22)],c12
    for j,seg in enumerate(c12['at']):
        assert near(seg['hz'],TONES[12+j]),('boundary12',j,seg)

    for i,label in ((22,'128'),(23,'320'),(24,'vbr')):
        n=chunk_at(i)['n']
        print('chunk',label,n)
        assert n==0,('mixed/VBR must not join with a different next song',label,n)

    # Playback: join the first 12, check title vs decoded tone at several currentTimes.
    page.evaluate("()=>load(0,true,0,'join')")
    page.wait_for_function('C && C.count===12 && !loadJob && mediaReady',timeout=30000)
    def check_at(song_i,label):
        page.evaluate("i=>{const g=C.segs.find(s=>s.i===i);seekAbs(g.start+0.45);updateProgress()}",song_i)
        page.wait_for_timeout(80)
        # Decode the attached blob at the element's currentTime (the clock used for the title).
        s=page.evaluate(AT, page.evaluate('audio.currentTime'))
        print(label,s)
        assert s.get('err') is None,(label,s)
        assert s['mode']=='join' and s['cur']==song_i and s['segI']==song_i,(label,'title/seg',s)
        assert files[song_i][0] in s['title'],(label,'title text',s)
        assert '連結' in s['diag'] and files[song_i][0] in s['diag'],(label,'diag',s)
        assert near(s['hz'],TONES[song_i]),(label,'decoded tone at currentTime',s,TONES[song_i])
        return s

    s=check_at(0,'t0')
    assert '12曲' in (s['msg'] or ''),s
    check_at(1,'t1')
    check_at(5,'t5')
    check_at(11,'t11')
    # After the first chunk's last song ends, joining resumes at song 12 (same 192k CBR).
    page.evaluate("()=>seekAbs(C.segs[C.segs.length-1].start+C.segs[C.segs.length-1].dur-0.35)")
    page.evaluate('()=>{if(audio.paused)startAudio()}')
    page.wait_for_function('!loadJob && mediaReady && cur===12',timeout=20000)
    s=page.evaluate(AT, page.evaluate('Math.max(0.3, audio.currentTime)'))
    # After a src swap, measure at 0.3s into the new chunk (start of song 12).
    if s.get('mode')!='join' or s.get('cur')!=12 or not near(s.get('hz'),TONES[12]):
        page.evaluate("()=>{if(C)seekAbs(C.segs[0].start+0.45);else seekAbs(0.45);updateProgress()}")
        page.wait_for_timeout(80)
        s=page.evaluate(AT, page.evaluate('audio.currentTime'))
    print('after first cap',s)
    assert s['cur']==12 and files[12][0] in s['title'] and near(s['hz'],TONES[12]),s
    if s['mode']=='join':
        assert s['firstI']==12 and s['count']==10,s

    # Shuffle + repeat-all: a known permutation of the 22 CBR-192 songs, then the others.
    page.evaluate("""()=>{
      S.shuffle=true;S.repeat='all';
      const rest=[22,23,24];
      order=[7,0,15,3,18,1,11,4,20,8,2,16,9,5,19,6,21,10,14,12,17,13].concat(rest);
      document.getElementById('shuffle').classList.add('on');
      document.getElementById('repeat').textContent='🔁 全曲リピート';
    }""")
    # Start join at order[0] === 7; first 12 of this order are all 192k, so they join.
    page.evaluate("()=>load(7,true,0,'join')")
    page.wait_for_function('C && C.count===12 && !loadJob && mediaReady && cur===7',timeout=30000)
    order=page.evaluate('order.slice(0,12)')
    print('shuffle order head',order)
    assert order[0]==7 and len(order)==12
    for k in (0,3,6,11):
        song_i=order[k]
        page.evaluate("k=>{const g=C.segs[k];seekAbs(g.start+0.45);updateProgress()}",k)
        page.wait_for_timeout(80)
        s=page.evaluate(AT, page.evaluate('audio.currentTime'))
        print('shuffle',k,song_i,s)
        assert s['mode']=='join' and s['cur']==song_i and s['segI']==song_i,(k,s)
        assert files[song_i][0] in s['title'] and '連結' in s['diag'],(k,s)
        assert near(s['hz'],TONES[song_i]),('shuffle tone',k,s,TONES[song_i])
    # Repeat-all: after this 12-song chunk, the next 192k songs (order 12..) join; not the 128k.
    page.evaluate("()=>seekAbs(C.total-0.35)")
    page.evaluate('()=>{if(audio.paused)startAudio()}')
    nxt=page.evaluate('order[12]')
    page.wait_for_function(f'!loadJob && mediaReady && cur==={nxt}',timeout=20000)
    page.evaluate("()=>{if(C)seekAbs(C.segs[0].start+0.45);else seekAbs(0.45);updateProgress()}")
    page.wait_for_timeout(80)
    s=page.evaluate(AT, page.evaluate('audio.currentTime'))
    print('repeat-all next chunk',nxt,s)
    assert s['cur']==nxt and near(s['hz'],TONES[nxt]),s
    if s['mode']=='join':
        assert s['firstI']==nxt and s['count']==10,s  # remaining 10 of the 22

    assert not errors,errors
    print('PASS v25: same-CBR 192k songs join (capped at 12) with Xing bitrate 192k; decoded tone at several currentTimes matches the displayed title (including shuffle+repeat-all); mixed 128/320/VBR do not share a chunk; after the cap, joining resumes on the next same-CBR run')
    browser.close()
server.shutdown()
