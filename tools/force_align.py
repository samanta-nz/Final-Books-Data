#!/usr/bin/env python3
"""Refine coarse ASR anchors with Whisper attention alignment of the known text.

Overlapping acoustic windows align the *original* transcript, so spelling errors
in recognition do not become published captions. Cache files are resumable.
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import time

import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.tokenizer import Tokenizer
from faster_whisper.audio import pad_or_trim

from align_asr import align, norm
from mobile_audio import decode as decode_audio


def force(path, recognized, cache, model, tokenizer):
    original = json.loads(path.read_text())
    coarse = align(json.loads(path.read_text()), recognized)
    duration = recognized['duration']
    words = []
    for si, sentence in enumerate(coarse['sentences']):
        for wi, word in enumerate(sentence['words']):
            words.append({**word, 'sentence': si, 'word_index': wi})
    audio_path = path.with_name(path.name.replace('.timing.json', '.ogg'))
    audio = decode_audio(audio_path)
    checkpoint = cache / (audio_path.stem+'.forced-v4.json')
    saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    fingerprint=hashlib.sha256(json.dumps({'version':5,'audio':recognized['audio_sha256'],
        'text':[s['text'] for s in original['sentences']],
        'reference':[[s.get('reference_t'),s.get('reference_end')] for s in original['sentences']],
        'recognition':recognized['chunks']},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if saved.get('fingerprint')!=fingerprint:saved={}
    candidates = {int(i):v for i,v in saved.get('candidates',{}).items()}
    completed = set(saved.get('completed',[]))
    groups={};first=0
    while first<len(words):
        indices=[]
        for wi in range(first,min(len(words),first+70)):
            trial=indices+[wi]
            text=' '.join(words[i]['text'] for i in trial)
            if indices and (len(tokenizer.encode(text))>350 or words[wi]['end']-words[first]['t']>22):break
            indices=trial
        groups[first]=indices
        if indices[-1]==len(words)-1:break
        first=indices[-1]+1-min(12,len(indices)//3)
    centers=list(groups)
    completed.intersection_update(centers)
    pending = [c for c in centers if c not in completed]
    for offset in range(0,len(pending),4):
        batch = []
        for first in pending[offset:offset+4]:
            indices=groups[first]
            text=' '.join(words[i]['text'] for i in indices)
            tokens=tokenizer.encode(text)
            center=(words[indices[0]]['t']+words[indices[-1]]['end'])/2
            start=max(0,center-14);end=min(duration,center+14)
            pcm=audio[int(start*16000):int(end*16000)]
            batch.append({'center':first,'start':start,'end':end,'indices':indices,'text':text,
                          'tokens':tokens,'features':pad_or_trim(model.feature_extractor(pcm),3000), 'frames':len(pcm)//160})
        if not batch:continue
        began=time.monotonic()
        encoded=model.encode(np.stack([b['features'] for b in batch]))
        aligned=model.find_alignment(tokenizer,[b['tokens'] for b in batch],encoded,[b['frames'] for b in batch])
        for b, result in zip(batch,aligned):
            char_start=[];char_end=[];char_prob=[]
            normalized=''
            for item in result:
                token=norm(item['word'])
                normalized+=token
                char_start.extend([b['start']+float(item['start'])]*len(token))
                char_end.extend([b['start']+float(item['end'])]*len(token))
                char_prob.extend([float(item['probability'])]*len(token))
            expected=''.join(norm(words[i]['text']) for i in b['indices'])
            if normalized!=expected:
                raise ValueError('Forced-alignment tokenization changed the original text')
            pos=0
            for i in b['indices']:
                n=len(norm(words[i]['text']))
                if not n:continue
                t=char_start[pos];e=char_end[pos+n-1];p=float(np.mean(char_prob[pos:pos+n]));pos+=n
                if e-t>1.2:t=max(t,e-.7)
                # Prefer words with acoustic context on both sides, avoiding
                # tokens squeezed onto a crop boundary by DTW.
                edge=min(t-b['start'],b['end']-e)
                score=5*np.log(p+.01)+min(edge,5)*.2-abs(t-words[i]['t'])*.4
                record={'t':round(t,3),'end':round(max(t,e),3),'p':round(p,3),'score':round(score,3)}
                if i not in candidates or score>candidates[i]['score']:candidates[i]=record
            completed.add(b['center'])
        checkpoint.write_text(json.dumps({'fingerprint':fingerprint,'completed':sorted(completed),'candidates':candidates},ensure_ascii=False,indent=1))
        print(audio_path.stem,len(completed),'/',len(centers),'windows',round(time.monotonic()-began,2),'s',flush=True)
    missing=[i for i in range(len(words)) if i not in candidates]
    if missing:raise ValueError(f'{audio_path.stem}: {len(missing)} words without acoustic alignment')
    # Preserve the global order. DTW windows can disagree on a boundary word;
    # use isotonic starts rather than letting a later caption jump backwards.
    blocks=[]
    for i in range(len(words)):
        weight=max(.1,candidates[i]['p'])
        blocks.append([i,i+1,weight,weight*candidates[i]['t']])
        while len(blocks)>1 and blocks[-2][3]/blocks[-2][2]>blocks[-1][3]/blocks[-1][2]:
            right=blocks.pop();left=blocks.pop()
            blocks.append([left[0],right[1],left[2]+right[2],left[3]+right[3]])
    starts=np.zeros(len(words))
    for a,b,weight,total in blocks:starts[a:b]=total/weight
    for i,w in enumerate(words):
        w['t']=round(float(starts[i]),3)
        w['end']=round(min(duration,max(w['t'],candidates[i]['end'])),3)
        if i+1<len(words):w['end']=min(w['end'],round(float(starts[i+1]),3))
        w['alignment_probability']=candidates[i]['p']
    for si,sentence in enumerate(original['sentences']):
        own=[w for w in words if w['sentence']==si]
        sentence['t']=own[0]['t'];sentence['end']=own[-1]['end']
        sentence['words']=[{k:v for k,v in w.items() if k not in ['sentence','word_index']} for w in own]
        sentence['alignment']={'method':'Whisper attention forced alignment',
                               'mean_probability':round(float(np.mean([w['alignment_probability'] for w in own])),3)}
    for i,section in enumerate(original['sections']):
        owned=[s for s in original['sentences'] if s['sec']==i]
        section['start']=0 if i==0 else owned[0]['t']
    for i,section in enumerate(original['sections']):
        section['end']=original['sections'][i+1]['start'] if i+1<len(original['sections']) else round(duration,3)
    original.pop('slides',None)
    original.pop('alignment_review',None)
    original.pop('prelude',None)
    if original['sentences'][0]['t']>1.5:
        title_end=original['sentences'][0]['t']
        title=re.sub(r'\s*\([^)]*\)\s*$','',original['doc'])
        title_pcm=audio[:int(min(duration,title_end+.5)*16000)]
        title_alignment=model.find_alignment(tokenizer,[tokenizer.encode(title)],
            model.encode(pad_or_trim(model.feature_extractor(title_pcm),3000)),len(title_pcm)//160)[0]
        title_start=[];title_ends=[]
        for w in title_alignment:
            n=len(norm(w['word']));title_start.extend([float(w['start'])]*n);title_ends.extend([float(w['end'])]*n)
        title_words=[];pos=0
        for token in title.split():
            n=len(norm(token))
            if n:
                t=max(0,min(title_end,title_start[pos]));e=max(t,min(title_end,title_ends[pos+n-1]))
                title_words.append({'text':token,'t':round(t,3),'end':round(e,3)});pos+=n
        original['prelude']={'t':0,'e':title_end,'text':title,'words':title_words}
    original['method']='Persian ASR anchors + overlapping Whisper attention forced alignment of original words'
    original['stats']={'duration':round(duration,3),'sentences':len(original['sentences']),
                       'words':len(words),'acoustic_windows':len(completed),'slides':len(original['sections']),
                       'low_probability_words':sum(w['alignment_probability']<.1 for w in words)}
    original['audio_sha256']=recognized['audio_sha256']
    path.write_text(json.dumps(original,ensure_ascii=False,indent=1))
    print('FORCED',audio_path.stem,original['stats'],flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('target',type=pathlib.Path)
    ap.add_argument('--cache',type=pathlib.Path,required=True)
    args=ap.parse_args()
    model=WhisperModel('small',device='cpu',compute_type='int8',cpu_threads=3)
    tokenizer=Tokenizer(model.hf_tokenizer,model.model.is_multilingual,task='transcribe',language='fa')
    files=sorted(args.target.rglob('*.timing.json')) if args.target.is_dir() else [args.target]
    for path in files:
        recognized=json.loads((args.cache/path.name.replace('.timing.json','.words.json')).read_text())
        if not recognized.get('complete'):raise ValueError('Recognition cache incomplete')
        force(path,recognized,args.cache,model,tokenizer)
