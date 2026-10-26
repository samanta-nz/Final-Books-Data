#!/usr/bin/env python3
"""Align an existing Persian transcript to recognized words, preserving its text.

Requires faster-whisper, rapidfuzz, numpy and ffmpeg. ASR cache is resumable;
recognition spelling is never substituted into the published transcript.
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import time

import numpy as np
from rapidfuzz.distance import Levenshtein
from rapidfuzz.fuzz import partial_ratio_alignment
from mobile_audio import decode as decode_audio, audio_fingerprint


def norm(text):
    text = text.translate(str.maketrans({'ي':'ی','ك':'ک','ى':'ی','ۀ':'ه','ؤ':'و','أ':'ا','إ':'ا','آ':'ا'}))
    text = re.sub(r'[\u064b-\u065f\u0670\u200c\u200d]', '', text)
    return ''.join(c for c in text.lower() if c.isalnum())


def integer_fa(value):
    small = ['صفر','یک','دو','سه','چهار','پنج','شش','هفت','هشت','نه','ده','یازده','دوازده','سیزده','چهارده','پانزده','شانزده','هفده','هجده','نوزده']
    tens = ['', '', 'بیست','سی','چهل','پنجاه','شصت','هفتاد','هشتاد','نود']
    hundreds = ['', 'صد','دویست','سیصد','چهارصد','پانصد','ششصد','هفتصد','هشتصد','نهصد']
    if value < 20: return small[value]
    if value < 100: return tens[value//10] + ('و'+integer_fa(value%10) if value%10 else '')
    if value < 1000: return hundreds[value//100] + ('و'+integer_fa(value%100) if value%100 else '')
    if value < 1000000: return (integer_fa(value//1000) if value//1000>1 else '')+'هزار'+('و'+integer_fa(value%1000) if value%1000 else '')
    return str(value)


def acoustic_norm(text):
    # Recognition often writes a spoken integer as digits and uses homophones.
    # This normalization is for alignment only; published text stays untouched.
    text = re.sub(r'\d+', lambda m: integer_fa(int(m.group())), text)
    return norm(text).translate(str.maketrans({'ث':'س','ص':'س','ذ':'ز','ض':'ز','ظ':'ز','ط':'ت','ح':'ه','ع':'ا','غ':'ق','ئ':'ی'}))


def recognize(path, cache, pipeline):
    key = audio_fingerprint(path)
    target = cache / (path.stem + '.words.json')
    old = json.loads(target.read_text()) if target.exists() else {}
    if old.get('audio_sha256') == key and old.get('complete'):
        return old
    audio = decode_audio(path)
    duration = len(audio) / 16000
    data = {'audio_sha256': key, 'model': 'small', 'language': 'fa',
            'duration': duration, 'chunks': old.get('chunks', []) if old.get('audio_sha256') == key else [],
            'complete': False}
    for offset in range(0, int(np.ceil(duration)), 240):
        if any(c['offset'] == offset for c in data['chunks']):
            continue
        stop = min(duration, offset + 240)
        clips = [{'start': float(i - offset), 'end': min(stop - offset, i - offset + 30.)}
                 for i in range(offset, int(np.ceil(stop)), 30)]
        started = time.monotonic()
        segments, _ = pipeline.transcribe(
            audio[offset * 16000:int(stop * 16000)].copy(), language='fa',
            beam_size=3, temperature=0, condition_on_previous_text=False,
            word_timestamps=True, vad_filter=False, clip_timestamps=clips,
            batch_size=4)
        words = []
        for segment in segments:
            for word in segment.words or []:
                words.append({'text': word.word.strip(), 't': round(offset + word.start, 3),
                              'end': round(offset + word.end, 3), 'p': round(word.probability, 3)})
        data['chunks'].append({'offset': offset, 'end': stop, 'words': words})
        target.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        print(f'{path.stem}: {stop:.0f}/{duration:.0f}s, {len(words)} words, '
              f'{time.monotonic()-started:.1f}s compute', flush=True)
    data['chunks'].sort(key=lambda c: c['offset'])
    data['complete'] = True
    target.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data


def align(timing, recognized):
    sentences = timing['sentences']
    original = [dict(s) for s in sentences]
    source = ''.join(acoustic_norm(s['text']) for s in sentences)
    source_starts = np.cumsum([0] + [len(acoustic_norm(s['text'])) for s in sentences])
    words = [w for c in recognized['chunks'] for w in c['words'] if norm(w['text'])]
    target, char_times, char_ends, probabilities = '', [], [], []
    for w in words:
        token = acoustic_norm(w['text'])
        target += token
        # A recognized word is the acoustic unit. Never invent intra-word offsets.
        char_times.extend([w['t']] * len(token))
        char_ends.extend([w['end']] * len(token))
        probabilities.extend([w['p']] * len(token))
    mapped = np.full(len(source), np.nan)
    mapped_end = np.full(len(source), np.nan)
    matched = np.zeros(len(source), dtype=bool)
    reference=[]
    for s in sentences:
        n=len(acoustic_norm(s['text']))
        reference.extend(np.linspace(s.get('reference_t',s['t']),s.get('reference_end',s['end']),max(1,n))[:n])
    for op in Levenshtein.opcodes(source, target):
        if op.tag != 'equal':
            continue
        # Tiny accidental character matches are insufficient acoustic anchors.
        if op.src_end - op.src_start < 12:
            continue
        for a, b in zip(range(op.src_start, op.src_end), range(op.dest_start, op.dest_end)):
            if probabilities[b] >= .12 and abs(char_times[b]-reference[a])<45:
                mapped[a] = char_times[b]
                mapped_end[a] = char_ends[b]
                matched[a] = True
    good = np.flatnonzero(np.isfinite(mapped))
    if len(good)<len(source)*.15:
        # A failed ASR crop must not shift the whole remaining lesson. Match
        # original sentences inside bounded acoustic neighborhoods instead.
        target_times=np.array(char_times)
        for i,s in enumerate(sentences):
            a,b=source_starts[i:i+2]
            snippet=source[a:b]
            if len(snippet)<15:continue
            left=np.searchsorted(target_times,s.get('reference_t',s['t'])-35)
            right=np.searchsorted(target_times,s.get('reference_end',s['end'])+35)
            if right<=left:continue
            found=partial_ratio_alignment(snippet,target[left:right],score_cutoff=65)
            if found is None:continue
            start=left+found.dest_start;end=left+found.dest_end
            for op in Levenshtein.opcodes(snippet,target[start:end]):
                if op.tag!='equal' or op.src_end-op.src_start<4:continue
                for x,y in zip(range(op.src_start,op.src_end),range(op.dest_start,op.dest_end)):
                    if abs(char_times[start+y]-reference[a+x])<45:
                        mapped[a+x]=char_times[start+y];mapped_end[a+x]=char_ends[start+y];matched[a+x]=True
        good=np.flatnonzero(np.isfinite(mapped))
    if len(good) < len(source) * .15:
        raise ValueError('Insufficient transcript/ASR agreement; refusing proportional fallback')
    all_pos = np.arange(len(source))
    # np.interp otherwise holds the last anchor constant, collapsing an entire
    # unrecognized outro (or intro) to one timestamp.
    rate=len(source)/recognized['duration']
    anchor_pos=good.astype(float)
    anchor_t=mapped[good]
    anchor_end=mapped_end[good]
    if good[0]>0:
        prefix=max(0,float(anchor_t[0])-good[0]/rate)
        anchor_pos=np.concatenate(([0.],anchor_pos));anchor_t=np.concatenate(([prefix],anchor_t));anchor_end=np.concatenate(([prefix],anchor_end))
    if good[-1]<len(source)-1:
        anchor_pos=np.concatenate((anchor_pos,[float(len(source)-1)]));anchor_t=np.concatenate((anchor_t,[recognized['duration']]));anchor_end=np.concatenate((anchor_end,[recognized['duration']]))
    interpolated = np.interp(all_pos, anchor_pos, anchor_t)
    interpolated_end = np.interp(all_pos, anchor_pos, anchor_end)
    # Only a coarse crop selector: prevent outlier matches from selecting audio
    # minutes away. The published times are refined acoustically by force_align.
    interpolated=np.maximum.accumulate(interpolated)
    interpolated_end=np.maximum(interpolated,interpolated_end)
    reviews = []
    for i, s in enumerate(sentences):
        a, b = source_starts[i:i+2]
        hits = np.flatnonzero(matched[a:b]) + a
        coverage = len(hits) / max(1, b-a)
        start = float(interpolated[a])
        end = float(interpolated_end[max(a, b-1)])
        # Caption boundaries are anchored to words, not to the preceding silence.
        s['t'], s['end'] = round(start, 3), round(max(start+.05, end), 3)
        s['alignment'] = {'matched_char_fraction': round(coverage, 3)}
        s['words'] = []
        local = a
        for token in re.findall(r'\S+', s['text']):
            length = len(acoustic_norm(token))
            if not length:
                continue
            t = float(interpolated[min(local, len(source)-1)])
            e = float(interpolated_end[min(local+length-1, len(source)-1)])
            s['words'].append({'text': token, 't': round(t, 3), 'end': round(max(t, e), 3)})
            local += length
        if coverage < .40 or abs(start - original[i]['t']) > 45:
            reviews.append({'sentence': i, 'coverage': round(coverage, 3),
                            'old_start': original[i]['t'], 'new_start': s['t'], 'text': s['text']})
    duration = recognized['duration']
    for i, s in enumerate(sentences):
        if i:
            s['t'] = max(sentences[i-1]['t'], s['t'])
        if i+1 < len(sentences):
            s['end'] = min(s['end'], max(s['t']+.01, sentences[i+1]['t']))
        s['end'] = min(duration, max(s['t']+.01, s['end']))
    for i, sec in enumerate(timing['sections']):
        owned = [s for s in sentences if s['sec'] == i]
        sec['start'] = 0 if i == 0 else owned[0]['t']
        sec['units'] = len(owned)
    for i, sec in enumerate(timing['sections']):
        sec['end'] = timing['sections'][i+1]['start'] if i+1 < len(timing['sections']) else round(duration, 3)
    timing.pop('slides', None)
    timing['method'] = 'Persian ASR word timestamps + monotone transcript character alignment'
    timing['stats'] = {'duration': round(duration, 3), 'sentences': len(sentences),
                       'recognized_words': len(words), 'matched_char_fraction': round(len(good)/len(source), 3),
                       'review_sentences': len(reviews), 'slides': len(timing['sections'])}
    timing['alignment_review'] = reviews
    timing['audio_sha256'] = recognized['audio_sha256']
    timing['anchor_char_range']=[int(good[0]),int(good[-1])]
    if sentences[0]['t'] > 1.5:
        timing['prelude'] = {'t': 0, 'e': sentences[0]['t'], 'text': timing['doc']}
    return timing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target', type=pathlib.Path)
    ap.add_argument('--cache', type=pathlib.Path, required=True)
    ap.add_argument('--recognize-only', action='store_true')
    ap.add_argument('--align-only', action='store_true')
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    files = sorted(args.target.glob('F0[12]/*.timing.json')) if args.target.is_dir() else [args.target]
    pipeline = None
    if not args.align_only:
        from faster_whisper import WhisperModel, BatchedInferencePipeline
        pipeline = BatchedInferencePipeline(WhisperModel('small', device='cpu', compute_type='int8', cpu_threads=3))
    for path in files:
        audio = path.with_name(path.name.replace('.timing.json', '.ogg'))
        words = json.loads((args.cache / (audio.stem + '.words.json')).read_text()) if args.align_only else recognize(audio, args.cache, pipeline)
        if not args.recognize_only:
            data = align(json.loads(path.read_text()), words)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
            print(path.name, data['stats'], flush=True)


if __name__ == '__main__':
    main()
