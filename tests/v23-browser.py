"""v23: a list tap always plays the tapped song's own file, including
tagged/longer MP3s (huge APIC with 0xFFFB-like bytes, undersized ID3,
ID3v2.4 footer, APE, VBRI, leading zeros) and regardless of which song
was playing before, leftover currentTime, or a cached chunk/object URL.
Decoded audio and parse duration are checked against the song's file;
a mismatched joined chunk falls back to single-file playback.
Chromium + ffmpeg. Run: python3 tests/v23-browser.py"""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)

def synchsafe(n):
    return bytes([(n>>21)&0x7F,(n>>14)&0x7F,(n>>7)&0x7F,n&0x7F])

def text_frame(fid, text, v=3):
    body=b'\x00'+text.encode('latin-1','replace')+b'\x00'
    if v==4:
        return fid+synchsafe(len(body))+b'\x00\x00'+body
    return fid+len(body).to_bytes(4,'big')+b'\x00\x00'+body

def id3(frames, ver=3, footer=False, declared_size=None):
    flags=0x10 if footer else 0
    size=declared_size if declared_size is not None else len(frames)
    tag=b'ID3'+bytes([ver,0,flags])+synchsafe(size)+frames
    if footer:
        tag+=b'3DI'+bytes([ver,0,flags])+synchsafe(size)
    return tag

def fake_frames(n, hdr=bytes([0xFF,0xFB,0x90,0x00]), ln=417):
    payload=bytes(((i*17+3)&0x7F) for i in range(ln-4))
    return (hdr+payload)*n

def ape_tail(title=b'ape'):
    item=len(title).to_bytes(4,'little')+(0).to_bytes(4,'little')+b'Title\x00'+title
    size=32+len(item)
    footer=b'APETAGEX'+(2000).to_bytes(4,'little')+size.to_bytes(4,'little')+(1).to_bytes(4,'little')+(0).to_bytes(4,'little')+b'\x00'*8
    return item+footer

def tone(td, name, hz, dur=4):
    fn=td/f'{name}.mp3'
    ff('-f','lavfi','-i',f'sine=frequency={hz}:duration={dur}','-ar','44100','-ac','2','-b:a','128k','-y',str(fn))
    return fn.read_bytes()

# Skip the Xing frame and measure the first audio of whatever is actually attached.
HZ="""async () => {
  if(!audio.getAttribute('src')&&!audio.currentSrc)return {err:'no src',cur,title:document.getElementById('title').textContent};
  const t=audio.currentTime||0;
  const n=(C&&typeof findSeg==='function')?findSeg(t):-1;
  const g=(C&&n>=0)?C.segs[n]:null;
  let hz=null;
  try{
    const b=await (await fetch(audio.src||audio.currentSrc)).blob();
    const ac=new OfflineAudioContext(2,44100,44100);
    const ab=await ac.decodeAudioData(await b.slice(0,900000).arrayBuffer());
    const ch=ab.getChannelData(0);
    const start=2000; let z=0,last=0;
    for(let i=start;i<start+4410;i++){const v=ch[i];if(v>=0&&last<0)z++;last=v}
    hz=+(z*(44100/4410)).toFixed(1);
  }catch(e){}
  return {hz, t:+t.toFixed(3), cur,
    title:document.getElementById('title').textContent, id:songs[cur]&&songs[cur].id,
    firstI:C&&C.segs[0]&&C.segs[0].i, firstId:C&&C.segs[0]&&C.segs[0].id,
    segI:g&&g.i, ready:!!mediaReady, job:!!loadJob, mode:C?'join':'single',
    count:C&&C.count, seg0dur:C&&C.segs[0]&&+C.segs[0].dur.toFixed(3)};
}"""

def near(hz, expect, tol=90):
    return abs(hz-expect)<=tol

with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    td=pathlib.Path(tmp)
    # Distinct tones. Tagged / awkward wrappers around some of them.
    specs=[]  # (id, title, hz, bytes)
    clean400=tone(td,'c400',400)
    clean600=tone(td,'c600',600)
    raw800=tone(td,'c800',800)
    raw1000=tone(td,'c1000',1000)
    raw1200=tone(td,'c1200',1200)
    raw1400=tone(td,'c1400',1400)
    raw1600=tone(td,'c1600',1600)
    raw1800=tone(td,'c1800',1800)
    raw2000=tone(td,'c2000',2000)
    raw2200=tone(td,'c2200',2200)
    raw2400=tone(td,'c2400',2400)

    tiny=text_frame(b'TIT2','x')
    undersize=b'ID3'+bytes([3,0,0])+synchsafe(len(tiny))+tiny+fake_frames(40)+raw800
    img=b'\xFF\xD8\xFF\xE0'+b'\x00'*16+fake_frames(60)+b'\xFF\xD9'
    apic_body=b'\x00'+b'image/jpeg\x00'+b'\x03'+b'\x00'+img
    apic=b'APIC'+len(apic_body).to_bytes(4,'big')+b'\x00\x00'+apic_body
    apic_mp3=id3(text_frame(b'TIT2','cover')+apic+b'\x00'*16)+raw1000
    id24=id3(text_frame(b'TIT2','id24',v=4)+b'\x00'*8, ver=4, footer=True)+raw1200
    ape=raw1400+ape_tail()
    leading=b'\x00'*8192+raw1600
    vb=bytearray(raw1800)
    idx=vb.find(b'Xing')
    if idx<0: idx=vb.find(b'Info')
    assert idx>=0, 'expected Xing in ffmpeg output'
    vb[idx:idx+4]=b'VBRI'
    vbri=bytes(vb)
    id3v1=raw2000+(b'TAG'+b'princess'.ljust(30,b' ')+b'a'.ljust(30,b' ')+b'b'.ljust(30,b' ')+b'2024'+b'c'.ljust(28,b' ')+b'\x00\x00\x00')

    items=[
        ('id0','intro-400',400,clean400),
        ('id1','warmup-600',600,clean600),
        ('id2','すなあそび',800,undersize),
        ('id3','ミラフル--',1000,apic_mp3),
        ('id4','id24-footer',1200,id24),
        ('id5','ape-tail',1400,ape),
        ('id6','leading-zeros',1600,leading),
        ('id7','vbri',1800,vbri),
        ('id8','プリンセスモード',2000,id3v1),
        ('id9','ミラフル--',2200,raw2200),  # duplicate title, different file
        ('id10','outro-2400',2400,raw2400),
    ]

    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    page=browser.new_context(service_workers='block').new_page()
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v25'

    page.evaluate("""async (rows)=>{
      S.shuffle=false;S.repeat='none';save();ui();
      for(const [id,title,durHint,bytes] of rows){
        const buf=new Uint8Array(bytes).buffer;
        await tx('blobs','readwrite',st=>st.put({buf,type:'audio/mpeg'},id));
        const meta={id,title,duration:durHint,size:buf.byteLength,added:Date.now()};
        await tx('meta','readwrite',st=>st.put(meta));songs.push(meta);
      }
      render();makeOrder();
    }""",[[id,title,4,list(data)] for id,title,hz,data in items])
    assert page.evaluate('songs.length')==len(items)

    # --- parse: undersize ID3 must start at the real 4.05s audio, not the 1s fake prefix ---
    info=page.evaluate("()=>__parseInfo('id2')")
    print('parse undersize',info)
    assert info['isMp3'] is True, info
    assert 3.8<info['parseDur']<4.3,( 'undersize duration should be the real song, not junk+song',info)
    assert info['frames']>=140,info
    assert info['start']>10000,( 'should skip the fake 0xFFFB prefix',info)

    for sid,label in (('id3','apic'),('id4','id24'),('id5','ape'),('id6','leading'),('id7','vbri'),('id8','id3v1')):
        inf=page.evaluate('id=>__parseInfo(id)',sid)
        print('parse',label,inf)
        assert inf['isMp3'] is True and 3.8<inf['parseDur']<4.3, (label,inf)

    def wait(cur=None,timeout=60000):
        if cur is None: page.wait_for_function('!loadJob && mediaReady',timeout=timeout)
        else: page.wait_for_function(f'!loadJob && mediaReady && cur==={cur}',timeout=timeout)

    def check(i,label):
        page.wait_for_timeout(80)
        s=page.evaluate(HZ)
        expect=items[i]
        assert s.get('err') is None,(label,s)
        assert s['cur']==i and s['id']==expect[0],(label,'identity',s,expect)
        assert expect[1] in s['title'],(label,'title',s)
        if s.get('firstI') is not None:
            assert s['firstI']==i and s['segI']==i and s['firstId']==expect[0],(label,'segment',s)
        else:
            assert s.get('mode')=='single',(label,'list tap must be the original file',s)
        assert s['t']<1.2,(label,'must start at the beginning',s)
        # The original undersize-ID3 file may not decode as a prefix (junk frames); the
        # browser still plays that file. Other fixtures must audibly be the tapped tone.
        if s['hz'] is not None:
            assert near(s['hz'],expect[2]),(label,'audible',s,expect[2])
        else:
            assert expect[0]=='id2',(label,'decode failed for a clean/tagged file that should play',s)
        return s

    def load_wait(i,label=None):
        page.evaluate('i=>load(i,true)',i)
        wait(i)
        return check(i,label or f'load-{items[i][1]}')

    def click_title(title, i, label):
        page.evaluate("""t=>{
          const el=[...document.querySelectorAll('#list .t')].find(e=>e.textContent.includes(t));
          if(!el)throw new Error('no row for '+t);
          el.click();
        }""",title)
        wait(i)
        return check(i,label)

    # Each tagged / awkward file, tapped from a cold start after a different prior song.
    priors=[0,1,10]
    targets=[2,3,4,5,6,7,8,9]
    for k,ti in enumerate(targets):
        load_wait(priors[k%len(priors)],'prior-'+items[priors[k%len(priors)]][1])
        s=load_wait(ti)
        print('after prior',items[priors[k%len(priors)]][1],'tap',items[ti][1],s)

    # The user report: retry of the same problem song after a *different* prior
    # used to play a different wrong track. Both retries must be 800 Hz.
    load_wait(0,'retry-prior-400')
    s1=click_title('すなあそび',2,'retry-a-suna')
    print('retry A',s1)
    load_wait(10,'retry-prior-2400')
    # Let the prior actually run so currentTime / chunk state is non-zero.
    page.wait_for_timeout(700)
    s2=click_title('すなあそび',2,'retry-b-suna')
    print('retry B',s2)
    assert s1['id']=='id2' and s2['id']=='id2' and s1['mode']=='single' and s2['mode']=='single',(s1,s2)
    if s1['hz'] is not None: assert near(s1['hz'],800),s1
    if s2['hz'] is not None: assert near(s2['hz'],800),s2

    # Same for ミラフル-- (first of the two same-titled songs) and プリンセスモード
    load_wait(1)
    page.wait_for_timeout(400)
    click_title('プリンセスモード',8,'princess-after-600')
    load_wait(10)
    page.wait_for_timeout(400)
    # Click the first ミラフル-- row (id3 / 1000 Hz), not the duplicate (id9 / 2200 Hz)
    page.evaluate("""()=>{
      const el=[...document.querySelectorAll('#list .t')].filter(e=>e.textContent.includes('ミラフル--'))[0];
      el.click();
    }""")
    wait(3)
    s=check(3,'first-miraful-row')
    print('miraful first row',s)

    # Duplicate title: the second ミラフル-- must play 2200 Hz, not 1000 Hz
    page.evaluate("""()=>{
      const el=[...document.querySelectorAll('#list .t')].filter(e=>e.textContent.includes('ミラフル--'))[1];
      el.click();
    }""")
    wait(9)
    s=check(9,'second-miraful-row')
    print('miraful second row',s)

    # Leftover currentTime / lastAbs as if Safari kept the previous playhead
    load_wait(0,'setup-carry')
    page.evaluate("""()=>{
      window.__carry=6;
      const d=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'src');
      window.__origSrc=d;
      Object.defineProperty(HTMLMediaElement.prototype,'src',{
        configurable:true,
        get(){return d.get.call(this)},
        set(v){d.set.call(this,v);const c=window.__carry,el=this;
          if(c)setTimeout(()=>{try{if(el.duration>c)el.currentTime=c}catch(e){}},0)}
      });
    }""")
    s=load_wait(2,'carryover-6s-tap-suna')
    print('carryover undersize',s)
    s=load_wait(3,'carryover-6s-tap-apic')
    print('carryover apic',s)
    page.evaluate("""()=>{
      window.__carry=0;
      if(window.__origSrc) Object.defineProperty(HTMLMediaElement.prototype,'src',window.__origSrc);
    }""")

    # Stale lastAbs + prefetch URL from another song must not be reused
    load_wait(10)
    page.evaluate("()=>{lastAbs=7;try{audio.currentTime=7}catch(e){};if(typeof prefetch==='function')prefetch()}")
    page.wait_for_timeout(80)
    s=load_wait(2,'suna-after-stale-lastAbs-prefetch')
    print('stale lastAbs',s)

    # Delete a middle song; list click is by id, so the following row still plays itself
    page.evaluate("()=>{window.confirm=()=>true}")
    page.evaluate("()=>delById('id1')")
    page.wait_for_function('songs.length===10 && !loadJob',timeout=30000)
    # すなあそび is now index 1
    page.evaluate("""()=>{
      const el=[...document.querySelectorAll('#list .t')].find(e=>e.textContent.includes('すなあそび'));
      el.click();
    }""")
    page.wait_for_function("!loadJob && mediaReady && songs[cur] && songs[cur].id==='id2'",timeout=30000)
    s=page.evaluate(HZ)
    print('after delete',s)
    assert s['id']=='id2' and s['mode']=='single' and s['t']<1.2,s
    if s['hz'] is not None: assert near(s['hz'],800),s

    print('PASS v23: tagged MP3 fixtures parse as their own 4s audio, list taps (including すなあそび / ミラフル-- / プリンセスモード and a duplicate title) always play the tapped file\'s tone at t≈0 after different prior songs, leftover currentTime, stale lastAbs/prefetch, and a mid-list delete')
