#!/usr/bin/env python3
"""Build one *self-contained* HTML file for a lesson:

    audio (.ogg, base64)  +  whiteboard (.html, base64)  +  timing data (json)

The result is a single file that works by double-click (file://), offline, with
no server: the ogg is embedded as base64 and injected into a <blob:> URL, the
board runs inside a `srcdoc` iframe and is driven with postMessage.

    python tools/build_embed.py G09/G09-Math/F01/0101
    -> G09/G09-Math/F01/0101-sync-embed.html
"""
import base64
import json
import os
import re
import sys

HOOK = r"""
<script>
/* bridge injected by tools/build_embed.py : the parent page drives the board */
(function(){
  function go(i){
    try{
      var sel = document.querySelector('#sp');
      if (sel && sel.value !== '100') { sel.value = '100'; sel.dispatchEvent(new Event('change')); }
      var ac = document.querySelector('#au'); if (ac) ac.checked = false;
      if (typeof play === 'function') play(i);
    }catch(e){}
  }
  addEventListener('message', function(e){
    var d = e.data || {};
    if (d.type === 'slide') go(d.slide);
  });
  function ready(){ try { parent.postMessage({type:'ready'}, '*'); } catch(e){} }
  addEventListener('load', ready);
  ready();
})();
</script>
"""

PAGE = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ — پخش همگام وایت‌بورد و صدا</title>
<style>
  :root{--bg:#eef1f5;--pnl:#fff;--tx:#1b2430;--mut:#5a6675;--ln:#d3dae2;--ac:#1f4fd1;--hi:#ffe45e;--warn:#c2410c}
  @media (prefers-color-scheme: dark){
    :root{--bg:#14181d;--pnl:#1c222a;--tx:#e6ebf0;--mut:#93a1b1;--ln:#313a45;--ac:#7aa2ff;--hi:#7a6410;--warn:#fb923c}
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:var(--bg);color:var(--tx);font-family:Vazirmatn,Tahoma,system-ui,sans-serif;display:flex;flex-direction:column;gap:8px;padding:8px}
  header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;background:var(--pnl);border:1px solid var(--ln);border-radius:12px;padding:8px 12px}
  h1{font-size:15px;margin:0 0 0 auto;font-weight:800}
  button{font:inherit;color:inherit;background:var(--pnl);border:1px solid var(--ln);border-radius:9px;padding:6px 12px;cursor:pointer}
  button:hover{border-color:var(--ac)}
  button.ghost{background:transparent}
  .grow{flex:1}
  .pill{font-size:12px;color:var(--mut);border:1px solid var(--ln);border-radius:99px;padding:3px 9px;white-space:nowrap}
  .pill b{color:var(--tx)}
  main{flex:1;min-height:0;display:grid;grid-template-columns:1.05fr 1fr;gap:8px}
  @media (max-width:900px){main{grid-template-columns:1fr}}
  section.pane{background:var(--pnl);border:1px solid var(--ln);border-radius:12px;display:flex;flex-direction:column;min-height:0;overflow:hidden}
  .pane h2{font-size:13px;margin:0;padding:8px 12px;border-bottom:1px solid var(--ln);color:var(--mut);font-weight:700;display:flex;gap:8px;align-items:center}
  .pane h2 .nowid{color:var(--tx);font-weight:800}
  #boardWrap{flex:1;min-height:320px;position:relative;background:#fbfdfe}
  #board{position:absolute;inset:0;width:100%;height:100%;border:0}
  #text{flex:1;overflow:auto;padding:6px 14px 40vh;font-size:15px;line-height:2}
  #text h3{font-size:13px;color:var(--mut);margin:18px 0 4px;font-weight:700;border-right:3px solid var(--ac);padding-right:8px}
  #text .s{transition:background .25s;border-radius:4px;padding:1px 2px}
  #text .s.cur{background:var(--hi);color:#111;box-shadow:0 0 0 2px var(--hi)}
  #text .s.done{opacity:.55}
  .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;background:var(--pnl);border:1px solid var(--ln);border-radius:12px;padding:8px 12px}
  audio{width:100%;min-width:240px}
  #chips{display:flex;flex-wrap:wrap;gap:6px;max-height:104px;overflow:auto}
  .chip{font:12px/1.2 inherit;display:inline-flex;gap:6px;align-items:center;border:1px solid var(--ln);border-radius:99px;padding:4px 8px;background:transparent;color:var(--tx);cursor:pointer}
  .chip.cur{border-color:var(--ac);background:color-mix(in srgb,var(--ac) 14%,transparent)}
  .chip i{font-style:normal;color:var(--mut)}
  .tag{font-size:11px;border-radius:6px;padding:1px 6px;background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
  label.chk{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--mut)}
  #boot{position:fixed;inset:0;background:var(--bg);display:grid;place-content:center;gap:10px;text-align:center;z-index:9;font-size:14px;color:var(--mut)}
  #boot b{color:var(--tx);font-size:16px}
</style>
</head>
<body>
<div id="boot"><b>__TITLE__</b><div id="bootmsg">در حال آماده‌سازی صدا…</div></div>

<header>
  <h1>__TITLE__</h1>
  <span class="pill">زمان: <b id="now">۰:۰۰</b> / <b id="dur">—</b></span>
  <span class="pill">اسلاید <b id="slideNo">—</b></span>
  <button class="ghost" id="minus" title="همگام‌سازی عقب‌تر">−۰٫۵s</button>
  <button class="ghost" id="plus" title="همگام‌سازی جلوتر">+۰٫۵s</button>
  <label class="chk"><input type="checkbox" id="sync" checked> همگام با صدا</label>
  <span class="tag">زمان‌بندی تخمینی</span>
</header>

<main>
  <section class="pane">
    <h2>وایت‌بورد <span class="nowid" id="slideTitle">—</span></h2>
    <div id="boardWrap"><iframe id="board" title="وایت‌بورد" sandbox="allow-scripts"></iframe></div>
  </section>
  <section class="pane">
    <h2>متن گفتار (جملهٔ در حال خواندن هایلایت می‌شود)</h2>
    <div id="text"></div>
  </section>
</main>

<div class="bar">
  <audio id="au" controls preload="metadata"></audio>
  <div class="grow"><div id="chips"></div></div>
</div>

<!-- ============ data ============ -->
<script id="board-data" type="text/plain">__BOARD_B64__</script>
<script id="audio-data" type="text/plain">__AUDIO_B64__</script>
<script>const DATA=__JSON__;</script>

<script>
const $=s=>document.querySelector(s);
const fa=n=>String(n).replace(/\d/g,d=>'۰۱۲۳۴۵۶۷۸۹'[d]);
const mmss=t=>{t=Math.max(0,t);const m=Math.floor(t/60),s=Math.floor(t%60);return fa(m)+':'+fa(String(s).padStart(2,'0'));};
const sents=DATA.sentences||[], starts=sents.map(s=>s.t);
const slides=(DATA.slides||[]).slice().sort((a,b)=>a.start-b.start);
const secs=DATA.sections||[];
let curS=-1, curSlide=null, offset=0, boardReady=false;
const au=$('#au'), textEl=$('#text'), frame=$('#board');

function b64bytes(b64){const s=atob(b64.replace(/\s+/g,''));const u=new Uint8Array(s.length);for(let i=0;i<s.length;i++)u[i]=s.charCodeAt(i);return u;}

const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function renderText(){
  let html='',cur=-1;
  sents.forEach((s,i)=>{
    if(s.sec!==cur){cur=s.sec;html+=`<h3>${esc(secs[cur]?secs[cur].title:'')} <span style="color:var(--mut);font-weight:400">${mmss(secs[cur]?secs[cur].start:0)}</span></h3>`}
    html+=`<span class="s" data-i="${i}">${esc(s.text)} </span>`;
  });
  textEl.innerHTML=html;
}
function renderChips(){
  const items = slides.length ? slides.map(s=>({t:s.start,label:s.title,no:s.slide}))
                             : secs.map(s=>({t:s.start,label:s.title}));
  $('#chips').innerHTML=items.map(it=>`<button class="chip" data-t="${it.t}"><i>${mmss(it.t)}</i>${esc(it.label)}</button>`).join('');
  $('#chips').querySelectorAll('.chip').forEach(b=>b.onclick=()=>{au.currentTime=+b.dataset.t+offset; au.play()});
  window.chipItems=items;
}
function slideAt(t){
  if(!slides.length) return null;
  let lo=0,hi=slides.length-1,r=null;
  while(lo<=hi){const m=(lo+hi)>>1; if(slides[m].start<=t){r=slides[m];lo=m+1}else hi=m-1}
  return r||slides[0];
}
function sentAt(t){
  let lo=0,hi=starts.length-1,r=-1;
  while(lo<=hi){const m=(lo+hi)>>1; if(starts[m]<=t){r=m;lo=m+1}else hi=m-1}
  return r;
}
function showSlide(sl){
  if(!sl || !boardReady) return;
  try{ frame.contentWindow.postMessage({type:'slide',slide:sl.slide-1},'*'); }catch(e){}
}
function tick(){
  if(!sents.length) return;
  const t=au.currentTime-offset;
  const i=sentAt(t);
  if(i!==curS){
    const p=textEl.querySelector('.s.cur'); if(p){p.classList.remove('cur');p.classList.add('done')}
    curS=i;
    const el=textEl.querySelector('.s[data-i="'+i+'"]');
    if(el){el.classList.remove('done');el.classList.add('cur');
      const r=el.getBoundingClientRect(), pr=textEl.getBoundingClientRect();
      if(r.top<pr.top+40||r.bottom>pr.bottom-60) el.scrollIntoView({block:'center',behavior:'smooth'});}
  }
  $('#now').textContent=mmss(au.currentTime);
  if($('#sync').checked && slides.length){
    const sl=slideAt(t);
    if(sl && (!curSlide || sl.slide!==curSlide.slide)){
      curSlide=sl; showSlide(sl);
      $('#slideNo').textContent=fa(sl.slide);
      $('#slideTitle').textContent=sl.title;
      document.querySelectorAll('#chips .chip').forEach((c,n)=>{
        const it=window.chipItems[n]; c.classList.toggle('cur', it && Math.abs(it.t-sl.start)<0.05);
      });
    }
  }
}
function boot(){
  // whiteboard
  frame.srcdoc=new TextDecoder('utf-8').decode(b64bytes(document.getElementById('board-data').textContent));
  // audio
  const blob=new Blob([b64bytes(document.getElementById('audio-data').textContent)],{type:'audio/ogg'});
  au.src=URL.createObjectURL(blob);
  $('#dur').textContent=mmss(DATA.stats.duration);
  renderText(); renderChips();
  $('#boot').style.display='none';
}
addEventListener('message',e=>{
  const d=e.data||{};
  if(d.type==='ready'){ boardReady=true; if(au.currentTime>0) tick(); }
});
au.addEventListener('timeupdate',tick);
au.addEventListener('seeked',tick);
$('#minus').onclick=()=>{offset-=0.5;curSlide=null;tick()};
$('#plus').onclick=()=>{offset+=0.5;curSlide=null;tick()};
$('#sync').onchange=()=>{curSlide=null;tick()};
addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT') return;
  if(e.code==='Space'){e.preventDefault(); au.paused?au.play():au.pause();}
  if(e.code==='ArrowLeft'){au.currentTime=Math.min(au.duration||0,au.currentTime+5)}
  if(e.code==='ArrowRight'){au.currentTime=Math.max(0,au.currentTime-5)}
});
requestAnimationFrame(()=>setTimeout(boot,30));
</script>
</body>
</html>
"""


def build(target, out=None, title=None):
    base = os.path.splitext(os.path.basename(target))[0]
    folder = os.path.dirname(os.path.abspath(target))
    ogg = os.path.join(folder, base + '.ogg')
    board = os.path.join(folder, base + '.html')
    timing = os.path.join(folder, base + '.timing.json')
    for p in (ogg, board, timing):
        if not os.path.exists(p):
            sys.exit(f'missing {p}')

    data = json.load(open(timing, encoding='utf-8'))
    data.pop('pauses', None)
    title = title or data.get('doc') or base
    title = re.sub(r'\s*\([^)]*\)\s*$', '', title)

    html = open(board, encoding='utf-8').read()
    if '</body>' in html:
        html = html.replace('</body>', HOOK + '</body>', 1)
    else:
        html += HOOK
    board_b64 = base64.b64encode(html.encode('utf-8')).decode('ascii')
    audio_b64 = base64.b64encode(open(ogg, 'rb').read()).decode('ascii')

    # chunk the audio base64 so the file stays editor/tool friendly
    audio_chunks = '\n'.join(audio_b64[i:i + 400] for i in range(0, len(audio_b64), 400))

    page = (PAGE
            .replace('__TITLE__', title)
            .replace('__BOARD_B64__', board_b64)
            .replace('__AUDIO_B64__', audio_chunks)
            .replace('__JSON__', json.dumps(data, ensure_ascii=False, separators=(',', ':'))))

    out = out or os.path.join(folder, base + '-sync-embed.html')
    open(out, 'w', encoding='utf-8').write(page)
    print(f'{out}  ({os.path.getsize(out) / 1e6:.1f} MB)')
    return out


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
