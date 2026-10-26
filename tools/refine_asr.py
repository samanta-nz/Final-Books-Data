#!/usr/bin/env python3
"""Re-recognize suspicious gaps in coarse transcript/ASR anchors."""
import pathlib,json,sys,subprocess,time,argparse
import numpy as np

from align_asr import align,acoustic_norm
from mobile_audio import decode as decode_audio
from faster_whisper import WhisperModel
ap=argparse.ArgumentParser()
ap.add_argument('target',type=pathlib.Path)
ap.add_argument('--cache',type=pathlib.Path,required=True)
args=ap.parse_args()
ROOT=args.target;CACHE=args.cache
model=WhisperModel('small',device='cpu',compute_type='int8',cpu_threads=3)
for path in sorted(ROOT.glob('F0[12]/*.timing.json')):
    cache=CACHE/path.name.replace('.timing.json','.words.json');data=json.loads(cache.read_text())
    if data.get('gap_repaired'):continue
    provisional=align(json.loads(path.read_text()),data)
    words=[w for s in provisional['sentences'] for w in s['words']]
    centers=set()
    for a,b in zip(words,words[1:]):
        if b['t']-a['t']>3:
            for c in np.arange(a['t']+10,b['t']+10,20):centers.add(round(float(c)/10)*10)
    print(path.stem,'repairing',len(centers),'acoustic gaps',flush=True)
    audio=path.with_name(path.name.replace('.timing.json','.ogg'))
    pcm=decode_audio(audio)
    old=[w for c in data['chunks'] for w in c['words']]
    for center in sorted(centers):
        left=max(0,center-12.5);right=min(data['duration'],center+12.5)
        segments,_=model.transcribe(pcm[int(left*16000):int(right*16000)].copy(),language='fa',beam_size=3,
                                   word_timestamps=True,temperature=0,condition_on_previous_text=False,vad_filter=False)
        new=[];began=time.monotonic()
        for segment in segments:
            for w in segment.words or []:
                t=left+w.start;e=left+w.end
                if max(0,center-10)<=(t+e)/2<min(data['duration'],center+10):new.append({'text':w.word.strip(),'t':round(t,3),'end':round(e,3),'p':round(w.probability,3)})
        if new:
            old=[w for w in old if not max(0,center-10)<=(w['t']+w['end'])/2<min(data['duration'],center+10)]+new
        print(audio.stem,center,len(new),round(time.monotonic()-began,2),flush=True)
    old.sort(key=lambda w:(w['t'],w['end']))
    data['chunks']=[{'offset':0,'end':data['duration'],'words':old}];data['gap_repaired']=True
    cache.write_text(json.dumps(data,ensure_ascii=False,indent=1))
