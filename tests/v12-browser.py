"""Background-path regressions (Chromium + ffmpeg, NOT an iPhone lock-screen test).
Run: python3 tests/v12-browser.py. No network services or physical device needed.
"""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    files=[]
    for n,joint in enumerate([0,1,0]):
        path=str(pathlib.Path(tmp)/f'stereo-{n}.mp3')
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                        f'sine=frequency={440+n*80}:duration=2','-af','volume=12dB','-ar','44100','-ac','2',  # loud: kept byte-identical (v15)
                        '-b:a','320k','-joint_stereo',str(joint),'-y',path],check=True)
        files.append(path)
    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    context.add_init_script("""
      // Fake an existing SW controller so controllerchange can be injected on demand.
      const sw=new EventTarget();sw.controller={};sw.register=async()=>({update:async()=>{}});
      Object.defineProperty(navigator,'serviceWorker',{value:sw});
      window.actions={};window.playCalls=0;window.dbCalls=0;
      const register=navigator.mediaSession.setActionHandler.bind(navigator.mediaSession);
      navigator.mediaSession.setActionHandler=(a,f)=>{actions[a]=f;register(a,f)};
      const play=HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play=function(){playCalls++;return play.call(this)};
      sessionStorage.setItem('boots',String(+(sessionStorage.getItem('boots')||0)+1));
    """)
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.wait_for_function('window.__ready')
    page.set_input_files('#file',files)
    page.wait_for_function('songs.length===3 && cur===0 && !loadJob && audio.readyState>=1')
    page.evaluate("""async () => {
      const modes=[];
      for(const song of songs){const b=await tx('blobs','readonly',s=>s.get(song.id));modes.push(parseMp3(new Uint8Array(b.buf)).mode)}
      if(modes[0]!==0||modes[1]!==1)throw Error('Fixture did not change stereo coding mode');
      if(C.segs.length!==3)throw Error('Stereo/joint-stereo unnecessarily split the joined stream');
      window.realTx=tx;tx=(...a)=>{dbCalls++;return realTx(...a)};
      window.realProgress=updateProgress;updateProgress=()=>{};audio.ontimeupdate=null;
      Object.defineProperty(document,'hidden',{configurable:true,value:true});
      document.dispatchEvent(new Event('visibilitychange'));
      await startAudio();stopTicker();
      window.src=audio.src;window.calls=playCalls;window.reads=dbCalls;
      seekAbs(C.segs[1].start-0.15);
    }""")
    page.wait_for_function('audio.currentTime>C.segs[1].start+0.2 && !audio.paused')
    page.evaluate("""() => {
      if(audio.src!==src||playCalls!==calls||dbCalls!==reads)throw Error('Song boundary required JS playback/reload');
      updateProgress=realProgress;updateProgress();
      if(cur!==1)throw Error('Delayed progress did not catch up');
      navigator.serviceWorker.dispatchEvent(new Event('controllerchange'));
      if(!document.getElementById('msg').textContent.includes('更新があります'))throw Error('No deferred update notice');
      window.dispatchEvent(new PageTransitionEvent('pagehide'));
    }""")
    page.wait_for_timeout(400)
    assert page.evaluate("sessionStorage.getItem('boots')")=='1'
    page.evaluate("""() => {
      if(audio.src!==src||audio.paused)throw Error('Update/lifecycle interrupted playing source');
      actions.pause();navigator.serviceWorker.dispatchEvent(new Event('controllerchange'));
    }""")
    page.wait_for_timeout(400)
    assert page.evaluate("sessionStorage.getItem('boots')")=='1'
    page.evaluate("""async () => {
      if(audio.src!==src||!audio.paused)throw Error('Update lost paused remote-control source');
      S.repeat='all';order=[2,0,1];await load(0,false);
      if(!C.loop||!audio.loop||C.segs.map(g=>g.i).join()!=='0,1,2')throw Error('Shuffled repeat did not build one native loop');
      updateProgress=()=>{};await startAudio();stopTicker();
      src=audio.src;calls=playCalls;reads=dbCalls;seekAbs(audio.duration-0.25);
    }""")
    page.wait_for_function('audio.currentTime<1 && !audio.paused')
    page.evaluate("""async () => {
      if(audio.src!==src||playCalls!==calls||dbCalls!==reads)throw Error('All-repeat needed JS handoff');
      pausePlayback();S.repeat='one';makeOrder();await load(1,false);
      if(C.segs.length!==1||C.segs[0].i!==1||!C.loop||!audio.loop)throw Error('One-repeat still depends on segment timer');
      await startAudio();stopTicker();src=audio.src;calls=playCalls;reads=dbCalls;seekAbs(audio.duration-0.25);
    }""")
    page.wait_for_function('audio.currentTime<1 && !audio.paused')
    page.evaluate("""() => {
      if(audio.src!==src||playCalls!==calls||dbCalls!==reads)throw Error('One-repeat needed JS handoff');
      actions.nexttrack();
    }""")
    page.wait_for_function('!loadJob && cur===2 && !audio.paused')
    page.evaluate("""async () => {
      pausePlayback();updateProgress=realProgress;
      // Keep the input tiny: repeat actual valid MP3 frames using immutable Blob parts.
      // Two 76MiB tracks cross the former 150MiB split; stored Blob format is supported.
      const b=await realTx('blobs','readonly',st=>st.get(songs[0].id));
      const parsed=parseMp3(new Uint8Array(b.buf));
      const piece=toBlob(b).slice(parsed.start,parsed.end);
      const count=Math.ceil(76*1048576/piece.size);
      const big=new Blob(Array(count).fill(piece),{type:'audio/mpeg'});
      await realTx('blobs','readwrite',st=>st.put(big,'large-a'));
      await realTx('blobs','readwrite',st=>st.put(big,'large-b'));
      const tail=songs[2];
      songs=[{id:'large-a',title:'large A',duration:parsed.dur*count},
             {id:'large-b',title:'large B',duration:parsed.dur*count},tail];
      S.repeat='none';S.shuffle=false;makeOrder();
      if(!await load(0,false))throw Error('Large joined stream failed to load');
      if(C.segs.length!==3)throw Error('Playlist still split at 150MiB');
      const blob=await (await fetch(audio.src)).blob();
      if(blob.size<=150*1048576)throw Error('Large fixture did not cross former cap');
      const header=new Uint8Array(await blob.slice(0,600).arrayBuffer());
      const h=hdr(header,0),at=4+(h.v===3?(h.mode===3?17:32):(h.mode===3?9:17));
      if(new DataView(header.buffer).getUint32(at+12)!==blob.size)throw Error('Large Xing byte counter incorrect');
      if(Math.abs(audio.duration-C.total)>0.15)throw Error('Large Xing duration incorrect');
      window.largeBytes=blob.size;
      await startAudio();stopTicker();src=audio.src;calls=playCalls;reads=dbCalls;
      seekAbs(C.segs[1].start-0.15);
    }""")
    page.wait_for_function('audio.currentTime>C.segs[1].start+0.2 && !audio.paused')
    page.evaluate("""() => {
      if(audio.src!==src||playCalls!==calls||dbCalls!==reads)throw Error('Former cap boundary rebuilt source');
      pausePlayback();
    }""")
    assert not errors,errors
    print('PASS v12: mixed stereo coding, hidden song boundary without progress callbacks, native all/one loops, one-repeat next, deferred SW update while playing/paused, >150MiB single source and valid Xing:',page.evaluate('largeBytes'))
    browser.close()
server.shutdown()
