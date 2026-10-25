#!/usr/bin/env python3
"""Estimate whiteboard-section timestamps from the narrated .ogg of a lesson.

No speech recognition is needed.  The narrated text exists next to the audio
(`NNNN.txt`), so the alignment is done on *speech timing* only:

  1. decode the ogg -> 10 ms RMS envelope -> adaptive silence threshold
     -> list of real pause events
  2. split the transcript into sentence units, each with a spoken-length weight
  3. first pass: map cumulative weight onto cumulative *voiced* time
  4. monotone DP that snaps sentence boundaries onto the END of real pauses
     (the moment the voice comes back), then rebuild the time-line
     piecewise-linearly between the snapped anchors, and iterate
  5. report start/end of every `##` section plus per-sentence start/end

Usage:
    python tools/align_ogg.py G09/G09-Math/F01/0101.ogg G09/G09-Math/F01/0101.txt -o out.json
"""
import argparse
import json
import os
import re
import sys

import numpy as np

try:
    import av
except ImportError:                                   # pragma: no cover
    sys.exit("need: pip install av numpy")

SR = 16000
HOP = 160                                    # 10 ms frames
PUNCT = r'[#*>`\[\]_|~،؛:«»()"ـ]'


# ----------------------------------------------------------------- audio ----
def decode(path):
    """ogg -> mono float32 @16 kHz"""
    c = av.open(path)
    s = c.streams.audio[0]
    r = av.AudioResampler(format='s16', layout='mono', rate=SR)
    buf = []
    for fr in c.decode(s):
        for o in r.resample(fr):
            buf.append(o.to_ndarray().reshape(-1))
    return np.concatenate(buf).astype(np.float32) / 32768.0


def envelope(x):
    n = len(x) // HOP
    e = np.sqrt((x[:n * HOP].reshape(n, HOP) ** 2).mean(1) + 1e-12)
    return 20 * np.log10(e + 1e-9)


def pauses(db, off=3.0, min_len=0.10, merge_gap=0.06):
    """silence runs (seconds) + boolean voiced mask per 10 ms frame"""
    thr = np.percentile(db, 12) + off
    voiced = db >= thr
    runs, i, n = [], 0, len(voiced)
    while i < n:
        if not voiced[i]:
            j = i
            while j < n and not voiced[j]:
                j += 1
            runs.append([i / 100.0, j / 100.0])
            i = j
        else:
            i += 1
    merged = []
    for a, b in runs:                        # glue together pauses split by a click
        if merged and a - merged[-1][1] < merge_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= min_len], voiced


# -------------------------------------------------------------- transcript ----
def weight(s):
    t = re.sub(PUNCT, ' ', s)
    t = re.sub(r'\s', '', t)
    return max(3, len(t))


def units(text):
    """-> doc title, section titles (## and the leading #), sentence units"""
    secs, us, doc, cur = [], [], None, None
    for line in text.split('\n'):
        ls = line.strip()
        if not ls:
            continue
        if ls.startswith('## '):
            secs.append(ls[3:].strip())
            cur = len(secs) - 1
            continue
        if ls.startswith('# '):
            doc = ls[2:].strip()
            secs.append(doc)
            cur = len(secs) - 1
            continue
        for s in re.split(r'(?<=[.؟!?])\s+', ls):
            s = s.strip()
            if s:
                us.append({'sec': cur if cur is not None else 0,
                           'text': s, 'w': weight(s)})
    return doc, secs, us


# --------------------------------------------------------------------- dp ----
def dp_anchor(pred, mids, band=18.0, skip=1.0):
    """monotone many-to-one alignment of predictions to pause ends.

    returns [(sentence_index, pause_index), ...]
    """
    K, M = len(pred), len(mids)
    BIG = 1e18
    dp = np.full((K + 1, M + 1), BIG)
    back = np.zeros((K + 1, M + 1), dtype=np.int8)   # 1=use pause, 2=skip sent, 3=match
    dp[0, :] = 0.0
    for k in range(1, K + 1):
        pk = pred[k - 1]
        row, prow = dp[k], dp[k - 1]
        for j in range(1, M + 1):
            best, bt = prow[j] + skip, 2
            if row[j - 1] < best:
                best, bt = row[j - 1], 1
            d = abs(pk - mids[j - 1])
            if d <= band:
                c = prow[j - 1] + d
                if c < best:
                    best, bt = c, 3
            row[j] = best
            back[k, j] = bt
    k, j, pairs = K, M, []
    while k > 0:
        bt = back[k, j]
        if bt == 3:
            pairs.append((k - 1, j - 1)); k -= 1; j -= 1
        elif bt == 2:
            k -= 1
        else:
            j -= 1
            if j < 0:
                break
    pairs.reverse()
    return pairs


# ------------------------------------------------------------------ align ----
def align(ogg, txt, iters=3, band=18.0, verbose=True):
    if os.path.exists(txt):
        txt = open(txt, encoding='utf-8').read()
    x = decode(ogg)
    dur = len(x) / SR
    db = envelope(x)
    plist, voiced = pauses(db)
    # a new sentence starts when the voice comes back: use the END of the pause
    mids = np.array([b for a, b in plist])
    cum_voiced = np.cumsum(voiced) / 100.0
    doc, secs, us = units(txt)

    cw = np.cumsum([u['w'] for u in us]).astype(float)
    total_w, total_v = cw[-1], cum_voiced[-1]
    rate = total_v / total_w                       # voiced seconds per character
    pred = np.interp(cw * rate, cum_voiced, np.arange(len(cum_voiced)) / 100.0).clip(0, dur)

    pairs = []
    for it in range(iters):
        b = band / (2 ** min(it, 3))
        pairs = dp_anchor(pred, mids, band=max(3.0, b), skip=1.0)
        at = np.array([0.0] + [mids[j] for _, j in pairs] + [dur])
        aw = np.array([0.0] + [np.interp(pred[k], pred, cw) for k, _ in pairs] + [total_w])
        keep = np.concatenate(([True], np.diff(at) > 1e-3)) & \
               np.concatenate(([True], np.diff(aw) > 1e-3))
        at, aw = at[keep], aw[keep]
        pred = np.interp(cw, aw, at)

    # `pred` is the time at the END of every sentence (cumulative weight);
    # the sentence itself starts where the previous one ended.
    starts = np.concatenate(([0.0], pred[:-1])) if len(pred) else pred
    resid = np.array([pred[k] - mids[j] for k, j in pairs]) if pairs else np.array([])
    stats = {
        'duration': round(dur, 2),
        'sentences': len(us),
        'pauses': len(plist),
        'anchored': len(pairs),
        'resid_median_s': round(float(np.median(np.abs(resid))), 3) if len(resid) else None,
        'resid_p90_s': round(float(np.percentile(np.abs(resid), 90)), 3) if len(resid) else None,
        'chars_per_voiced_s': round(float(total_w / total_v), 2),
        'voiced_frac': round(float(voiced.mean()), 3),
    }

    sec_out = []
    for i, ttl in enumerate(secs):
        idx = [n for n, u in enumerate(us) if u['sec'] == i]
        st = 0.0 if (not idx or idx[0] == 0) else float(pred[idx[0] - 1])
        sec_out.append({'title': ttl, 'start': round(st, 2), 'units': len(idx)})
    for i in range(len(sec_out) - 1):
        sec_out[i]['end'] = sec_out[i + 1]['start']
    sec_out[-1]['end'] = round(dur, 2)

    if verbose:
        print(json.dumps(stats, ensure_ascii=False))
        for s in sec_out:
            print(f"  {s['start']:8.2f} {s['end']:8.2f}  "
                  f"{int(s['start'] // 60):02d}:{s['start'] % 60:05.2f}  {s['title'][:62]}")
    return {'doc': doc, 'stats': stats, 'sections': sec_out,
            'pauses': [[round(a, 2), round(b, 2)] for a, b in plist],
            'sentences': [{'t': round(float(st), 2), 'end': round(float(en), 2),
                           'sec': u['sec'], 'text': u['text']}
                          for st, en, u in zip(starts, pred, us)]}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('ogg')
    ap.add_argument('txt')
    ap.add_argument('-o', '--out')
    ap.add_argument('--band', type=float, default=18.0)
    a = ap.parse_args()
    res = align(a.ogg, a.txt, band=a.band)
    if a.out:
        json.dump(res, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
