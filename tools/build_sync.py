#!/usr/bin/env python3
"""Build one self-contained, *audio-synchronised* whiteboard page per lesson.

    python tools/build_sync.py G09/G09-Math/F01            # every lesson of the folder
    python tools/build_sync.py G09/G09-Math/F01/0101       # a single lesson

The output (`<base>-sync.html`) contains, in one file:

  * the narration of the lesson, embedded as base64 (re-encoded to 32 kbit/s Opus
    so the page stays ~8 MB instead of ~21 MB - `--original-audio` keeps the ogg),
  * the whiteboard drawing engine, taken from the lesson's own `html` file,
  * the timing data (sections, sentences, slides) produced by align_ogg.py.

Behaviour required by the player:

  * the board is rendered **in the page itself** (no iframe), item by item,
    driven by the audio clock - so it always starts at the section start and
    walks forward in order with the voice,
  * the board is **never empty**: the first line of a section appears the moment
    the section starts (and at t=0 on load) and nothing is ever removed, so the
    previous section stays on screen until the next one begins,
  * the transcript highlight switches at the **start** of each sentence,
  * a section that has several slides gets its window split between them.
"""
import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import align_ogg as A                                      # noqa: E402

CANON_BOARD = 'G09/G09-Math/F01/0101.html'                  # engine template


# ------------------------------------------------------------------ engine ----
def split_board(html):
    """-> (style, engine_js, slides_js) for a whiteboard html file"""
    style = re.search(r'<style>(.*?)</style>', html, re.S).group(1)
    js = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
    m_arr = re.search(r'\nconst L\d+\s*=\s*\[', js)
    if not m_arr:
        raise ValueError('slide array not found')
    start = m_arr.start()
    m_end = re.search(r'\nconst S\s*=', js)
    name = re.search(r'\nconst (L\d+)\s*=\s*\[', js).group(1)
    engine = js[:start]
    slides = js[start:m_end.start()] + f'\nconst S={name};'
    # keep the item list of every slide so it can be replayed step by step
    engine = engine.replace('async()=>{for(const i of a)await run(i)}]',
                            'async()=>{for(const i of a)await run(i)},a]')
    if ',a]' not in engine:
        raise ValueError('could not patch sc() to keep item lists')
    return style, engine, slides


def strip_body_rules(css):
    """drop the board's html/body layout rules, the player supplies its own"""
    css = re.sub(r'(?m)(^|[}\n])\s*(html\s*,\s*body|body)\s*\{[^}]*\}', r'\1', css)
    return css


# ------------------------------------------------------- synthetic whiteboard ----
CLEAN = re.compile(r'[@\[\]{}|`*#~\\]')
GREET = ('سلام', 'خداحافظ', 'به روی ماه', 'من مهیار')


def _clean(t, limit=64):
    t = CLEAN.sub(' ', t)
    t = re.sub(r'\s+', ' ', t).strip(' ،؛:')
    if len(t) > limit:
        cut = t[:limit]
        t = cut[:cut.rfind(' ')] + '…'
    return t


def _chunks(text, limit=64, hard_max=96):
    """break a sentence into board-sized lines on punctuation first"""
    text = CLEAN.sub(' ', text)
    parts = re.split(r'(?<=[،؛.!؟])\s+', text)
    out, buf = [], ''
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not buf:
            buf = p
        elif len(buf) + 1 + len(p) <= limit:
            buf += ' ' + p
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return [c for c in (_clean(x, hard_max) for x in out) if c]


def auto_slides(timing, max_lines=16):
    """build board items out of the transcript for lessons without an html board"""
    sents = timing['sentences']
    secs = timing['sections']
    out = []
    for i, sec in enumerate(secs):
        mine = [s for s in sents if s['sec'] == i]
        keep = [s for s in mine if not any(g in s['text'] for g in GREET)] or mine
        cand = []
        for s in keep:
            cand += _chunks(s['text'])
        if len(cand) > max_lines:                       # keep them spread over the section
            idx = sorted({int(round(k * (len(cand) - 1) / (max_lines - 1)))
                          for k in range(max_lines)})
            cand = [cand[k] for k in idx]
        colors = ['B', 'K', 'G', 'P', 'R']
        items = [colors[n % len(colors)] + t for n, t in enumerate(cand)]
        if not items:                                    # never leave a section blank
            items = ['B' + _clean(sec['title'])]
        out.append([_clean(sec['title'], 60) or sec['title'], None, items])
    return out


# ------------------------------------------------------------------- audio ----
def opus_b64(ogg, bitrate=32000):
    """32 kbit/s opus of the narration (cached in ~/.cache so rebuilds are fast)"""
    import hashlib
    cache_dir = os.path.join(os.path.expanduser('~'), '.cache', 'board-sync')
    os.makedirs(cache_dir, exist_ok=True)
    key = hashlib.md5(f'{os.path.abspath(ogg)}|{os.path.getmtime(ogg)}|{bitrate}'.encode()).hexdigest()[:12]
    cached = os.path.join(cache_dir, os.path.basename(ogg).replace('.ogg', f'-{bitrate//1000}k-{key}.ogg'))
    if os.path.exists(cached):
        return open(cached, 'rb').read()
    data = _encode_opus(ogg, bitrate)
    open(cached, 'wb').write(data)
    return data


def _encode_opus(ogg, bitrate):
    import av
    c = av.open(ogg)
    s = c.streams.audio[0]
    tmp = tempfile.NamedTemporaryFile(suffix='.ogg', delete=False)
    tmp.close()
    out = av.open(tmp.name, 'w', format='ogg')
    st = out.add_stream('libopus', rate=16000, layout='mono')
    st.bit_rate = bitrate
    res = av.AudioResampler(format='fltp', layout='mono', rate=16000)
    for fr in c.decode(s):
        for o in res.resample(fr):
            o.pts = None
            for pkt in st.encode(o):
                out.mux(pkt)
    for pkt in st.encode(None):
        out.mux(pkt)
    out.close()
    data = open(tmp.name, 'rb').read()
    os.unlink(tmp.name)
    return data


# -------------------------------------------------------------------- page ----
PAGE = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ — وایت‌بورد همگام با صدا</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lalezar&family=Vazirmatn:wght@400;700;800&display=swap">
<style>/* ---- whiteboard (from the lesson's own html) ---- */ __BOARD_CSS__</style>
<style>/* ---- player chrome ---- */
  :root{--bg:#eef1f5;--pnl:#fff;--tx:#1b2430;--mut:#5a6675;--ln:#d3dae2;--ac:#1f4fd1;--hi:#ffe45e;--ok:#16883f;--warn:#c2410c}
  @media (prefers-color-scheme: dark){
    :root{--bg:#14181d;--pnl:#1c222a;--tx:#e6ebf0;--mut:#93a1b1;--ln:#313a45;--ac:#7aa2ff;--hi:#8a6d0b}
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:var(--bg);color:var(--tx);font-family:Vazirmatn,Tahoma,system-ui,sans-serif;display:flex;flex-direction:column;gap:6px;padding:6px;overflow:hidden}
  header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;background:var(--pnl);border:1px solid var(--ln);border-radius:12px;padding:7px 12px}
  h1{font-size:14px;margin:0;font-weight:800;white-space:nowrap}
  .sec{flex:1;min-width:180px;font-size:13px;color:var(--mut);display:flex;align-items:center;gap:8px}
  .sec b{color:var(--tx);font-weight:800;font-size:14px}
  .bar2{height:5px;background:var(--ln);border-radius:9px;overflow:hidden;min-width:70px;flex:0 0 90px}
  .bar2 i{display:block;height:100%;width:0;background:var(--ac);transition:width .4s linear}
  .pill{font-size:12px;color:var(--mut);border:1px solid var(--ln);border-radius:99px;padding:3px 9px;white-space:nowrap}
  .pill b{color:var(--tx)}
  button{font:inherit;font-size:12px;color:inherit;background:var(--pnl);border:1px solid var(--ln);border-radius:8px;padding:4px 9px;cursor:pointer}
  button:hover{border-color:var(--ac)}
  label.chk{display:inline-flex;align-items:center;gap:5px;font-size:12px;color:var(--mut)}
  main{flex:1;min-height:0;display:grid;grid-template-columns:1.15fr 1fr;gap:6px}
  @media (max-width:880px){main{grid-template-columns:1fr;grid-template-rows:1fr 1fr}}
  .pane{background:var(--pnl);border:1px solid var(--ln);border-radius:12px;display:flex;flex-direction:column;min-height:0;overflow:hidden}
  .pane>h2{font-size:12px;margin:0;padding:6px 12px;border-bottom:1px solid var(--ln);color:var(--mut);font-weight:700;display:flex;gap:8px;align-items:center}
  .pane>h2 .tag{font-size:10px;border-radius:6px;padding:1px 6px;background:color-mix(in srgb,var(--warn) 16%,transparent);color:var(--warn)}
  .pane>h2 .tag.ok{background:color-mix(in srgb,var(--ok) 16%,transparent);color:var(--ok)}
  .boardwrap{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden;padding:4px}
  .boardwrap .frame{border-color:#b9c2cc}
  .boardwrap #board{background:#fbfdfe}
  .boardwrap .page{color:#222}
  #text{flex:1;overflow:auto;padding:4px 14px 45vh;font-size:14px;line-height:1.95}
  #text h3{font-size:12px;color:var(--mut);margin:16px 0 2px;font-weight:700;border-right:3px solid var(--ac);padding-right:8px}
  #text .s{border-radius:4px;padding:0 2px;cursor:pointer;transition:background .2s,opacity .2s}
  #text .s.cur{background:var(--hi);color:#111;box-shadow:0 0 0 2px var(--hi);font-weight:700}
  #text .s.done{opacity:.5}
  footer{display:flex;flex-wrap:wrap;gap:8px;align-items:center;background:var(--pnl);border:1px solid var(--ln);border-radius:12px;padding:6px 10px}
  audio{width:100%;max-width:420px;min-width:220px;height:34px}
  #chips{display:flex;flex-wrap:wrap;gap:5px;max-height:74px;overflow:auto;flex:1}
  .chip{font:11px/1.1 inherit;display:inline-flex;gap:5px;align-items:center;border:1px solid var(--ln);border-radius:99px;padding:3px 8px;background:transparent;color:var(--tx);cursor:pointer;white-space:nowrap}
  .chip.cur{border-color:var(--ac);background:color-mix(in srgb,var(--ac) 15%,transparent)}
  .chip i{font-style:normal;color:var(--mut)}
  #err{display:none;background:#7f1d1d;color:#fff;font-size:12px;padding:6px 10px;border-radius:8px;white-space:pre-wrap;max-height:80px;overflow:auto}
  #boot{position:fixed;inset:0;display:grid;place-content:center;gap:8px;text-align:center;background:var(--bg);z-index:9;font-size:14px;color:var(--mut)}
  #boot b{color:var(--tx);font-size:16px}
  .slabel{margin:14px 0 2px;font-size:11px;color:#93a1b1;border-top:1px dashed #cfd6dd;padding-top:6px;display:flex;gap:8px;align-items:center}
  .slabel b{font-weight:800;color:#5a6675}
  .slabel:first-child{border-top:0;margin-top:0}
</style>
</head>
<body>
<div id="boot"><b>__TITLE__</b><div>در حال آماده‌سازی صدا…</div></div>

<header>
  <h1>__TITLE__</h1>
  <div class="sec">
    <b id="secTitle">—</b>
    <span id="secNo" class="pill">۰/۰</span>
    <span class="bar2"><i id="secBar"></i></span>
  </div>
  <span class="pill">زمان <b id="now">۰:۰۰</b> / <b id="dur">—</b></span>
  <button id="minus" title="همگام‌سازی عقب‌تر">−۰٫۵s</button>
  <button id="plus" title="همگام‌سازی جلوتر">+۰٫۵s</button>
  <span class="pill" id="shift">جابه‌جایی: ۰٫۰s</span>
  <label class="chk"><input type="checkbox" id="sync" checked> همگام</label>
  <span class="tag" style="font-size:10px;color:var(--mut)">__MODE__</span>
</header>

<div id="err"></div>

<main>
  <section class="pane">
    <h2>وایت‌بورد <span style="color:var(--mut);font-weight:400">— خط‌به‌خط با صدا</span></h2>
    <div class="boardwrap">
      <div class="frame"><div id="board"><div class="page"></div></div><div class="tray"></div></div>
    </div>
  </section>
  <section class="pane">
    <h2>متن گفتار — جملهٔ در حال خواندن هایلایت می‌شود</h2>
    <div id="text">__TEXT__</div>
  </section>
</main>

<footer>
  <audio id="au" controls preload="auto"></audio>
  <div id="chips"></div>
</footer>

<script id="audio-data" type="text/plain">__AUDIO__</script>
<script>__ENGINE__</script>
<script>__SLIDES__</script>
<script>__DRIVER__</script>
</body>
</html>
"""

DRIVER = r"""
/* ================= sync driver (tools/build_sync.py) ================= */
var SY_SEGS = __SEGS__;          /* [{start,end,slide,title}] */
var SY_STARTS = __STARTS__;      /* sentence start times, same order as the spans */
var SY_DUR = __DUR__;

function syQ(id){ return document.getElementById(id); }
function syFa(n){ return String(n).replace(/[0-9]/g, function(d){ return '۰۱۲۳۴۵۶۷۸۹'[+d]; }); }
function syMM(t){ t = Math.max(0, t); var m = Math.floor(t/60), s = Math.floor(t%60);
  return syFa(m) + ':' + syFa(('0'+s).slice(-2)); }

var syAu = syQ('au'), syText = syQ('text'), syChips = syQ('chips');
var syErrBox = syQ('err');
function syFail(msg){ syErrBox.style.display='block'; syErrBox.textContent = String(msg).slice(0, 600); }
window.addEventListener('error', function(e){ syFail('خطا: ' + (e.message || e.error)); });

function syItems(k){
  var e = S[SY_SEGS[k].slide];
  if (!e) return [];
  var a = (e.length > 2 ? e[2] : e[1]);
  return (a && a.length) ? a : [];
}
function sySegAt(t){
  var lo = 0, hi = SY_SEGS.length - 1, r = 0;
  while (lo <= hi){ var m = (lo + hi) >> 1;
    if (SY_SEGS[m].start <= t){ r = m; lo = m + 1; } else hi = m - 1; }
  return r;
}
/* how many items of section k should be on the board at time t (0-based, -1 = none) */
function syIndexIn(k, t){
  var n = syItems(k).length; if (!n) return -1;
  var seg = SY_SEGS[k], d = seg.end - seg.start;
  var f = (d > 0) ? (t - seg.start) / d : 1;
  f = Math.min(0.999999, Math.max(0, f));
  return Math.floor(f * n);
}
function sySentAt(t){
  var lo = 0, hi = SY_STARTS.length - 1, r = -1;
  while (lo <= hi){ var m = (lo + hi) >> 1;
    if (SY_STARTS[m] <= t){ r = m; lo = m + 1; } else hi = m - 1; }
  return r;
}

/* ---------------- one fresh board per section, drawn in order ---------------- */
var sySeg = -1, syDrawn = 0, syWant = -1, syBusy = false, syGen = 0,
    syCurSent = -1, syCurHdr = -1, syOff = 0;

function syDivider(k){
  var d = document.createElement('div');
  d.className = 'slabel';
  d.innerHTML = '<b>بخش ' + syFa(k + 1) + '</b> <span>' + (SY_SEGS[k].title || '') + '</span>';
  d.title = 'کلیک: پخش از ابتدای این بخش';
  d.style.cursor = 'pointer';
  d.onclick = function(){ syAu.currentTime = SY_SEGS[k].start + syOff + 0.02; syAu.play(); };
  pg.appendChild(d);
}

function syReset(k, j){                     /* new section: wipe, then draw at once */
  syGen++;                                   /* cancels whatever is being drawn */
  pg.replaceChildren(pn);
  sySeg = k; syDrawn = 0; syWant = Math.max(0, j);
  syBusy = false;
  syDivider(k);
  syPump();
}

async function syPump(){
  if (syBusy) return;
  syBusy = true;
  var gen = syGen;
  try {
    while (sySeg >= 0 && syDrawn <= syWant){
      var items = syItems(sySeg);
      if (syDrawn >= items.length) break;
      sp = (syWant - syDrawn > 2) ? 240 : 16;      /* catch-up vs. natural pace */
      try { await run(items[syDrawn]); } catch (e) { /* never leave the board blank */ }
      if (gen !== syGen) return;                   /* the board was reset meanwhile */
      syDrawn++;
    }
  } finally { if (gen === syGen) syBusy = false; }
}

function syTextTick(si){
  if (si === syCurSent) return;
  var prev = syText.querySelector('.s.cur');
  if (prev){ prev.classList.remove('cur'); prev.classList.add('done'); }
  syCurSent = si;
  var el = syText.querySelector('.s[data-i="' + si + '"]');
  if (!el) return;
  el.classList.remove('done');
  el.classList.add('cur');
  var r = el.getBoundingClientRect(), pr = syText.getBoundingClientRect();
  if (r.top < pr.top + 30 || r.bottom > pr.bottom - 50)
    syText.scrollTop += (r.top - pr.top) - syText.clientHeight * 0.35;
}

function syHeader(k, ct){
  var seg = SY_SEGS[k];
  if (k !== syCurHdr){
    syCurHdr = k;
    syQ('secNo').textContent = syFa(k + 1) + '/' + syFa(SY_SEGS.length);
    syQ('secTitle').textContent = seg.title || '';
    var chips = syChips.querySelectorAll('.chip');
    for (var i = 0; i < chips.length; i++) chips[i].classList.toggle('cur', i === k);
  }
  var d = Math.max(0.1, seg.end - seg.start);
  syQ('secBar').style.width = (Math.min(1, Math.max(0, (ct - seg.start) / d)) * 100).toFixed(1) + '%';
}

function syTick(){
  var t = syAu.currentTime || 0;
  var ct = t - syOff;
  syQ('now').textContent = syMM(t);
  var k = sySegAt(ct);
  syHeader(k, ct);
  syTextTick(sySentAt(ct));
  if (!syQ('sync').checked) return;
  var j = syIndexIn(k, ct);
  if (k !== sySeg) syReset(k, j);            /* new section (or a seek backwards) */
  else if (j > syWant){ syWant = j; if (!syBusy) syPump(); }
}

/* ---------------- chips, seek, nudge ---------------- */
(function buildChips(){
  var h = '';
  for (var i = 0; i < SY_SEGS.length; i++){
    h += '<button class="chip" data-i="' + i + '"><i>' + syMM(SY_SEGS[i].start) + '</i>' +
         (SY_SEGS[i].title || '') + '</button>';
  }
  syChips.innerHTML = h;
  var bs = syChips.querySelectorAll('.chip');
  for (var k = 0; k < bs.length; k++){
    bs[k].onclick = (function(el){ return function(){
      syAu.currentTime = SY_SEGS[+el.getAttribute('data-i')].start + syOff + 0.02;
      syAu.play();
    };})(bs[k]);
  }
})();

(function bindText(){
  var bs = syText.querySelectorAll('.s');
  for (var i = 0; i < bs.length; i++){
    bs[i].onclick = (function(el){ return function(){
      syAu.currentTime = (SY_STARTS[+el.getAttribute('data-i')] || 0) + syOff;
      syAu.play();
    };})(bs[i]);
  }
})();

function sySetOff(v){
  syOff = Math.round(v * 10) / 10;
  try { localStorage.setItem('syoff', syOff); } catch(e){}
  syQ('shift').textContent = 'جابه‌جایی: ' + (syOff > 0 ? '+' : syOff < 0 ? '−' : '') +
                             Math.abs(syOff).toFixed(1) + 's';
  sySeg = -1; syCurHdr = -1; syCurSent = -1;
  syTick();
}
syQ('minus').onclick = function(){ sySetOff(syOff - 0.5); };
syQ('plus').onclick = function(){ sySetOff(syOff + 0.5); };
try { syOff = parseFloat(localStorage.getItem('syoff') || '0') || 0; } catch(e){}

/* ---------------- audio ---------------- */
function syB64(s){
  s = s.replace(/\s+/g, '');
  var bin = atob(s), n = bin.length, u = new Uint8Array(n);
  for (var i = 0; i < n; i++) u[i] = bin.charCodeAt(i);
  return u;
}
(function boot(){
  try {
    var bytes = syB64(document.getElementById('audio-data').textContent);
    syAu.src = URL.createObjectURL(new Blob([bytes], {type: 'audio/ogg'}));
  } catch (e) { syFail('صدا بارگذاری نشد: ' + e); }
  pg.appendChild(pn);
  syQ('dur').textContent = syMM(SY_DUR);
  syQ('boot').style.display = 'none';
  syTick();                       /* never start with an empty board */
})();

syAu.addEventListener('timeupdate', syTick);
syAu.addEventListener('seeked', syTick);
syAu.addEventListener('loadedmetadata', syTick);
syAu.addEventListener('play', syTick);
addEventListener('keydown', function(e){
  var tg = (e.target && e.target.tagName) || '';
  if (tg === 'INPUT' || tg === 'SELECT' || tg === 'TEXTAREA') return;
  if (e.code === 'Space'){ e.preventDefault(); syAu.paused ? syAu.play() : syAu.pause(); }
  if (e.code === 'ArrowLeft'){ syAu.currentTime = Math.min(syAu.duration || 0, syAu.currentTime + 5); }
  if (e.code === 'ArrowRight'){ syAu.currentTime = Math.max(0, syAu.currentTime - 5); }
});
setInterval(function(){ if (!syAu.paused) syTick(); }, 200);
if (syOff) sySetOff(syOff);
"""


def text_html(timing):
    sents = timing['sentences']
    secs = timing['sections']
    out, cur = [], -1
    for i, s in enumerate(sents):
        if s['sec'] != cur:
            cur = s['sec']
            ttl = secs[cur]['title'] if cur < len(secs) else ''
            out.append(f'<h3>{esc(ttl)} <span style="font-weight:400">{fmt(s["t"])}</span></h3>')
        out.append(f'<span class="s" data-i="{i}">{esc(s["text"])}</span> ')
    return ''.join(out)


def esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def fmt(t):
    m, s = divmod(max(0.0, t), 60)
    return f'{int(m)}:{int(s):02d}'


def build(folder, base, original_audio=False, bitrate=32000, canon=None):
    ogg = os.path.join(folder, base + '.ogg')
    timing_p = os.path.join(folder, base + '.timing.json')
    if not os.path.exists(timing_p):
        sys.exit(f'missing {timing_p}: run tools/slides_timing.py first')
    timing = json.load(open(timing_p, encoding='utf-8'))

    board_html = os.path.join(folder, base + '.html')
    if os.path.exists(board_html):
        style, engine, slides_js = split_board(open(board_html, encoding='utf-8').read())
        title = nice_title(timing.get('doc'), base)
        mode = 'وایت‌بورد اصلی'
        segs = [{'start': s['start'], 'end': s['end'], 'slide': s['slide'] - 1,
                 'title': s['title']} for s in timing['slides']]
    else:                                    # no board in the repo -> build one from the text
        style, engine, _ = split_board(open(canon, encoding='utf-8').read())
        auto = auto_slides(timing)
        slides_js = 'const S = ' + json.dumps(auto, ensure_ascii=False) + ';'
        title = nice_title(timing.get('doc'), base)
        mode = 'وایت‌بورد ساخته‌شده از متن'
        segs = [{'start': s['start'], 'end': s['end'], 'slide': i, 'title': s['title']}
                for i, s in enumerate(timing['sections'])]
    if not segs:
        sys.exit(f'{base}: no segments')
    segs[0]['start'] = 0.0
    for i in range(len(segs) - 1):           # no gaps, no overlaps
        segs[i]['end'] = max(segs[i]['end'], segs[i]['start'] + 1.0)
        segs[i + 1]['start'] = segs[i]['end']
    segs[-1]['end'] = max(segs[-1]['end'], timing['stats']['duration'])

    raw = open(ogg, 'rb').read() if original_audio else opus_b64(ogg, bitrate)
    b64 = base64.b64encode(raw).decode('ascii')
    audio = '\n'.join(b64[i:i + 400] for i in range(0, len(b64), 400))

    starts = [round(s['t'], 2) for s in timing['sentences']]
    page = (PAGE
            .replace('__TITLE__', title)
            .replace('__BOARD_CSS__', strip_body_rules(style))
            .replace('__MODE__', mode)
            .replace('__TEXT__', text_html(timing))
            .replace('__AUDIO__', audio))
    page = (page
            .replace('__ENGINE__', engine)
            .replace('__SLIDES__', slides_js)
            .replace('__DRIVER__', DRIVER
                     .replace('__SEGS__', json.dumps(segs, ensure_ascii=False))
                     .replace('__STARTS__', json.dumps(starts))
                     .replace('__DUR__', str(round(timing['stats']['duration'], 2)))))
    out = os.path.join(folder, base + '-sync.html')
    open(out, 'w', encoding='utf-8').write(page)
    print(f'{out}  ({os.path.getsize(out) / 1e6:.1f} MB)' + ('' if original_audio else f'  opus {bitrate//1000}k'))
    return out


INDEX = """<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>فصل ۱ ریاضی نهم — وایت‌بوردهای همگام</title>
<style>body{background:#eef1f5;color:#1b2430;font-family:Vazirmatn,Tahoma,sans-serif;margin:0;padding:24px;line-height:2}
@media(prefers-color-scheme:dark){body{background:#14181d;color:#e6ebf0}.c{background:#1c222a;border-color:#313a45}a{color:#7aa2ff}}
h1{font-size:20px;margin:0 0 4px}p{color:#5a6675;font-size:13px;margin:0 0 18px}
.c{display:block;background:#fff;border:1px solid #d3dae2;border-radius:12px;padding:12px 16px;margin:8px 0;text-decoration:none;color:inherit}
.c b{font-size:15px}.c span{display:block;color:#5a6675;font-size:12px}a{color:#1f4fd1}</style></head><body>
<h1>فصل ۱ ریاضی نهم — وایت‌بورد همگام با صدا</h1>
<p>هر درس یک فایل مستقل: صدا (base64) + وایت‌بورد + متن، همه درون همان فایل. بدون نیاز به سرور، آفلاین و با دابل‌کلیک باز می‌شود.</p>
<p>داخل هر فایل: وایت‌بورد با شروع هر بخش تازه می‌شود، خط‌به‌خط و به ترتیب با صدا جلو می‌رود و تا شروع بخش بعدی روی صفحه می‌ماند.
جملهٔ در حال خواندن در ستون متن هایلایت می‌شود. اگر جایی صدا چند دهم جلوتر بود، با دکمه‌های ±۰٫۵s تنظیم کن (ذخیره می‌شود).</p>
__ITEMS__
</body></html>
"""


def nice_title(doc, base):
    t = re.sub(r'\s*\([^)]*\)\s*$', '', doc or base)
    t = re.sub(r'^ریاضی (نهم|دهم|یازدهم|دوازدهم)،\s*', '', t)
    return t.strip(' ،:') or base


def build_index(folder, bases):
    items = []
    for b in bases:
        p = os.path.join(folder, b + '.timing.json')
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding='utf-8'))
        title = nice_title(d.get('doc'), b)
        dur = d['stats']['duration']
        mode = 'وایت‌بورد اصلی' if d.get('slides') else 'وایت‌بورد ساخته‌شده از متن'
        items.append(f'<a class="c" href="{b}-sync.html"><b>{esc(title)}</b>'
                     f'<span>{fmt(dur)} · {len(d["sections"])} بخش · {mode}</span></a>')
    out = os.path.join(folder, 'sync-player.html')
    open(out, 'w', encoding='utf-8').write(INDEX.replace('__ITEMS__', '\n'.join(items)))
    print(out)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('target', help='lesson folder, a lesson base path, or a single lesson dir')
    ap.add_argument('--original-audio', action='store_true', help='embed the untouched ogg (bigger)')
    ap.add_argument('--bitrate', type=int, default=32000)
    a = ap.parse_args()
    t = a.target
    canon = os.path.join(os.path.dirname(HERE), CANON_BOARD)
    if os.path.isdir(t):
        bases = sorted(os.path.splitext(os.path.basename(p))[0]
                       for p in glob.glob(os.path.join(t, '*.ogg')))
        for b in bases:
            build(t, b, a.original_audio, a.bitrate, canon)
        build_index(t, bases)
    else:
        folder = os.path.dirname(os.path.abspath(t)) or '.'
        base = os.path.splitext(os.path.basename(t))[0]
        build(folder, base, a.original_audio, a.bitrate, canon)
