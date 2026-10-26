#!/usr/bin/env python3
"""Reusable Persian lesson workflow. Run --help; no hardcoded grade/chapter."""
import argparse,json,pathlib,subprocess,sys,zipfile
HERE=pathlib.Path(__file__).resolve().parent
REPO=HERE.parent

def run(name,*args):
    subprocess.run([sys.executable,str(HERE/name),*map(str,args)],check=True,cwd=REPO)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('target',type=pathlib.Path,help='Chapter, book directory or lesson .ogg')
    ap.add_argument('--stage',choices=['init','align','build','verify','package'],required=True)
    ap.add_argument('--cache',type=pathlib.Path,default=REPO/'.cache/mobile-alignment')
    ap.add_argument('--output',type=pathlib.Path,default=REPO/'output/mobile-lessons.zip')
    ap.add_argument('--bg',type=pathlib.Path)
    ap.add_argument('--browser',help='Optional Chromium executable')
    args=ap.parse_args();target=args.target.resolve()
    oggs=sorted(target.rglob('*.ogg')) if target.is_dir() else [target]
    # Never pick up generated bundles or recognition caches.
    oggs=[p for p in oggs if not any(x in p.parts for x in ['sources','output','.cache'])]
    if not oggs:ap.error('No source OGG lessons found')
    base=target if target.is_dir() else target.parent
    if args.stage=='init':
        for ogg in oggs:
            timing=ogg.with_suffix('.timing.json')
            if timing.exists():print('Keep existing timing:',timing);continue
            if not ogg.with_suffix('.txt').exists():raise ValueError(f'Missing transcript: {ogg.with_suffix(".txt")}')
            run('align_ogg.py',ogg,ogg.with_suffix('.txt'),'-o',timing)
    elif args.stage=='align':
        for ogg in oggs:
            timing=ogg.with_suffix('.timing.json')
            if not timing.exists():raise ValueError(f'Run --stage init first: {ogg}')
            cache=args.cache.resolve()/ogg.relative_to(base).with_suffix('')
            cache.mkdir(parents=True,exist_ok=True)
            run('align_asr.py',timing,'--cache',cache,'--recognize-only')
            run('refine_asr.py',timing,'--cache',cache)
            run('force_align.py',timing,'--cache',cache)
    elif args.stage=='build':
        for ogg in oggs:
            if not ogg.with_suffix('.board.json').exists():raise ValueError(f'Author the timed board first: {ogg.with_suffix(".board.json")} (see tools/README.md)')
        for build_target in ([target.with_suffix('')] if target.is_file() else sorted({p.parent for p in oggs})):
            run('build_mobile.py',build_target,*(['--bg',args.bg] if args.bg else []))
    elif args.stage=='verify':
        run('verify_mobile.py',target,*(['--browser',args.browser] if args.browser else []))
    elif args.stage=='package':
        args.output.parent.mkdir(parents=True,exist_ok=True)
        paths={}
        for chapter in sorted({p.parent for p in oggs}):
            for p in (chapter/'mobile').glob('*.html'):paths[p]=p.relative_to(base)
            for pattern in ['*.timing.json','*.board.json','*.caption-edits.json','*.audio-edits.json']:
                for p in chapter.glob(pattern):paths[p]=pathlib.Path('sources')/p.relative_to(base)
        if (base/'index.html').exists():paths[base/'index.html']=pathlib.Path('index.html')
        if not paths:raise ValueError('Build lessons first')
        for p in HERE.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:paths[p]=pathlib.Path('sources/tools')/p.relative_to(HERE)
        for p in ((REPO/'fonts') if (REPO/'fonts').exists() else REPO/'G09/G09-Math/F01/mobile/fonts').glob('*'):
            if p.is_file():paths[p]=pathlib.Path('sources/fonts')/p.name
        bg=args.bg or REPO/'Cheerful Persian Study Classroom Interface.png'
        if bg.exists():paths[bg]=pathlib.Path('sources')/bg.name
        rules=REPO/'G09/G09-Math/AGENTS.md'
        if rules.exists():paths[rules]=pathlib.Path('sources/G09/G09-Math/AGENTS.md')
        if (REPO/'AGENTS.md').exists():paths[REPO/'AGENTS.md']=pathlib.Path('sources/AGENTS.md')
        report=REPO/'output/mobile-verification.json'
        if report.exists():paths[report]=pathlib.Path('validation/mobile-verification.json')
        with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED) as z:
            for p,name in sorted(paths.items()):z.write(p,str(name))
            z.writestr('README.txt','فایل index.html یا mobile/index.html فصل را باز کنید. هر درس مستقل و آفلاین است. راهنمای بازتولید در sources/tools/README.md است.\n')
        with zipfile.ZipFile(args.output) as z:assert z.testzip() is None
        print(args.output.resolve())

if __name__=='__main__':main()
