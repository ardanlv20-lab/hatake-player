"""Run with python3 after pip install playwright && playwright install chromium.
Uses a temporary localhost server, generated fixtures (ffmpeg), isolated Chromium profile.
Does not reproduce iOS Control Center / OS process termination.
"""
import functools, http.server, pathlib, re, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root = pathlib.Path(__file__).resolve().parents[1]
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_port}/"
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    fixtures = []
    for name, ext in [("song", "mp3"), ("other", "wav")]:
        path = str(pathlib.Path(tmp) / (name + "." + ext))
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=30", "-y", path], check=True)
        fixtures.append(path)
    browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    context = browser.new_context()
    context.add_init_script("""
      window.actions={};window.positions=[];
      const register=navigator.mediaSession.setActionHandler.bind(navigator.mediaSession);
      navigator.mediaSession.setActionHandler=(a,f)=>{actions[a]=f;register(a,f)};
      const position=navigator.mediaSession.setPositionState.bind(navigator.mediaSession);
      navigator.mediaSession.setPositionState=s=>{positions.push(s);position(s)};
      Object.defineProperty(navigator,'audioSession',{value:{type:'auto'}});
    """)
    page = context.new_page()
    errors=[]
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_function('window.__ready')
    page.set_input_files('#file', fixtures)
    page.wait_for_function("songs.length===2 && cur===0 && audio.readyState>=1")
    page.evaluate("""() => {
      window.originalElement=audio;
      window.nativePlay=audio.play.bind(audio);
      audio.play=()=>{window.playCalled=true;return nativePlay()};
      window.realTx=tx;window.dbCalls=0;tx=(...a)=>{dbCalls++;return realTx(...a)};
      actions.play();
      if(!playCalled||dbCalls!==0)throw Error('Play was deferred or DB accessed first');
    }""")
    page.wait_for_function("!audio.paused && navigator.mediaSession.playbackState==='playing'")
    page.evaluate("""() => {
      const src=audio.src;
      actions.seekto({seekTime:7});actions.pause();
      if(audio.src!==src||!audio.paused||navigator.mediaSession.playbackState!=='paused')throw Error('Pause destroyed session');
      actions.stop();
      if(audio.src!==src||navigator.mediaSession.playbackState!=='paused')throw Error('Stop destroyed session');
      window.beforeResume=audio.currentTime;
      window.beforeDB=dbCalls;actions.play();
      if(dbCalls!==beforeDB)throw Error('Successful resume used DB');
      const metadata=navigator.mediaSession.metadata;
      if(metadata.title!=='song'||metadata.artwork.length!==2)throw Error('Missing metadata');
      if(!metadata.artwork.every(a=>a.src.startsWith(location.origin)))throw Error('Non-local artwork');
      if(navigator.audioSession.type!=='playback')throw Error('Missing audioSession');
    }""")
    page.wait_for_function("!audio.paused && audio.currentTime>=beforeResume")
    page.evaluate("""() => {
      actions.pause();window.oldSource=audio.src;window.failOnce=true;
      audio.play=()=>{if(failOnce){failOnce=false;return Promise.reject(new DOMException('test discarded resource','NotSupportedError'))}return nativePlay()};
      actions.play();
    }""")
    page.wait_for_function("audio.src!==oldSource && !audio.paused && audio.currentTime>=7")
    page.evaluate("""() => {
      actions.pause();window.keptSource=audio.src;
      window.dispatchEvent(new PageTransitionEvent('pagehide'));
      document.dispatchEvent(new Event('visibilitychange'));
      if(audio.src!==keptSource||!audio.paused)throw Error('Lifecycle changed paused source');
      actions.nexttrack();
    }""")
    page.wait_for_function("cur===1 && !audio.paused")
    page.evaluate('actions.previoustrack()')
    page.wait_for_function("cur===0 && !audio.paused")
    page.evaluate("actions.seekto({seekTime:9});actions.pause()")
    page.wait_for_function('navigator.serviceWorker.controller!==null')
    page.evaluate('navigator.serviceWorker.ready')
    context.set_offline(True)
    page.reload()
    page.wait_for_function('window.__ready')
    page.wait_for_function('audio.readyState>=1 && audio.currentTime>=9')
    assert page.locator('#ver').inner_text()==re.search(r"APP_VERSION='([^']+)'", (root/'app.js').read_text())[1]
    assert page.locator('#list li').count()==2
    page.evaluate("""() => {
      if(audio!==document.querySelector('audio')||document.querySelectorAll('audio').length!==1)throw Error('Multiple audio elements');
      if(!audio.playsInline||audio.preload!=='auto')throw Error('Audio configuration');
      actions.play();
    }""")
    page.wait_for_function("!audio.paused && navigator.mediaSession.playbackState==='playing'")
    assert not errors, errors
    print('PASS: synchronous remote play, pause/stop source retention, title/artwork, audioSession, failed-play recovery, saved position, lifecycle, next/previous, single audio element, offline shell/library/playback, v7')
    browser.close()
server.shutdown()
