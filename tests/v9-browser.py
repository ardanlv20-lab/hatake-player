"""Launch-race tests: python3 tests/v9-browser.py (playwright/Chromium + ffmpeg).
A test-only gate delays buildChunk; production source is otherwise unchanged.
"""
import functools, http.server, pathlib, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    files=[]
    for n in range(6):
        path=str(pathlib.Path(tmp)/f'song-{n+1}.mp3')
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                        f'sine=frequency={440+n*80}:duration=8','-ar','44100','-ac','2','-y',path],check=True)
        files.append(path)
    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    context=browser.new_context(service_workers='block')
    context.add_init_script("""
      window.actions={};window.playCalls=[];
      const register=navigator.mediaSession.setActionHandler.bind(navigator.mediaSession);
      navigator.mediaSession.setActionHandler=(a,f)=>{actions[a]=f;register(a,f)};
      const play=HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play=function(){
        playCalls.push({time:this.currentTime,src:this.getAttribute('src'),title:document.getElementById('title')?.textContent});
        return play.call(this);
      };
      window.__holdBuild=sessionStorage.getItem('holdBuild')==='yes';
      window.__buildGate=new Promise(resolve=>{window.__releaseBuild=()=>{window.__holdBuild=false;resolve()}});
    """)
    source=(root/'app.js').read_text().replace('async function buildChunk(i){','async function buildChunk(i){if(window.__holdBuild)await window.__buildGate;')
    context.route('**/app.js*',lambda route:route.fulfill(status=200,content_type='text/javascript',body=source))
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url);page.wait_for_function('window.__ready')
    page.set_input_files('#file',files)
    page.wait_for_function('songs.length===6 && cur===0 && !loadJob && audio.readyState>=1')
    page.evaluate('load(1,false,3.25)')
    page.wait_for_function('cur===1 && !loadJob && Math.abs(audio.currentTime-3.25)<0.05')
    page.evaluate('pausePlayback()')
    page.reload();page.wait_for_function('window.__ready && !loadJob')
    page.evaluate("""() => {
      if(cur!==-1||!audio.paused||playCalls.length)throw Error('Startup should stay idle');
      if(document.getElementById('title').textContent!=='曲を選んでください')throw Error('Wrong idle title');
      if(audio.getAttribute('src'))throw Error('Startup loaded audio');
    }""")
    page.evaluate('load(1,false,3.25)')
    page.wait_for_function('cur===1 && !loadJob && Math.abs(audio.currentTime-3.25)<0.05')
    page.evaluate("""() => {
      if(document.getElementById('title').textContent!=='song-2')throw Error('Wrong loaded title');
      if(C.segs[0].id!==songs[1].id)throw Error('Wrong chunk start');
    }""")
    # Metadata is already present: no new loadedmetadata event should be necessary.
    assert page.evaluate('restorePosition(2.5,null)')
    page.evaluate("""() => {
      if(Math.abs(audio.currentTime-2.5)>0.05||playCalls.length)throw Error('Ready metadata seek failed');
      lastAbs=3.25;audio.currentTime=0;startAudio();
      if(playCalls.length!==1||playCalls[0].time<3.2)throw Error('Reset-to-zero was not restored before synchronous play');
      pausePlayback();playCalls=[];
      seekAbs(3.25);pausePlayback();sessionStorage.setItem('holdBuild','yes');
    }""")
    page.reload();page.wait_for_function('window.__ready && !loadJob && cur===-1')
    page.evaluate('() => { load(1,false,3.25) }')
    page.wait_for_function('loadJob')
    page.evaluate("""() => {
      if(document.getElementById('title').textContent!=='song-2')throw Error('No loading title');
      // Force an order different from the selected song to catch the old recovery bug.
      order=[0,1,2,3,4,5];window.restoreGeneration=building;
    }""")
    page.locator('#play').click()
    page.evaluate("""() => {
      if(!wantPlay||building!==restoreGeneration||playCalls.length)throw Error('Early play cancelled restore');
      // A second request arriving at metadata must also wait until the saved seek finishes.
      audio.addEventListener('loadedmetadata',()=>{
        startAudio();if(playCalls.length)throw Error('Played before restoration seek');
      },{once:true});
      __releaseBuild();
    }""")
    page.wait_for_function('window.__ready && !loadJob && !audio.paused')
    page.evaluate("""() => {
      if(building!==restoreGeneration||cur!==1||C.segs[0].id!==songs[1].id)throw Error('Wrong restore won');
      if(playCalls.length!==1||playCalls[0].time<3.2||playCalls[0].title!=='song-2')throw Error('Play happened before seek');
      if(!playCalls[0].src||Math.abs(audio.currentTime-3.25)>0.8)throw Error('Wrong playback position');
      const src=audio.src;actions.pause();if(audio.src!==src)throw Error('Pause cleared source');
      seekAbs(3.25);pausePlayback();
    }""")
    # Cancelling a queued request must not auto-play after restoration.
    page.reload();page.wait_for_function('window.__ready && !loadJob && cur===-1')
    page.evaluate('load(1,false,3.25);startAudio();pausePlayback();__releaseBuild()')
    page.wait_for_function('window.__ready && !loadJob')
    page.evaluate("if(!audio.paused||wantPlay||playCalls.length)throw Error('Queued pause was ignored')")
    # A newer explicit selection supersedes an older build without stale callbacks.
    page.evaluate("""async () => {
      __holdBuild=true;__buildGate=new Promise(resolve=>window.__releaseBuild=()=>{__holdBuild=false;resolve()});
      const old=load(1,false,3),newer=load(2,false,2);startAudio();
      __releaseBuild();await Promise.all([old,newer]);
    }""")
    page.wait_for_function('!loadJob && !audio.paused && cur===2')
    page.evaluate("""() => {
      if(playCalls.length!==1||playCalls[0].time<1.95||playCalls[0].title!=='song-3')throw Error('Stale build changed new selection');
      const src=audio.src;actions.pause();if(audio.src!==src)throw Error('Pause lost source');
    }""")
    page.evaluate("""async () => {
      // Supersede a load precisely during metadata delivery, before its old seek.
      let newer;
      audio.addEventListener('loadedmetadata',()=>{newer=load(2,false,2)},{once:true});
      await load(1,false,5);
      if(newer)await newer;
      if(loadJob||cur!==2||Math.abs(audio.currentTime-2)>0.05)throw Error('Stale metadata seek escaped cancellation');
    }""")
    page.evaluate("sessionStorage.removeItem('holdBuild')")
    page.reload();page.wait_for_function('window.__ready && !loadJob && cur===-1', timeout=30000)
    page.set_input_files('#file', files[0])
    page.wait_for_function('songs.length===7 && !loadJob', timeout=60000)
    page.evaluate("""() => {
      if(cur!==-1)throw Error('Idle add auto-loaded a song');
      if(document.getElementById('title').textContent!=='曲を選んでください')throw Error('Idle add changed title');
      if(audio.getAttribute('src'))throw Error('Idle add set audio src');
      document.getElementById('seek').value=0;
      document.getElementById('seek').dispatchEvent(new Event('input'));
    }""")
    page.evaluate('() => { load(0,false) }')
    page.wait_for_function('cur===0 && !loadJob && audio.readyState>=1', timeout=30000)
    page.evaluate("""async () => {
      if(!C||!urlNow||!audio.getAttribute('src'))throw Error('Expected loaded chunk');
      const oldConfirm=window.confirm;window.confirm=()=>true;
      while(songs.length>1)await del(songs.length-1);
      if(cur!==0||!C||!urlNow)throw Error('Deleting other songs cleared the current track');
      await del(0);
      window.confirm=oldConfirm;
      if(C||urlNow||audio.getAttribute('src')||cur!==-1)throw Error('Last-song delete leaked playback: C='+!!C+' urlNow='+!!urlNow+' src='+audio.getAttribute('src')+' cur='+cur);
      if(document.getElementById('title').textContent!=='曲を追加してください')throw Error('Wrong empty title');
    }""")
    assert page.locator('#ver').inner_text()=='v14'
    assert not errors,errors
    print('PASS v14: idle startup, no idle-add autoload, last-song cleanup, metadata-already-ready seek, early play stays queued, no recovery/cancellation, seek-before-first-play, queue cancellation, newer-load ownership, retained src, no page errors')
    browser.close()
server.shutdown()
