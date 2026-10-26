"""Shared, reproducible audio edits for recognition, alignment and AAC export."""
import hashlib
import json
import pathlib
import subprocess

import numpy as np


def edits_path(path):
    return pathlib.Path(path).with_suffix('.audio-edits.json')


def audio_fingerprint(path):
    digest=hashlib.sha256(pathlib.Path(path).read_bytes())
    edits=edits_path(path)
    if edits.exists():digest.update(edits.read_bytes())
    return digest.hexdigest()


def ffmpeg_input(path):
    command=['ffmpeg','-v','error','-y','-i',str(path)]
    edits=edits_path(path)
    cuts=json.loads(edits.read_text()).get('remove',[]) if edits.exists() else []
    if not cuts:return command
    kept=[];previous=0
    for cut in cuts:
        if cut['start']<previous or cut['end']<=cut['start']:raise ValueError('Invalid audio cut order')
        if cut['start']>previous:kept.append((previous,cut['start']))
        previous=cut['end']
    kept.append((previous,None))
    filters=[]
    for i,(start,end) in enumerate(kept):
        trim=f'atrim=start={start}'+(f':end={end}' if end is not None else '')
        filters.append(f'[0:a]{trim},asetpts=PTS-STARTPTS[a{i}]')
    filters.append(''.join(f'[a{i}]' for i in range(len(kept)))+f'concat=n={len(kept)}:v=0:a=1[edited]')
    return command+['-filter_complex',';'.join(filters),'-map','[edited]']


def decode(path,rate=16000):
    raw=subprocess.check_output(ffmpeg_input(path)+['-ar',str(rate),'-ac','1','-f','f32le','-'])
    return np.frombuffer(raw,np.float32)
