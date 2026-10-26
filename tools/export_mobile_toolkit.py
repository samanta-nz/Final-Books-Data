#!/usr/bin/env python3
"""Export the reusable tooling, fonts, background, rules and one authored example."""
import argparse,pathlib,zipfile
ROOT=pathlib.Path(__file__).resolve().parent.parent
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=pathlib.Path,default=ROOT/'output/Persian-Mobile-Lesson-Toolkit.zip');args=ap.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted((ROOT/'tools').rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:z.write(p,p.relative_to(ROOT))
        for p in (ROOT/'G09/G09-Math/F01/mobile/fonts').glob('*'):
            if p.is_file():z.write(p,pathlib.Path('fonts')/p.name)
        for name in ['AGENTS.md','G09/G09-Math/AGENTS.md','Cheerful Persian Study Classroom Interface.png']:
            p=ROOT/name
            if p.exists():z.write(p,name)
        for suffix in ['txt','board.json','timing.json']:
            p=ROOT/'G09/G09-Math/F01'/('0101.'+suffix)
            if p.exists():z.write(p,pathlib.Path('examples')/p.name)
        z.writestr('README.txt','راهنمای کامل ابزارها در tools/README.md قرار دارد. نمونهٔ examples فقط برای ساختار داده است؛ صدای درس بعدی را در کنار متن خودش قرار دهید. فرمان‌ها را از ریشهٔ همین بسته اجرا کنید.\n')
    with zipfile.ZipFile(args.output) as z:assert z.testzip() is None
    print(args.output.resolve())
