#!/usr/bin/env python3
"""Build the mobile, fully self-contained whiteboard page of a lesson.

    python tools/build_mobile.py G09/G09-Math/F01              # every lesson + index
    python tools/build_mobile.py G09/G09-Math/F01/0101         # one lesson
    python tools/build_mobile.py G09/G09-Math/F01 --bg other.png --bitrate 24000

Output: `G09/G09-Math/F01/mobile/<base>.html` - a single HTML5 file that
contains the classroom background (base64), the narration (base64 Opus) and the
timing data.  Layout follows the background artwork (values measured on the
image, in % of the image box):

    title bar     5.6% .. 12.0%      <- lesson title
    whiteboard   18.2% .. 49.4%      <- board, one fresh page per section
    text panel   54.0% .. 67.0%      <- transcript, exactly 3 lines, auto-scroll
    controls     88.6% .. 94.6%      <- play / prev / next / section dropdown / time
"""
import argparse
import base64
import glob
import io
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ---------------------------------------------------------------- helpers ----
def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def nice_title(doc, base):
    t = re.sub(r'\s*\([^)]*\)\s*$', '', doc or base)
    t = re.sub(r'^ریاضی (نهم|دهم|یازدهم|دوازدهم)،\s*', '', t)
    return t.strip(' ،:') or base


def mmss(t):
    t = max(0.0, float(t))
    return f'{int(t // 60)}:{int(t % 60):02d}'


# ------------------------------------------------------------------- audio ----
def audio_bytes(ogg, bitrate=32000, sr=22050):
    """AAC-LC in MP4 (universal: iOS Safari, Chrome, Firefox, Edge).

    Cached in ~/.cache so rebuilds are instant.
    """
    import hashlib
    cache = os.path.join(os.path.expanduser('~'), '.cache', 'board-sync')
    os.makedirs(cache, exist_ok=True)
    key = hashlib.md5(f'{os.path.abspath(ogg)}|{os.path.getmtime(ogg)}|{bitrate}|{sr}'.encode()).hexdigest()[:12]
    path = os.path.join(cache, os.path.basename(ogg).replace('.ogg', f'-aac{bitrate // 1000}k-{key}.m4a'))
    if os.path.exists(path):
        return open(path, 'rb').read()
    import av
    src = av.open(ogg)
    st_in = src.streams.audio[0]
    tmp = tempfile.NamedTemporaryFile(suffix='.m4a', delete=False)
    tmp.close()
    out = av.open(tmp.name, 'w', format='ipod')
    st = out.add_stream('aac', rate=sr, layout='mono')
    st.bit_rate = bitrate
    res = av.AudioResampler(format='fltp', layout='mono', rate=sr)
    for fr in src.decode(st_in):
        for o in res.resample(fr):
            o.pts = None
            for pkt in st.encode(o):
                out.mux(pkt)
    for pkt in st.encode(None):
        out.mux(pkt)
    out.close()
    data = open(tmp.name, 'rb').read()
    os.unlink(tmp.name)
    open(path, 'wb').write(data)
    return data


def b64(data, width=400):
    """base64 text; width=None -> one single line (required inside CSS/attributes)"""
    s = base64.b64encode(data).decode('ascii')
    if not width:
        return s
    return '\n'.join(s[i:i + width] for i in range(0, len(s), width))


def bg_jpeg(png, quality=82, width=901):
    from PIL import Image
    im = Image.open(png).convert('RGB')
    if width and im.width != width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=quality, optimize=True, progressive=True)
    return b.getvalue()


# ------------------------------------------------------------- board items ----
def split_board(html):
    """-> (board css, engine js, slides js) of a lesson's whiteboard html"""
    style = re.search(r'<style>(.*?)</style>', html, re.S).group(1)
    js = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
    m_arr = re.search(r'\nconst L\d+\s*=\s*\[', js)
    m_end = re.search(r'\nconst S\s*=', js)
    name = re.search(r'\nconst (L\d+)\s*=\s*\[', js).group(1)
    engine = js[:m_arr.start()].replace('async()=>{for(const i of a)await run(i)}]',
                                        'async()=>{for(const i of a)await run(i)},a]')
    if ',a]' not in engine:
        raise ValueError('could not patch sc() to keep item lists')
    slides = js[m_arr.start():m_end.start()] + f'\nconst S={name};'
    return style, engine, slides


def strip_body_rules(css):
    return re.sub(r'(?m)(^|[}\n])\s*(html\s*,\s*body|body)\s*\{[^}]*\}', r'\1', css)


CLEAN = re.compile(r'[@\[\]{}|`*#~\\]')
GREET = ('سلام', 'خداحافظ', 'به روی ماه', 'من مهیار')


def _chunks(text, limit=34, hard=46):
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
    res = []
    for c in out:
        c = re.sub(r'\s+', ' ', c).strip(' ،؛:')
        while len(c) > hard:
            cut = c[:hard]
            k = cut.rfind(' ')
            k = k if k > 20 else hard
            res.append(c[:k])
            c = c[k:].strip()
        if c:
            res.append(c + ('…' if len(c) > limit else ''))
    return res


def auto_slides(timing, max_lines=7):
    """board content for lessons that have no whiteboard html"""
    sents, secs = timing['sentences'], timing['sections']
    out = []
    for i, sec in enumerate(secs):
        mine = [s for s in sents if s['sec'] == i]
        keep = [s for s in mine if not any(g in s['text'] for g in GREET)] or mine
        cand = []
        for s in keep:
            cand += _chunks(s['text'])
        if len(cand) > max_lines:
            idx = sorted({int(round(k * (len(cand) - 1) / (max_lines - 1))) for k in range(max_lines)})
            cand = [cand[k] for k in idx]
        colors = ['O', 'K', 'B', 'G', 'P', 'R']
        items = [colors[n % len(colors)] + t for n, t in enumerate(cand)] or ['B' + sec['title']]
        out.append(['', None, items])
    return out


# -------------------------------------------------------------------- page ----
PAGE = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#0f1a2b">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>__TITLE__</title>
<style>/* whiteboard engine (from the lesson html) */__BOARD_CSS__</style>
<style>
:root{--u:1px}
html,body{margin:0;height:100%;overflow:hidden;background:#e9edf2;
  font-family:Vazirmatn,Tahoma,system-ui,sans-serif;user-select:none;
  -webkit-user-select:none;touch-action:manipulation;-webkit-text-size-adjust:100%}
/* the artwork IS the page: a real <img> layer, stretched to the viewport so that
   every overlay in % of the same box lands exactly on its frame in the artwork */
#bg{position:fixed;inset:0;width:100%;height:100%;object-fit:fill;
  z-index:0;pointer-events:none;-webkit-user-select:none;user-select:none;-webkit-user-drag:none}
#stage{position:fixed;inset:0;z-index:1;width:100vw;height:100vh;height:100dvh}

/* title */
#title{position:absolute;right:19.8%;left:20.4%;top:5.27%;height:6.76%;
  display:flex;align-items:center;justify-content:center;padding:0 4%;overflow:hidden}
#title span{display:block;width:100%;text-align:center;white-space:nowrap;
  color:#123a72;font-weight:900;line-height:1.15;letter-spacing:-.2px;
  text-shadow:0 1px 0 #ffffff66}

/* whiteboard */
#wb{position:absolute;right:4.4%;left:4.3%;top:17.75%;height:30.4%;
  display:flex;flex-direction:column;overflow:hidden;padding:0.6% 1.2%}
#wb .frame{position:static;flex:1;min-height:0;display:flex;flex-direction:column;
  border:0;border-radius:0;background:none;box-shadow:none;overflow:hidden}
#board{flex:1;overflow:hidden;background:none}
#board .page{padding:0;min-height:0;color:#12233b;line-height:1.6;font-size:16px;filter:none}
#board .page .ln{margin:.05em 0;animation:mIn .36s ease both}
#board .page .h1{font-family:Lalezar,Vazirmatn,Tahoma;font-size:1.45em;text-align:center;margin:.05em 0}
#board .page .h2{font-family:Lalezar,Vazirmatn,Tahoma;font-size:1.16em;margin:.05em 0}
#board .page .set{font-size:.97em}
#board .page .dg svg{width:min(100%,380px);margin:.1em auto}
#board .page .rd{color:#d62d2d}
#board .page .fr{font-size:.88em}
#board .page .hl{background-size:100% 75%!important}
#board .page .pen{display:none!important}
@keyframes mIn{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}

/* three-line transcript */
#tx{position:absolute;right:10%;left:6.9%;top:56.5%;height:12%;
  display:flex;flex-direction:column;justify-content:flex-start;overflow:hidden;padding:0 1.6%}
#txin{will-change:transform;transition:transform .36s cubic-bezier(.3,.72,.3,1)}
#tx .l{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center;
  color:#8a929c;font-weight:700;opacity:0;transition:color .28s,opacity .28s}
#tx .l.show{opacity:.5}
#tx .l.cur{color:#123a72;font-weight:900;opacity:1}

/* controls */
#ct{position:absolute;right:9.3%;left:9.0%;top:87.7%;height:6.9%;
  display:flex;align-items:center;gap:2.4%;font-size:calc(15*var(--u))}
.btn{flex:0 0 auto;display:flex;align-items:center;justify-content:center;
  width:2.85em;height:2.85em;border:0;border-radius:50%;background:#ffffffd9;color:#123a72;
  box-shadow:0 1px 3px #0002,inset 0 -2px 0 #0000000f;cursor:pointer}
.btn svg{width:54%;height:54%;fill:currentColor}
.btn.main{width:3.45em;height:3.45em;background:#1f6fd1;color:#fff;box-shadow:0 2px 7px #1f6fd155}
.btn:active{transform:translateY(1px)}
#sel{flex:1 1 auto;min-width:0;height:2.5em;border:0;border-radius:13px;
  background:#ffffffdf;color:#123a72;font-family:inherit;font-weight:700;font-size:.72em;
  padding:0 26px 0 .7em;text-overflow:ellipsis;appearance:none;cursor:pointer;
  box-shadow:0 1px 3px #0002,inset 0 -2px 0 #0000000f;
  background-image:linear-gradient(45deg,transparent 50%,#123a7288 50%),
                   linear-gradient(135deg,#123a7288 50%,transparent 50%);
  background-position:calc(100% - 15px) calc(50% + 1px),calc(100% - 10px) calc(50% + 1px);
  background-size:5px 5px,5px 5px;background-repeat:no-repeat}
#time{flex:0 0 auto;font-size:.7em;font-weight:800;color:#123a72;opacity:.8;
  font-variant-numeric:tabular-nums;direction:ltr}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><filter id="r"><feTurbulence baseFrequency=".035" numOctaves="2" seed="4"/><feDisplacementMap in="SourceGraphic" scale="2.4"/></filter></svg>
<img id="bg" alt="" src="data:image/jpeg;base64,__BG__">
<div id="stage">
  <div id="title"><span>__TITLE__</span></div>
  <div id="wb"><div class="frame"><div id="board"><div class="page"></div></div><div class="tray" style="display:none"></div></div></div>
  <div id="tx"><div id="txin"></div></div>
  <div id="ct">
    <button class="btn" id="pv" aria-label="بخش قبل"><svg viewBox="0 0 24 24"><path d="M18 5v14l-9-7zM7 5h2v14H7z"/></svg></button>
    <button class="btn main" id="pp" aria-label="پخش"><svg viewBox="0 0 24 24" id="ic"><path d="M8 5v14l11-7z"/></svg></button>
    <button class="btn" id="nx" aria-label="بخش بعد"><svg viewBox="0 0 24 24"><path d="M6 5v14l9-7zM15 5h2v14h-2z"/></svg></button>
    <select id="sel" aria-label="بخش‌ها"></select>
    <span id="time">0:00</span>
  </div>
</div>
<audio id="au" preload="auto" playsinline></audio>
<script id="audio-data" type="text/plain">__AUDIO__</script>
<script>__ENGINE__</script>
<script>__SLIDES__</script>
<script>
var M_SEGS = __SEGS__, M_LINES = __LINES__, M_DUR = __DUR__;
var q = function(id){ return document.getElementById(id); };
var fa = function(n){ return String(n).replace(/[0-9]/g, function(d){ return '۰۱۲۳۴۵۶۷۸۹'[+d]; }); };
var mm = function(t){ t = Math.max(0, t || 0);
  return fa(Math.floor(t / 60)) + ':' + fa(('0' + Math.floor(t % 60)).slice(-2)); };

var au = q('au'), txin = q('txin'), sel = q('sel'), stage = q('stage'),
    wb = q('wb'), tx = q('tx'), pgEl = document.querySelector('#board .page');
var seg = -1, drawn = 0, want = -1, busy = false, gen = 0, curLine = -1, curHdr = -1;
var lineH = 24, linesEls = [], u = 1;

function items(k){
  var e = S[M_SEGS[k].slide];
  if (!e) return [];
  var a = e.length > 2 ? e[2] : e[1];
  return (a && a.length) ? a : [];
}
function segAt(t){
  var lo = 0, hi = M_SEGS.length - 1, r = 0;
  while (lo <= hi){ var m = (lo + hi) >> 1;
    if (M_SEGS[m].start <= t){ r = m; lo = m + 1; } else hi = m - 1; }
  return r;
}
function lineAt(t){
  if (!M_LINES.length) return 0;
  var lo = 0, hi = M_LINES.length - 1, r = 0;
  while (lo <= hi){ var m = (lo + hi) >> 1;
    if (M_LINES[m].t <= t){ r = m; lo = m + 1; } else hi = m - 1; }
  return r;
}

/* board font size that fits the frame on the first try */
function planFont(list){
  var w = (wb.clientWidth || 340) - 10, h = (wb.clientHeight || 210) - 10;
  var rows = 0, longest = 10;
  for (var i = 0; i < list.length; i++){
    var it = list[i];
    if (typeof it === 'function'){ rows += 2.6; continue; }
    var t = String(it).slice(1).replace(/\[\[|\]\]/g, '');
    rows += Math.max(1, Math.ceil(t.length / 26));
    longest = Math.max(longest, Math.min(30, t.length));
  }
  rows = Math.max(2.6, rows);
  var byH = h / (rows * 1.62), byW = w / (longest * 0.52);
  return Math.max(11, Math.min(byH, byW, 20));
}

/* never-blank board: section title first, then the items in order */
function sectionTitle(k){
  var d = document.createElement('div');
  d.className = 'ln h2';
  var s = document.createElement('span');
  s.className = 'in';
  s.style.color = '#1f4fd1';
  s.textContent = M_SEGS[k].title || '';
  d.appendChild(s);
  pgEl.appendChild(d);
}
function reset(k, j){
  gen++;
  pgEl.replaceChildren(pn);
  seg = k; drawn = 0; want = Math.max(0, j); busy = false;
  pgEl.style.fontSize = planFont(items(k)).toFixed(2) + 'px';
  sectionTitle(k);
  q('board').scrollTop = 0;
  pump();
}
function shrink(){
  var f = parseFloat(pgEl.style.fontSize) || 16;
  var bdEl = q('board');
  while (pgEl.scrollHeight > bdEl.clientHeight + 2 && f > 11.5){
    f *= 0.94;
    pgEl.style.fontSize = f.toFixed(2) + 'px';
  }
  if (pgEl.scrollHeight > bdEl.clientHeight + 2) bdEl.scrollTop = pgEl.scrollHeight;
}
function pump(){
  if (busy) return;
  busy = true;
  var g = gen;
  (async function(){
    try {
      while (drawn <= want){
        var list = items(seg);
        if (drawn >= list.length) break;
        sp = (want - drawn > 2) ? 220 : 20;
        try { await run(list[drawn]); } catch (e) {}
        if (g !== gen) return;
        drawn++;
        shrink();
      }
    } finally { if (g === gen) busy = false; }
  })();
}
function indexIn(k, t){
  var n = items(k).length;
  if (!n) return -1;
  var s = M_SEGS[k], d = s.end - s.start;
  var f = d > 0 ? (t - s.start) / d : 1;
  return Math.floor(Math.min(0.999999, Math.max(0, f)) * n);
}

/* transcript: exactly three visible rows, the current one in the middle */
function buildLines(){
  var w = (tx.clientWidth || 300) * 0.96;
  /* the artwork panel has three ruled rows between 56.6% and 65.6% of its height */
  var Hpx = stage.clientHeight || 800;
  lineH = Math.max(14, Math.round(Hpx * (65.64 - 56.56) / 200));   /* = row span */
  var base = Math.max(10.5, Math.min(19, lineH * 0.58));
  var maxChars = Math.max(13, Math.floor(w / (base * 0.55)));
  var out = [];
  for (var i = 0; i < M_LINES.length; i++){
    var L = M_LINES[i], t = String(L.text).replace(/\s+/g, ' ').trim();
    var parts = [], buf = '', words = t.split(' ');
    for (var wI = 0; wI < words.length; wI++){
      var wd = words[wI];
      if (!buf) buf = wd;
      else if ((buf + ' ' + wd).length <= maxChars) buf += ' ' + wd;
      else { parts.push(buf); buf = wd; }
    }
    if (buf) parts.push(buf);
    var total = t.length || 1, acc = 0, span = Math.max(0.4, (L.e - L.t)) / total;
    for (var p = 0; p < parts.length; p++){
      out.push({t: L.t + span * acc, e: L.t + span * (acc + parts[p].length + 1), text: parts[p]});
      acc += parts[p].length + 1;
    }
  }
  M_LINES = out;
  txin.innerHTML = '';
  linesEls = [];
  for (var k = 0; k < out.length; k++){
    var d = document.createElement('div');
    d.className = 'l';
    d.style.height = d.style.lineHeight = lineH + 'px';
    d.style.fontSize = base.toFixed(2) + 'px';
    d.textContent = out[k].text;
    txin.appendChild(d);
    linesEls.push(d);
  }
  var Hpx = stage.clientHeight || 800;
  tx.style.height = tx.style.minHeight = (lineH * 3) + 'px';
  tx.style.lineHeight = lineH + 'px';
  /* centre the three rows on the artwork: first rule at 56.56%, last at 65.64% */
  tx.style.top = (Hpx * 0.5656 - lineH / 2) + 'px';
  tx.style.bottom = 'auto';
  curLine = -1;
}
function showLine(i){
  if (i === curLine || !linesEls.length) return;
  curLine = i;
  var shift = Math.min(0, -(i - 1) * lineH);
  txin.style.transform = 'translate3d(0,' + shift + 'px,0)';
  for (var k = 0; k < linesEls.length; k++){
    var vis = k >= i - 1 && k <= i + 1;
    linesEls[k].style.visibility = vis ? 'visible' : 'hidden';
    if (!vis) linesEls[k].classList.remove('cur');
    linesEls[k].classList.toggle('cur', k === i);
  }
}

function buildSelect(){
  var h = '';
  for (var i = 0; i < M_SEGS.length; i++)
    h += '<option value="' + i + '">' + fa(i + 1) + '. ' + (M_SEGS[i].title || '') + '</option>';
  sel.innerHTML = h;
}
function header(k){ if (k !== curHdr){ curHdr = k; sel.value = String(k); } }
function setIcon(on){
  q('ic').innerHTML = on ? '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>' : '<path d="M8 5v14l11-7z"/>';
}
function fitTitle(){
  var el = q('title'), sp = el.querySelector('span');
  if (!el.clientWidth){ el.style.fontSize = (17 * u) + 'px'; return; }
  var size = 22 * u;
  el.style.fontSize = size + 'px';
  var guard = 0;
  while (guard++ < 40 && (sp.scrollWidth > el.clientWidth - 8 || sp.scrollHeight > el.clientHeight - 2)){
    size *= 0.94;
    el.style.fontSize = size.toFixed(2) + 'px';
    if (size < 8) break;
  }
}
function tick(){
  var t = au.currentTime || 0;
  q('time').textContent = mm(t);
  var k = segAt(t);
  header(k);
  if (k !== seg) reset(k, indexIn(k, t));
  else {
    var j = indexIn(k, t);
    if (j > want){ want = j; if (!busy) pump(); }
  }
  showLine(lineAt(t));
}
function b64(s){
  s = s.replace(/\s+/g, '');
  var bin = atob(s), n = bin.length, out = new Uint8Array(n);
  for (var i = 0; i < n; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function layout(){
  u = Math.max(0.75, Math.min(1.6, stage.clientWidth / 390));
  document.documentElement.style.setProperty('--u', u + 'px');
  fitTitle();
  buildLines();
  if (seg >= 0){ pgEl.style.fontSize = planFont(items(seg)).toFixed(2) + 'px'; shrink(); }
  curLine = -1;
}
(function boot(){
  try {
    au.src = URL.createObjectURL(new Blob([b64(document.getElementById('audio-data').textContent)],
                                          {type: 'audio/mp4'}));
  } catch (e) {}
  pgEl.appendChild(pn);
  buildSelect();
  layout();
  tick();
  var lastT = 0;
  (function loop(now){
    if (!now || now - lastT > 160){ lastT = now || 0; tick(); }
    requestAnimationFrame(loop);
  })();
})();
q('pp').onclick = function(){ au.paused ? au.play() : au.pause(); };
au.addEventListener('play', function(){ setIcon(true); });
au.addEventListener('pause', function(){ setIcon(false); });
au.addEventListener('ended', function(){ setIcon(false); });
au.addEventListener('timeupdate', tick);
au.addEventListener('seeked', tick);
au.addEventListener('loadedmetadata', tick);
q('nx').onclick = function(){ var k = Math.min(M_SEGS.length - 1, seg + 1);
  au.currentTime = M_SEGS[k].start + 0.02; au.play(); };
q('pv').onclick = function(){ var k = Math.max(0, segAt(au.currentTime) - 1);
  au.currentTime = M_SEGS[k].start + 0.02; au.play(); };
sel.onchange = function(){ au.currentTime = M_SEGS[+sel.value].start + 0.02; au.play(); };
addEventListener('resize', layout);
addEventListener('orientationchange', function(){ setTimeout(layout, 250); });
</script>
</body>
</html>
"""


INDEX = """<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>درس‌ها</title>
<style>
body{margin:0;padding:26px 18px;background:#0f1a2b;color:#eaf0f7;
 font-family:Vazirmatn,Tahoma,system-ui;line-height:1.9}
h1{font-size:19px;margin:0 0 16px}
a{display:block;text-decoration:none;color:inherit;background:#182a44;border:1px solid #ffffff1a;
 border-radius:16px;padding:14px 16px;margin:10px 0}
a b{font-size:15px}a span{display:block;color:#9fb2c9;font-size:12px}
</style></head><body>
<h1>فصل ۱ ریاضی نهم</h1>
__ITEMS__
</body></html>
"""


# ------------------------------------------------------------------- build ----
def build(folder, base, bg_png, bitrate=32000, quality=82, out_dir=None):
    timing_p = os.path.join(folder, base + '.timing.json')
    if not os.path.exists(timing_p):
        sys.exit(f'missing {timing_p}: run tools/slides_timing.py first')
    timing = json.load(open(timing_p, encoding='utf-8'))
    title = nice_title(timing.get('doc'), base)
    out_dir = out_dir or os.path.join(folder, 'mobile')
    os.makedirs(out_dir, exist_ok=True)

    board = os.path.join(folder, base + '.html')
    if os.path.exists(board):
        css, engine, slides_js = split_board(open(board, encoding='utf-8').read())
        segs = [{'start': s['start'], 'end': s['end'], 'slide': s['slide'] - 1, 'title': s['title']}
                for s in timing.get('slides') or []]
    else:
        css, engine, _ = split_board(open(CANON_BOARD, encoding='utf-8').read())
        auto = auto_slides(timing)
        slides_js = 'const S=' + json.dumps(auto, ensure_ascii=False) + ';'
        segs = [{'start': s['start'], 'end': s['end'], 'slide': i, 'title': s['title']}
                for i, s in enumerate(timing['sections'])]
    if not segs:
        sys.exit(f'{base}: no segments')

    segs[0]['start'] = 0.0
    for i in range(len(segs) - 1):                  # no gaps, no overlaps
        segs[i]['end'] = max(segs[i]['end'], segs[i]['start'] + 1.0)
        segs[i + 1]['start'] = segs[i]['end']
    segs[-1]['end'] = max(segs[-1]['end'], timing['stats']['duration'])

    lines = [{'t': s['t'], 'e': max(s['t'] + 0.4, s['end']), 'text': s['text']}
             for s in timing['sentences']]
    audio = b64(audio_bytes(os.path.join(folder, base + '.ogg'), bitrate))
    bg = b64(bg_jpeg(bg_png, quality=quality), width=None)

    page = (PAGE
            .replace('__TITLE__', esc(title))
            .replace('__TITLEJS__', json.dumps(title, ensure_ascii=False))
            .replace('__BOARD_CSS__', strip_body_rules(css))
            .replace('__BG__', bg)
            .replace('__AUDIO__', audio)
            .replace('__ENGINE__', engine)
            .replace('__SLIDES__', slides_js)
            .replace('__SEGS__', json.dumps(segs, ensure_ascii=False))
            .replace('__LINES__', json.dumps(lines, ensure_ascii=False))
            .replace('__DUR__', str(round(timing['stats']['duration'], 2))))
    out = os.path.join(out_dir, base + '.html')
    open(out, 'w', encoding='utf-8').write(page)
    print(f'{out}  ({os.path.getsize(out) / 1e6:.1f} MB)  {len(segs)} بخش  '
          f'bg {len(bg) // 1024}KB  audio {len(audio_bytes(os.path.join(folder, base + ".ogg"), bitrate)) // 1024}KB')
    return out, title, timing


def build_index(folder, out_dir, rows):
    items = []
    for base, title, timing in rows:
        items.append(f'<a href="{base}.html"><b>{esc(title)}</b>'
                     f'<span>{mmss(timing["stats"]["duration"])} · '
                     f'{len(timing["sections"])} بخش</span></a>')
    open(os.path.join(out_dir, 'index.html'), 'w', encoding='utf-8').write(
        INDEX.replace('__ITEMS__', '\n'.join(items)))
    print(os.path.join(out_dir, 'index.html'))


REPO = os.path.dirname(HERE)
CANON_BOARD = os.path.join(REPO, 'G09/G09-Math/F01/0101.html')
DEFAULT_BG = os.path.join(REPO, 'Cheerful Persian Study Classroom Interface.png')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('target', help='lesson folder or a single lesson base path')
    ap.add_argument('--bg', default=None, help='classroom background png')
    ap.add_argument('--bitrate', type=int, default=32000)
    ap.add_argument('--quality', type=int, default=82)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    if os.path.isdir(a.target):
        folder = a.target
        bg = a.bg or DEFAULT_BG
        rows = []
        for p in sorted(glob.glob(os.path.join(folder, '*.ogg'))):
            base = os.path.splitext(os.path.basename(p))[0]
            out, title, timing = build(folder, base, bg, a.bitrate, a.quality, a.out)
            rows.append((base, title, timing))
        build_index(folder, a.out or os.path.join(folder, 'mobile'), rows)
    else:
        folder = os.path.dirname(os.path.abspath(a.target)) or '.'
        base = os.path.splitext(os.path.basename(a.target))[0]
        bg = a.bg or DEFAULT_BG
        build(folder, base, bg, a.bitrate, a.quality, a.out)
