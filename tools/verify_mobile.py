#!/usr/bin/env python3
"""Check reusable lesson output, original OGG, captions, figures and controls."""
import argparse,asyncio,base64,functools,hashlib,http.server,json,pathlib,re,shutil,threading
from playwright.async_api import async_playwright
ROOT=pathlib.Path(__file__).resolve().parent.parent

async def verify(args):
    target=args.target.resolve();files=sorted(target.rglob('mobile/*.html')) if target.is_dir() else [target]
    files=[p for p in files if p.stem!='index' and 'sources' not in p.parts]
    if not files:raise ValueError('No built lesson HTML files')
    handler=functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(ROOT))
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),handler)
    threading.Thread(target=server.serve_forever,daemon=True).start();results=[]
    try:
        async with async_playwright() as pw:
            executable=args.browser or shutil.which('chromium') or shutil.which('chromium-browser')
            browser=await pw.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}))
            try:
                for path in files:
                    html=path.read_text();data=base64.b64decode(re.search(r'<script id="audio-data"[^>]*>(.*?)</script>',html,re.S)[1])
                    ogg=path.parent.parent/(path.stem+'.ogg')
                    assert data==ogg.read_bytes(),f'Embedded OGG changed: {path}'
                    for width,height in [(390,844),(360,640)]:
                        page=await browser.new_page(viewport={'width':width,'height':height});errors=[];external=[]
                        page.on('pageerror',lambda e:errors.append(str(e)))
                        page.on('request',lambda r:external.append(r.url) if r.url.startswith(('http:','https:')) and not r.url.startswith(f'http://127.0.0.1:{server.server_port}/') else None)
                        await page.goto(f'http://127.0.0.1:{server.server_port}/'+path.relative_to(ROOT).as_posix())
                        await page.wait_for_function('au.readyState>0');await page.evaluate('document.fonts.ready')
                        states=await page.evaluate('''() => {
                          let states=0;const problems=[];
                          for(let k=0;k<M_BOARDS.length;k++)for(let j=0;j<M_BOARDS[k].items.length;j++){
                            renderedKey='';renderBoard(k,j);states++;
                            if(pgEl.querySelector('.katex-error'))problems.push('formula: '+pgEl.querySelector('.katex-error').title);
                            if(pgEl.scrollWidth>q('board').clientWidth+2)problems.push('horizontal overflow');
                            const walker=document.createTreeWalker(pgEl,NodeFilter.SHOW_TEXT);let n;
                            while(n=walker.nextNode()){if(!n.parentElement.closest('annotation')&&/[0-9]/.test(n.textContent))problems.push('Latin digit: '+n.textContent);}
                          }
                          seekTo(Math.min(30,M_DUR/2),false);
                          return {states,problems:[...new Set(problems)],font:getComputedStyle(pgEl).fontFamily};
                        }''')
                        assert not states['problems'],(path,width,states['problems'])
                        # Test both long presses, preserving paused state.
                        for button,delta in [('#nx',5),('#pv',-5)]:
                            before=await page.evaluate('lessonTime()');await page.locator(button).hover();await page.mouse.down();await page.wait_for_timeout(500);await page.mouse.up()
                            after=await page.evaluate('lessonTime()');assert abs(after-before-delta)<.15,(path,button,after-before)
                        await page.locator('#seek').fill('10');await page.wait_for_timeout(80)
                        assert abs(await page.evaluate('lessonTime()')-10)<.15
                        expected=await page.evaluate('Math.min(M_SEGS.length-1,segAt(lessonTime())+1)')
                        await page.click('#nx');await page.wait_for_timeout(100)
                        assert await page.evaluate('segAt(lessonTime())')==expected,'Short click must change section'
                        await page.evaluate('au.pause();tick()');await page.select_option('#rate','2')
                        assert await page.evaluate('au.playbackRate')==2
                        rows=await page.evaluate("({shown:document.querySelectorAll('.caption-line.show').length,height:q('tx').clientHeight,lineH})")
                        assert rows['shown']<=3 and abs(rows['height']-rows['lineH']*3)<=1,rows
                        before=await page.evaluate('lessonTime()');await page.wait_for_timeout(120);assert abs(await page.evaluate('lessonTime()')-before)<.01
                        assert not errors,errors
                        assert not external,external
                        results.append({'lesson':str(path.relative_to(ROOT)),'viewport':[width,height],**states,'original_ogg_sha256':hashlib.sha256(data).hexdigest(),'controls':'passed','three_row_captions':'passed','external_requests':external})
                        await page.close()
            finally:await browser.close()
    finally:server.shutdown();server.server_close()
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(f'Passed {len(results)} lesson/viewport checks; report: {args.report}')

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('target',type=pathlib.Path);ap.add_argument('--browser');ap.add_argument('--report',type=pathlib.Path,default=ROOT/'output/mobile-verification.json');asyncio.run(verify(ap.parse_args()))
