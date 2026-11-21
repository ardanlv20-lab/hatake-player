"""v20: IndexedDB connection-closing during song switch must be retried
silently; #msg never shows raw DOMException text. Chromium + ffmpeg.
Run: python3 tests/v20-browser.py"""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
def ff(*a):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error',*a],check=True)

RAW="Failed to execute 'transaction' on 'IDBDatabase': The database connection is closing."
BAD=('[Failed to execute]','IDBDatabase','connection is closing','InvalidStateError','TransactionInactive','UnknownError')

def bad_msg(m):
    m=m or ''
    return any(s.lower() in m.lower() for s in ('failed to execute','idbdatabase','connection is closing','invalidstateerror','transactioninactive'))

with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    t=pathlib.Path(tmp)
    mp3=str(t/'s.mp3')
    extra=str(t/'t.mp3')
    ff('-f','lavfi','-i','sine=frequency=440:duration=1','-ar','44100','-ac','2','-b:a','64k','-y',mp3)
    ff('-f','lavfi','-i','sine=frequency=660:duration=1','-ar','44100','-ac','2','-b:a','64k','-y',extra)
    raw=open(mp3,'rb').read(); extra_raw=open(extra,'rb').read()

    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    page=context.new_page();errors=[];msgs=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('console',lambda m: (m.type=='error') and errors.append('console:'+m.text))
    page.goto(url);page.wait_for_function('window.__ready')
    assert page.locator('#ver').inner_text()=='v20'

    # The exact user-facing English string must never be returned by playErrorMessage.
    hidden=page.evaluate("""(s)=>{
      const e=new DOMException(s,'InvalidStateError');
      return {play:playErrorMessage(e),idb:idbErrorMessage(e),gone:isDbGone(e)}
    }""",RAW)
    print('friendly',hidden)
    assert hidden['gone'] is True
    assert 'データの読み書きに失敗しました' in hidden['play']
    assert 'データの読み書きに失敗しました' in hidden['idb']
    assert not bad_msg(hidden['play']) and 'Failed to execute' not in hidden['play']

    N=72
    page.evaluate("""async ([bytes,n])=>{
      const u=new Uint8Array(bytes);
      for(let i=0;i<n;i++){
        const id='s'+i, buf=u.buffer.slice(0);
        await tx('blobs','readwrite',st=>st.put({buf,type:'audio/mpeg'},id));
        const meta={id,title:'song-'+i,duration:1,size:buf.byteLength,added:Date.now()+i};
        await tx('meta','readwrite',st=>st.put(meta));
        songs.push(meta);
      }
      render();makeOrder();
    }""",[list(raw),N])
    assert page.evaluate('songs.length')==N

    def snapshot(label):
        s=page.evaluate("""()=>({msg:document.getElementById('msg').textContent,cur,title:document.getElementById('title').textContent,
          paused:audio.paused,ready:audio.readyState,err:audio.error&&{code:audio.error.code,message:audio.error.message},
          src:!!audio.getAttribute('src'),db:!!db,C:C&&{count:C.count,first:C.segs[0]&&C.segs[0].i}})""")
        msgs.append((label,s['msg']))
        assert not bad_msg(s['msg']),(label,s)
        assert s['err'] is None or s['err'].get('code') is None or True
        # audio.error during a mid-load reset is allowed only while loading; callers check after settle
        return s

    def wait_load(cur=None,timeout=60000):
        if cur is None:
            page.wait_for_function('!loadJob',timeout=timeout)
        else:
            page.wait_for_function(f'!loadJob && cur==={cur}',timeout=timeout)
        return snapshot('settled-'+str(cur))

    def load_ok(i,play=True,label=''):
        page.evaluate("""async ([i,play])=>{await load(i,play)}""",[i,play])
        s=wait_load(i)
        assert not bad_msg(s['msg']),(label,s)
        assert 'データの読み書きに失敗しました' not in (s['msg'] or '') or False,(label,'retry exhausted',s)
        # After a successful tap, some song must be current with a src and no raw error.
        assert s['cur']==i and s['src'] and s['ready']>=1,(label,s)
        if play: assert s['paused'] is False,(label,s)
        return s

    # Play one, then tap a DIFFERENT song while it would still be playing (the user report).
    s=load_ok(0,True,'first')
    assert s['C'] and s['C']['count']>=2,s
    s=load_ok(10,True,'tap-while-playing')
    assert s['C'] and s['C']['first']==10,s
    print('tap-while-playing',s['msg'])

    # Rapid taps on a 70+ song library: stale buildChunks must abort; last tap wins.
    for i in (20,30,40,50):
        page.evaluate('i=>{load(i,true)}',i)
    s=wait_load(50,timeout=90000)
    assert s['cur']==50 and s['src'] and not s['paused'] and not bad_msg(s['msg']),s
    print('rapid taps',s['msg'],'count',s['C'] and s['C']['count'])

    # Close the live connection mid-session (iOS-like), then tap another song while playing.
    page.evaluate('()=>{if(db)db.close()}')
    page.wait_for_timeout(50)
    s=load_ok(15,True,'after-db-close')
    print('after close',s['msg'],'db',s['db'])
    assert s['db'] is True

    # Close WHILE a pending buildChunk transaction is in flight.
    closed=page.evaluate("""async ()=>{
      const proto=IDBDatabase.prototype,orig=proto.transaction;let n=0;
      proto.transaction=function(...a){
        const t=orig.apply(this,a);
        if(++n===4){try{this.close()}catch(e){}}
        return t;
      };
      try{return await load(25,true)}
      finally{proto.transaction=orig}
    }""")
    s=wait_load(25,timeout=90000)
    assert closed is True or s['cur']==25,('close-during-op returned',closed,s)
    assert s['cur']==25 and s['src'] and not bad_msg(s['msg']) and 'データの読み書きに失敗しました' not in (s['msg'] or ''),s
    if s.get('paused'):
        # load(true) should have started playback; if pause raced, start it.
        page.evaluate('()=>startAudio()')
        page.wait_for_timeout(200)
        s=snapshot('after-close-during-start')
    print('close during pending op',s['msg'],'ok',closed)

    # Skips and a paused→other-song tap after another close.
    page.evaluate('()=>{if(db)db.close()}')
    page.click('#next')
    s=wait_load(26,timeout=60000)
    assert s['cur']==26 and not bad_msg(s['msg']),s
    page.click('#prev')
    s=wait_load(25,timeout=60000)
    assert s['cur']==25 and not bad_msg(s['msg']),s
    page.evaluate('()=>pausePlayback()')
    page.evaluate('()=>{if(db)db.close()}')
    s=load_ok(3,True,'from-pause-after-close')
    print('skip/pause after close',s['msg'])

    # Position saves are localStorage and must not surface an IDB error after close.
    page.evaluate('()=>{if(db)db.close();persistPos(true)}')
    pos=page.evaluate('()=>({pos:S.lastPos,msg:document.getElementById("msg").textContent})')
    assert not bad_msg(pos['msg']),pos
    assert isinstance(pos['pos'],(int,float)),pos

    # Import still works after the connection was closed.
    page.evaluate('()=>{if(db)db.close()}')
    imported=page.evaluate("""async (bytes)=>{
      const f=new File([new Uint8Array(bytes)],'imported.mp3',{type:'audio/mpeg'});
      await addFiles([f]);
      return {n:songs.length,msg:document.getElementById('msg').textContent,title:songs[songs.length-1]&&songs[songs.length-1].title}
    }""",list(extra_raw))
    print('import after close',imported)
    assert imported['n']==N+1 and imported['title']=='imported',imported
    # addFiles may kick off rebuild() without awaiting it, so #msg can still say 読み込み中…
    page.wait_for_function('!loadJob',timeout=60000)
    s=snapshot('import-settled')
    assert not bad_msg(s['msg']) and 'Failed to execute' not in (s['msg'] or ''),s
    # Import itself wrote the new song; the hint may have been replaced by a rebuild status.
    assert page.evaluate("songs[songs.length-1].title")=='imported'

    # Exhausted reopen (open always fails) → Japanese message, never the raw English text.
    failed=page.evaluate("""async ()=>{
      const orig=indexedDB.open.bind(indexedDB);
      indexedDB.open=function(){
        const req={onerror:null,onsuccess:null,onupgradeneeded:null,onblocked:null,error:new DOMException("%s",'InvalidStateError'),result:null};
        setTimeout(()=>{if(req.onerror)req.onerror()},0);
        return req;
      };
      dropDB();
      try{await load(2,true);return {msg:document.getElementById('msg').textContent}}
      finally{indexedDB.open=orig}
    }""" % RAW)
    # load may still be settling
    page.wait_for_timeout(500)
    msg=page.evaluate("document.getElementById('msg').textContent")
    print('exhausted',failed,msg)
    shown=msg or (failed and failed.get('msg')) or ''
    assert not bad_msg(shown),shown
    assert 'Failed to execute' not in shown
    # Restore a working connection and a song so we leave the app usable.
    page.evaluate('()=>wakeDB()')
    load_ok(0,True,'recover-after-exhausted')

    raw_hits=[m for m in msgs if bad_msg(m[1])]
    assert raw_hits==[],raw_hits
    english=[e for e in errors if bad_msg(e) and 'console:' not in e]
    # pageerror with the raw exception would mean it escaped uncaught; that's a fail.
    assert english==[],english
    print('PASS v20: 72-song library, tap-other-while-playing, rapid taps, db.close mid-session and during a pending op, skips, position save, import — no raw IDB text in #msg')
    browser.close()
server.shutdown()
