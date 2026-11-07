"""Regression: python3 tests/v8-browser.py (requires playwright/Chromium and ffmpeg)."""
import functools, http.server, pathlib, re, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright

root=pathlib.Path(__file__).resolve().parents[1]
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}/'
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    browser=p.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
    for rate,channels in [(44100,1),(48000,2),(22050,1),(11025,2)]:
        files=[]
        for n in range(3):
            path=str(pathlib.Path(tmp)/f'{rate}-{channels}-{n}.mp3')
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                            f'sine=frequency={440+100*n}:duration=4','-ar',str(rate),'-ac',str(channels),
                            '-q:a',str(2+n),'-y',path],check=True)
            files.append(path)
        context=browser.new_context()
        context.add_init_script("""
          window.actions={};window.positions=[];window.rejected=[];
          const register=navigator.mediaSession.setActionHandler.bind(navigator.mediaSession);
          navigator.mediaSession.setActionHandler=(a,f)=>{actions[a]=f;register(a,f)};
          const position=navigator.mediaSession.setPositionState.bind(navigator.mediaSession);
          navigator.mediaSession.setPositionState=s=>{positions.push({...s});position(s)};
        """)
        page=context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url);page.wait_for_function('window.__ready')
        page.set_input_files('#file',files)
        page.wait_for_function('C && C.segs.length===3 && audio.readyState>=1')
        info=page.evaluate("""async () => {
          const buf=new Uint8Array(await (await fetch(audio.src)).arrayBuffer());
          const h=hdr(buf,0),at=4+(h.v===3?(h.mode===3?17:32):(h.mode===3?9:17));
          const view=new DataView(buf.buffer),parsed=parseMp3(buf);
          if(String.fromCharCode(...buf.slice(at,at+4))!=='Xing')throw Error('Missing Xing');
          if(view.getUint32(at+12)!==buf.length)throw Error('Incorrect byte count');
          if(view.getUint32(at+8)!==parsed.offsets.length)throw Error('Incorrect frame count');
          if(!Number.isFinite(audio.duration)||Math.abs(audio.duration-C.total)>0.15)throw Error('Invalid chunk duration');
          return {duration:audio.duration,total:C.total,frames:parsed.offsets.length};
        }""")
        page.evaluate("""() => {
          // Suppress timeupdate to prove that the independent 250ms ticker works.
          window.timeupdate=audio.ontimeupdate;audio.ontimeupdate=null;
          window.positions=[];actions.play();
        }""")
        page.wait_for_function("positions.length>=4 && Number(document.getElementById('cur').textContent.split(':')[1])>=1")
        page.evaluate("""() => {
          if(!positions.every(s=>Number.isFinite(s.duration)&&s.duration>0&&Number.isFinite(s.position)&&s.position>=0&&s.position<=s.duration))throw Error('Invalid position');
          if(!positions.some((s,i)=>i>0&&s.position>positions[i-1].position))throw Error('Clock not advancing');
          if(ticker===null)throw Error('No ticker');
          audio.dispatchEvent(new Event('pause')); // Transient iOS-style notification, not a user pause.
          if(navigator.mediaSession.playbackState!=='playing')throw Error('Transient pause froze OS clock');
        }""")
        page.wait_for_function('ticker!==null')
        page.evaluate("""() => {
          seekAbs(C.segs[1].start+1.2);updateProgress();
          if(segIdx!==1||cur!==1||document.getElementById('cur').textContent!=='0:01')throw Error('Boundary ticker failed');
          audio.ontimeupdate=timeupdate;
          seekAbs(C.segs[2].start+1.2);audio.dispatchEvent(new Event('timeupdate'));
          if(segIdx!==2||cur!==2||document.getElementById('cur').textContent!=='0:01')throw Error('Boundary timeupdate failed');
          const original=navigator.mediaSession.setPositionState;
          navigator.mediaSession.setPositionState=s=>{
            if(s.duration!==audio.duration){rejected.push(s);throw new TypeError('Test: only element duration accepted')}
            original(s);
          };
          posState();
          if(!rejected.length||positions.at(-1).duration!==audio.duration)throw Error('Element fallback failed');
          navigator.mediaSession.setPositionState=original;
          const dur=C.segs[2].dur;C.segs[2].dur=NaN;posState();C.segs[2].dur=dur;
          if(positions.at(-1).duration!==audio.duration)throw Error('Invalid segment fallback failed');
          window.keptSrc=audio.src;actions.pause();
          if(audio.src!==keptSrc||!audio.paused||ticker!==null)throw Error('Pause lost source or ticker');
        }""")
        page.evaluate("""() => {
          userPaused=false;audio.dispatchEvent(new Event('pause'));
          if(navigator.mediaSession.playbackState!=='paused')throw Error('Explicit pause state lost');
          audio.dispatchEvent(new Event('ended'));
          if(ticker!==null)throw Error('Ended leaked ticker');
        }""")
        page.wait_for_function('navigator.serviceWorker.controller!==null')
        page.evaluate('navigator.serviceWorker.ready')
        context.set_offline(True);page.reload();page.wait_for_function('window.__ready')
        assert page.locator('#list li').count()==3
        assert page.locator('#ver').inner_text()==re.search(r"APP_VERSION='([^']+)'", (root/'app.js').read_text())[1]
        assert not errors,errors
        print(f'PASS MPEG fixture {rate}Hz / {channels}ch: {info}; ticker, timeupdate, position state, pause, offline')
        context.close()
    browser.close()
server.shutdown()
print('All v8 tests passed (Chromium, not real iOS Control Center)')
