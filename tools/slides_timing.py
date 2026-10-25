#!/usr/bin/env python3
"""Map whiteboard slides (`sc(...)` entries of the board HTML) onto the
transcript sections of a lesson and therefore onto audio timestamps.

    python tools/slides_timing.py G09/G09-Math/F01/0101 --json G09/G09-Math/F01/0101.timing.json

The audio alignment itself is produced by tools/align_ogg.py; this script
re-uses it (and caches the expensive part via the .timing.json file).
"""
import argparse
import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import align_ogg as A                                     # noqa: E402

STOP = set('و در به از با که این آن را یه یک تا هم یا برای روی های ها'.split())


# ---------------------------------------------------------------- parsing ----
def _scan_string(s, i):
    """read a JS string literal starting at s[i] == quote -> (value, next index)"""
    q = s[i]
    i += 1
    out = []
    while i < len(s):
        ch = s[i]
        if ch == '\\':
            nxt = s[i + 1]
            if nxt == 'u':
                out.append(chr(int(s[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append({'n': '\n', 't': '\t'}.get(nxt, nxt))
            i += 2
            continue
        if ch == q:
            return ''.join(out), i + 1
        out.append(ch)
        i += 1
    raise ValueError('unterminated string')


def board_slides(html):
    """-> [(title, n_items)] for every sc('title',[...]) of the board"""
    m = re.search(r'const\s+L\d+\s*=\s*\[', html) or re.search(r'const\s+S\s*=\s*\[', html)
    start = m.start()
    s = html[start:]
    slides = []
    i = 0
    while True:
        k = s.find('sc(', i)
        if k < 0:
            break
        # must be a top-level entry: previous non-space char is '[' or ','
        p = k - 1
        while p >= 0 and s[p] in ' \n\t':
            p -= 1
        if p < 0 or s[p] not in '[,[':
            i = k + 3
            continue
        j = k + 3
        while s[j] in ' \n\t':
            j += 1
        title, j = _scan_string(s, j)
        while s[j] in ' ,\n\t':
            j += 1
        if s[j] != '[':
            i = k + 3
            continue
        depth, j2, items, quotes = 0, j, 0, None
        while j2 < len(s):
            ch = s[j2]
            if quotes:
                if ch == '\\':
                    j2 += 2
                    continue
                if ch == quotes:
                    quotes = None
            elif ch in '\'"`':
                quotes = ch
            elif ch in '[({':
                depth += 1
            elif ch in '])}':
                depth -= 1
                if depth == 0:
                    break
            elif ch == ',' and depth == 1:
                items += 1
            j2 += 1
        body = s[j + 1:j2].strip()
        n_items = 0 if not body else items + 1
        slides.append((title, n_items))
        i = j2
    return slides


# --------------------------------------------------------------- matching ----
def toks(t):
    t = re.sub(r'[\u064B-\u0652\u200c]', ' ', t)
    t = re.sub(r'[^\w\u0600-\u06FF]+', ' ', t)
    return {w for w in t.split() if len(w) > 2 and w not in STOP}


def sim(a, b):
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def match(slides, sections, sec_len=None, tpi=None):
    """Monotone segmentation: every slide belongs to exactly one section
    (a section may own several slides, or none).

    slides   : [(title, n_items)]
    sections : [title]
    sec_len  : [duration per section] (optional, used as a weak prior)
    tpi      : expected seconds per board item (optional)

    returns list (one entry per section) of slide index lists
    """
    L, S = len(slides), len(sections)
    BIG = 1e9
    EMPTY = 0.8          # cost of a section that owns no slide
    PER_EXTRA = 0.35     # cost per additional slide in a group
    DUR_W = 0.5          # weight of the duration prior
    simc = np.zeros((S, L + 1, L + 1))       # sim costs of group k+1..i for section j
    for j in range(S):
        for k in range(L):
            run = 0.0
            for i in range(k + 1, L + 1):
                m = 1.0 - 2.0 * sim(slides[i - 1][0], sections[j])
                if sec_len and tpi:
                    exp = max(6.0, tpi * sum(slides[x][1] for x in range(k, i)))
                    m += DUR_W * min(2.0, abs(np.log(max(1e-3, sec_len[j] / exp))))
                if i - k > 1:
                    m += PER_EXTRA
                run += m
                simc[j, k, i] = run
    dp = np.full((S + 1, L + 1), BIG)
    bk = np.zeros((S + 1, L + 1), dtype=np.int32)
    dp[0, 0] = 0.0
    for j in range(1, S + 1):
        for i in range(L + 1):
            best, bt = dp[j - 1, i] + EMPTY, i        # section j-1 is empty
            for k in range(i):
                c = dp[j - 1, k] + simc[j - 1, k, i]
                if c < best:
                    best, bt = c, k
            dp[j, i], bk[j, i] = best, bt
    j, i, groups = S, L, []
    while j > 0:
        k = int(bk[j, i])
        groups.append(list(range(k, i)))
        i, j = k, j - 1
    groups.reverse()
    return groups


# ------------------------------------------------------------------- main ----
def build(lesson_dir, base, out=None, band=18.0):
    ogg = os.path.join(lesson_dir, base + '.ogg')
    txt = os.path.join(lesson_dir, base + '.txt')
    html = os.path.join(lesson_dir, base + '.html')
    cache = out or os.path.join(lesson_dir, base + '.timing.json')

    if os.path.exists(cache):
        data = json.load(open(cache, encoding='utf-8'))
        if 'sentences' in data and 'sections' in data:
            print(f'{base}: using cached {os.path.basename(cache)}')
        else:
            data = None
    else:
        data = None
    if data is None:
        if not (os.path.exists(ogg) and os.path.exists(txt)):
            print(f'{base}: missing ogg/txt, skipped')
            return None
        res = A.align(ogg, txt, band=band, verbose=False)
        data = {'base': base,
                'audio': os.path.basename(ogg),
                'board': os.path.basename(html) if os.path.exists(html) else None,
                'doc': res['doc'],
                'method': 'energy-pause + proportional speech-rate alignment (no ASR)',
                'stats': res['stats'],
                'sections': res['sections'],
                'sentences': res['sentences']}

    # ---- slides
    if data.get('board') and os.path.exists(os.path.join(lesson_dir, data['board'])):
        html_txt = open(os.path.join(lesson_dir, data['board']), encoding='utf-8').read()
        slides = board_slides(html_txt)
    else:
        slides = []

    secs = data['sections']
    sents = data['sentences']
    if slides:
        sec_titles = [s['title'] for s in secs]
        sec_len = [max(1.0, s['end'] - s['start']) for s in secs]
        tpi = data['stats']['duration'] / max(1, sum(n for _, n in slides))
        groups = match(slides, sec_titles, sec_len=sec_len, tpi=tpi)

        by_sec = {}
        for n, u in enumerate(sents):
            by_sec.setdefault(u['sec'], []).append(n)

        segs = []                                  # (start, end, slide_idx|None)
        for j, si in enumerate(secs):
            idx = by_sec.get(j, [])
            owners = groups[j] if j < len(groups) else []
            if not owners or not idx:
                segs.append([si['start'], si['end'], None, j])
                continue
            share = [max(1, slides[o][1]) for o in owners]
            tot, acc, cuts = sum(share), 0, []
            for sh in share:
                acc += sh
                cuts.append(int(round(len(idx) * acc / tot)))
            cuts[-1] = len(idx)
            prev = 0
            for o, cut in zip(owners, cuts):
                span = idx[prev:cut]
                prev = cut
                if not span:
                    continue
                st = 0.0 if span[0] == 0 else sents[span[0] - 1]['t']
                en = si['end'] if cut >= len(idx) else sents[span[-1]]['t']
                segs.append([st, en, o, j])

        # sections without a slide are folded into the neighbouring slide
        for n, sg in enumerate(segs):
            if sg[2] is not None:
                continue
            prev = next((x for x in reversed(segs[:n]) if x[2] is not None), None)
            nxt = next((x for x in segs[n + 1:] if x[2] is not None), None)
            if prev is not None:
                prev[1] = max(prev[1], sg[1])
            elif nxt is not None:
                nxt[0] = min(nxt[0], sg[0])

        out_slides = []
        for st, en, o, j in segs:
            if o is None:
                continue
            out_slides.append({'slide': o + 1, 'title': slides[o][0],
                               'start': round(st, 2), 'end': round(en, 2),
                               'items': slides[o][1], 'section': secs[j]['title']})
        out_slides.sort(key=lambda x: (x['start'], x['slide']))
        data['slides'] = out_slides
        data['stats']['slides'] = len(slides)
        data['stats']['slides_mapped'] = len(out_slides)

    json.dump(data, open(cache, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"{base}: {data['stats']}")
    for s in data.get('slides', []):
        print(f"   slide {s['slide']:2d} {s['start']:7.2f}-{s['end']:7.2f}  {s['title'][:44]}")
    return data


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('target', help='lesson base path without extension, e.g. .../F01/0101')
    ap.add_argument('--band', type=float, default=18.0)
    a = ap.parse_args()
    d = os.path.dirname(os.path.abspath(a.target))
    b = os.path.splitext(os.path.basename(a.target))[0]
    build(d, b, band=a.band)
