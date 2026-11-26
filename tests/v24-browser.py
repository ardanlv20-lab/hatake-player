"""v24: a list tap always plays that song's original file (never a joined
chunk, never a seek), even after another song, mid-join playback, leftover
currentTime, or rapid taps. After the tapped file ends, following songs join.
The リセット button clears playback state (not the library) so the next tap
plays the chosen song. Chromium + ffmpeg. Run: python3 tests/v24-browser.py"""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)

TONES=[400,700,1000,1300,1600,1900,2200]
HZ="""async () => {
  if(!audio.getAttribute('src')&&!audio.currentSrc)return {err:'no src',cur,title:document.getElementById('title').textContent};
  const b=await (await fetch(audio.src||audio.currentSrc)).blob();
  const ac=new OfflineAudioContext(2,44100,44100);
  const ab=await ac.decodeAudioData(await b.slice(0,800000).arrayBuffer());
  const ch=ab.getChannelData(0);
  const start=2000; let z=0,last=0;
  for(let i=start;i<start+4410;i++){const v=ch[i];if(v>=0&&last<0)z++;last=v}
  const t=audio.currentTime||0;
  const n=(C&&typeof findSeg==='function')?findSeg(t):-1;
  const g=(C&&n>=0)?C.segs[n]:null;
  const diag=(document.getElementById('diag')||{}).textContent||'';
  return {hz:+(z*(44100/4410)).toFixed(1), t:+t.toFixed(3), cur,
    title:document.getElementById('title').textContent, id:songs[cur]&&songs[cur].id,
    firstI:C&&C.segs[0]&&C.segs[0].i, segI:g&&g.i, ready:!!mediaReady, job:!!loadJob,
    mode:C?'join':'single', count:C&&C.count, diag, srcKind:C?'連結':'単曲',
    msg:document.getElementById('msg').textContent, paused:audio.paused};
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

    # A realistic tagged MP3: correctly sized ID3v2 + APIC filled with 0xFFFB-like
    # bytes, then a 1150 Hz song. The original file (used for a list tap) must
    # still decode as that tone from the start.
    raw_tag=str(td/'tag.mp3')
    ff('-f','lavfi','-i','sine=frequency=1150:duration=3','-ar','44100','-ac','2','-b:a','96k','-y',raw_tag)
    raw=open(raw_tag,'rb').read()
    def synchsafe(n):
        return bytes([(n>>21)&0x7F,(n>>14)&0x7F,(n>>7)&0x7F,n&0x7F])
    fake=bytes([0xFF,0xFB,0x90,0x00])+bytes(((i*17+3)&0x7F) for i in range(413))
    img=b'\xff\xd8\xff\xe0'+b'\x00'*8+fake*40+b'\xff\xd9'
    apic_body=b'\x00'+b'image/jpeg\x00'+b'\x03'+b'\x00'+img
    apic=b'APIC'+len(apic_body).to_bytes(4,'big')+b'\x00\x00'+apic_body
    tit=b'\x00suna\x00'
    frames=b'TIT2'+len(tit).to_bytes(4,'big')+b'\x00\x00'+tit+apic
    tagged=b'ID3'+bytes([3,0,0])+synchsafe(len(frames))+frames+raw

    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    page=browser.new_context(service_workers='block').new_page()
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v26'
    assert page.locator('#reset').inner_text()=='リセット'

    rows=[[f'id{i}',f'song-{i}',3,list(b)] for i,b in enumerate(blobs)]
    rows.append(['idtag','すなあそび',3,list(tagged)])
    page.evaluate("""async (rows)=>{
      S.shuffle=false;S.repeat='none';save();ui();
      for(const [id,title,dur,bytes] of rows){
        const buf=new Uint8Array(bytes).buffer;
        await tx('blobs','readwrite',st=>st.put({buf,type:'audio/mpeg'},id));
        const meta={id,title,duration:dur,size:buf.byteLength,added:Date.now()};
        await tx('meta','readwrite',st=>st.put(meta));songs.push(meta);
      }
      render();makeOrder();
    }""",rows)
    N=len(rows)
    assert page.evaluate('songs.length')==N

    def wait(cur=None,timeout=60000):
        if cur is None: page.wait_for_function('!loadJob && mediaReady',timeout=timeout)
        else: page.wait_for_function(f'!loadJob && mediaReady && cur==={cur}',timeout=timeout)

    def check(i,label,expect_join=False):
        page.wait_for_timeout(80)
        s=page.evaluate(HZ)
        assert s.get('err') is None,(label,s)
        assert s['cur']==i and s['id']==rows[i][0],(label,'identity',s)
        assert rows[i][1] in s['title'],(label,'title',s)
        assert s['t']<1.2,(label,'must start at the beginning',s)
        hz=1150 if rows[i][0]=='idtag' else TONES[i]
        assert near(s['hz'],hz),(label,'audible',s,hz)
        if s['mode']=='join' or expect_join:
            assert s['mode']=='join' and s['firstI']==i and '連結' in s['diag'] and rows[i][1] in s['diag'],(label,'join must start at this song',s)
        else:
            assert s['mode']=='single' and s['firstI'] is None,(label,'incompatible fallback should be the original file',s)
            assert '単曲' in s['diag'] and rows[i][1] in s['diag'],(label,'diag',s)
        return s

    def load_wait(i,label=None,how=None):
        if how: page.evaluate('([i,h])=>load(i,true,0,h)',[i,how])
        else: page.evaluate('i=>load(i,true)',i)
        wait(i)
        return check(i,label or f'load-{rows[i][1]}',expect_join=(how=='join'))

    def click_title(title,i,label):
        page.evaluate("""t=>{
          const el=[...document.querySelectorAll('#list .t')].find(e=>e.textContent.includes(t));
          if(!el)throw new Error('no row for '+t);
          el.click();
        }""",title)
        wait(i)
        return check(i,label)

    # Cold tap and a tap while another song is playing.
    s=load_wait(0,'cold-0')
    print('cold',s)
    s=click_title('song-3',3,'tap-3-while-0-plays')
    print('tap while playing',s)

    # Mid-joined-stream: let song 0 end so following songs join, then tap another title.
    load_wait(0,'setup-join-from-0')
    page.evaluate('()=>seekSong(Math.max(0,(audio.duration||3)-0.4))')
    page.wait_for_function('C && !loadJob && mediaReady && cur===1',timeout=20000)
    s=page.evaluate(HZ)
    print('auto join after song 0',s)
    assert s['mode']=='join' and s['cur']==1 and s['firstI']==1 and near(s['hz'],TONES[1]),s
    assert '連結' in s['diag'] and 'song-1' in s['diag'],s
    # Still in the joined stream: tap a later title — must leave the chunk and play that file.
    s=click_title('song-5',5,'tap-5-from-mid-join')
    print('tap from mid-join',s)

    # Rapid taps: last one wins, still the original file.
    for i in (1,6,2):
        page.evaluate('i=>load(i,true)',i)
    wait(2,timeout=60000)
    s=check(2,'rapid-last-is-2')
    print('rapid',s)

    # Leftover currentTime as if Safari kept the previous playhead.
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
    s=click_title('すなあそび',N-1,'carryover-tagged')
    print('carryover tagged',s)
    page.evaluate("""()=>{
      window.__carry=0;
      if(window.__origSrc) Object.defineProperty(HTMLMediaElement.prototype,'src',window.__origSrc);
    }""")

    # Next/prev also start that song's file at 0 (then will rejoin when it ends).
    load_wait(2,'setup-step')
    page.click('#next');wait(3);s=check(3,'next-button');print('next',s)
    page.click('#prev');wait(2);s=check(2,'prev-button');print('prev',s)

    # After a tapped file ends, joining of the following songs resumes (same src for later tracks).
    load_wait(3,'setup-end-then-join')
    page.evaluate('()=>seekSong(Math.max(0,(audio.duration||3)-0.35))')
    page.wait_for_function('C && !loadJob && mediaReady && cur===4',timeout=20000)
    s=page.evaluate(HZ)
    print('join after tapped song ended',s)
    assert s['mode']=='join' and s['cur']==4 and s['firstI']==4 and near(s['hz'],TONES[4]),s
    src=page.evaluate('audio.src')
    page.evaluate('()=>seekAbs(C.segs[0].start+C.segs[0].dur-0.4)')
    page.wait_for_function('cur===5 && !loadJob && !audio.paused',timeout=20000)
    assert page.evaluate('audio.src')==src,'following songs should stay on the joined src'
    s=page.evaluate(HZ)
    # The blob still starts at song-4 (first of this join); playback has advanced to song-5.
    assert s['mode']=='join' and s['cur']==5 and s['firstI']==4 and s['segI']==5 and near(s['hz'],TONES[4]),s

    # リセット: library stays, playback is torn down; the next tap plays that song.
    n_before=page.evaluate('songs.length')
    page.click('#reset')
    page.wait_for_function("document.getElementById('msg').textContent==='再生をリセットしました' && cur===-1 && !loadJob",timeout=20000)
    st=page.evaluate("""()=>({cur,C:!!C,src:!!audio.getAttribute('src'),n:songs.length,diag:(document.getElementById('diag')||{}).textContent,title:document.getElementById('title').textContent,lastAbs,cache:parseCache.size})""")
    print('after reset',st)
    assert st['C'] is False and st['src'] is False and st['n']==n_before,st
    assert st['title']=='曲を選んでください' and st['lastAbs']==0,st
    s=click_title('song-1',1,'tap-after-reset')
    print('after reset tap',s)
    assert s['cur']==1 and near(s['hz'],TONES[1]) and (s['mode']=='single' or (s['mode']=='join' and s['firstI']==1)),s

    print('PASS v24: list taps (including mid-join, rapid, leftover currentTime, tagged file) start the chosen song at t≈0 (joined from that song when compatible); after a song ends later songs can share one src; リセット clears playback and the next tap plays the chosen song')
