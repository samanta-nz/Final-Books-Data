#!/usr/bin/env python3
"""Regression checks for authored notes, acoustic anchors and mobile playback.

Run from repository root: python tools/test_mobile.py [--browser]
The browser suite needs a local server: python tools/preview_server.py . 8765
"""
import argparse
import asyncio
import json
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
BOOK=ROOT/'G09/G09-Math'
FILES=sorted(BOOK.glob('F0[12]/*.board.json'))

class ContentTests(unittest.TestCase):
    def test_all_lessons_have_authored_notes_and_monotone_acoustic_times(self):
        self.assertEqual(len(FILES),9)
        for path in FILES:
            with self.subTest(file=path.name):
                board=json.loads(path.read_text())
                timing=json.loads(path.with_name(path.name.replace('.board.json','.timing.json')).read_text())
                self.assertIn('forced alignment',timing['method'])
                self.assertEqual(len(board['sections']),len(timing['sections']))
                starts=[s['t'] for s in timing['sentences']]
                edits=path.with_name(path.name.replace('.board.json','.caption-edits.json'))
                excluded=set(json.loads(edits.read_text())['excluded']) if edits.exists() else set()
                self.assertEqual(starts,sorted(starts))
                for section in board['sections']:
                    self.assertTrue(section['items'])
                    for note in section['items']:
                        sentence=timing['sentences'][note['sentence']]
                        self.assertEqual(sentence['sec'],section['section'])
                        self.assertEqual(note['anchor_match_score'],100)
                        self.assertTrue(note['text'].strip())
                        self.assertEqual(note['text'].count('`')%2,0)
                for s in timing['sentences']:
                    self.assertLessEqual(s['t'],s['end'])
                    self.assertLessEqual(s['end'],timing['stats']['duration']+.05)
                    self.assertTrue(s['words'])
                    if len(s['text'])>30 and s['text'] not in excluded:
                        self.assertGreater(s['end']-s['t'],.1,'A complete sentence collapsed to a single time')

    def test_previous_audible_sync_regressions(self):
        anchors=json.loads((ROOT/'tools/testdata/mobile_alignment_anchors.json').read_text())
        errors=[]
        for a in anchors:
            timing=json.loads((BOOK/(a['lesson']+'.timing.json')).read_text())
            sentence=next(s for s in timing['sentences'] if s['text']==a['text'])
            expected=a['approximate_audio_start']
            edits=BOOK/(a['lesson']+'.audio-edits.json')
            if edits.exists():
                expected-=sum(c['end']-c['start'] for c in json.loads(edits.read_text())['remove'] if a['approximate_audio_start']>=c['end'])
            error=abs(sentence['t']-expected)
            errors.append({'lesson':a['lesson'],'old_error_s':a['old_error_s'],'new_error_s':round(error,3)})
            with self.subTest(lesson=a['lesson']):self.assertLessEqual(error,2.0)
        (ROOT/'mobile-alignment-validation.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2))

    def test_key_math_conditions_and_table_are_present(self):
        b=json.loads((BOOK/'F01/0102.board.json').read_text())['sections']
        text=' '.join(n['text'] for n in b[1]['items'])
        self.assertIn('10+4+(−2)=12',text)
        self.assertIn('12+4+(−4)=12',text)
        self.assertIn('b≠0',' '.join(n['text'] for n in b[11]['items']))
        for lesson,sec,phrase in [('F01/0104',1,'هم‌شانس'),('F02/0203',2,'a=−4'),('F01/01-review',2,'∅')]:
            data=json.loads((BOOK/(lesson+'.board.json')).read_text())
            self.assertIn(phrase,' '.join(n['text'] for n in data['sections'][sec]['items']))


async def browser_checks(base_url):
    from playwright.async_api import async_playwright
    results=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        for width,height in [(390,844),(360,640)]:
            for path in FILES:
                lesson=path.name.replace('.board.json','')
                url=base_url+'/'+str(path.parent.relative_to(ROOT)/'mobile'/(lesson+'.html'))
                page=await browser.new_page(viewport={'width':width,'height':height})
                errors=[];requests=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r: requests.append(r.url) if r.url.startswith('http') and not r.url.startswith(base_url) else None)
                await page.goto(url)
                await page.wait_for_function('Number.isFinite(au.duration) && M_LINES.length>3')
                await page.evaluate('document.fonts.ready')
                await page.wait_for_timeout(100)
                geometry=await page.evaluate('''()=>{
                  const failures=[];let pages=0;
                  for(let k=0;k<M_BOARDS.length;k++){
                    const j=M_BOARDS[k].items.length-1,plan=pagePlan(k);
                    for(let p=0;p<plan.length;p++){
                      manualPage=p;renderedKey='';renderBoard(k,j);pages++;
                      if(pgEl.scrollHeight>q('board').clientHeight+2)failures.push({section:k,page:p,content:pgEl.scrollHeight,frame:q('board').clientHeight});
                      if(pgEl.scrollWidth>q('board').clientWidth+2)failures.push({section:k,page:p,horizontal:true});
                    }
                    for(let n=0;n<=j;n++){
                      manualPage=null;renderedKey='';renderBoard(k,n);
                      if(pgEl.scrollHeight>q('board').clientHeight+2)failures.push({section:k,note:n,content:pgEl.scrollHeight,frame:q('board').clientHeight});
                    }
                  }return {failures,pages};
                }''')
                if geometry['failures']:raise AssertionError((lesson,width,geometry['failures']))
                await page.evaluate('seekTo(M_SEGS[1].end-.1,false)')
                await page.evaluate('seekTo(M_SEGS[1].start+.01,false)')
                future=await page.evaluate('Array.from(pgEl.querySelectorAll(".note")).some(n=>+n.dataset.time>au.currentTime+.001)')
                if future:raise AssertionError((lesson,'future board text after backwards seek'))
                await page.evaluate('seekTo(M_SEGS[0].end-.05,false)')
                await page.click('#pv');await page.evaluate('au.pause()')
                if await page.evaluate('Array.from(pgEl.querySelectorAll(".note")).some(n=>+n.dataset.time>au.currentTime+.001)'):
                    raise AssertionError((lesson,'previous button retained future notes'))
                await page.evaluate('seekTo(M_DUR/2,false)')
                visible=await page.evaluate('lineEls.filter(e=>getComputedStyle(e).visibility==="visible"&&+getComputedStyle(e).opacity>0).length')
                if visible!=3:raise AssertionError((lesson,'three caption rows',visible))
                before=await page.evaluate('JSON.stringify(M_SOURCE)')
                rows=await page.evaluate('JSON.stringify(M_LINES)')
                await page.set_viewport_size({'width':844,'height':390});await page.wait_for_timeout(140)
                await page.set_viewport_size({'width':width,'height':height});await page.wait_for_timeout(140)
                if before!=await page.evaluate('JSON.stringify(M_SOURCE)') or rows!=await page.evaluate('JSON.stringify(M_LINES)'):
                    raise AssertionError((lesson,'resize changed caption timing'))
                if errors or requests:raise AssertionError((lesson,errors,requests))
                results.append({'lesson':str(path.relative_to(BOOK)),'size':[width,height],'board_pages':geometry['pages'],'errors':errors,'external_requests':requests})
                await page.close()
        page=await browser.new_page(viewport={'width':390,'height':844})
        await page.goto(base_url+'/G09/G09-Math/F01/mobile/0102.html')
        await page.wait_for_function('Number.isFinite(au.duration)')
        await page.select_option('#rate','2');await page.click('#pp');await page.wait_for_timeout(500)
        running=await page.evaluate('({t:au.currentTime,rate:au.playbackRate})')
        if running['rate']!=2 or running['t']<.4:raise AssertionError(('playback speed',running))
        await page.click('#pp');paused=await page.evaluate('au.currentTime');await page.wait_for_timeout(250)
        if paused!=await page.evaluate('au.currentTime'):raise AssertionError('pause did not hold audio')
        await page.close();await browser.close()
    (ROOT/'mobile-browser-validation.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print('Browser checks passed:',len(results),'lesson/viewport combinations')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--browser',action='store_true');ap.add_argument('--url',default='http://127.0.0.1:8765')
    args=ap.parse_args()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ContentTests))
    if not result.wasSuccessful():raise SystemExit(1)
    if args.browser:asyncio.run(browser_checks(args.url))
