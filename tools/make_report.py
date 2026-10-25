#!/usr/bin/env python3
"""Build a Persian Markdown report from the *.timing.json files of a folder.

    python tools/make_report.py G09/G09-Math/F01
"""
import glob
import json
import os
import re
import sys

FA = '۰۱۲۳۴۵۶۷۸۹'


def fa(x):
    return str(x).translate(str.maketrans('0123456789', FA))


def mmss(t, digits=False):
    t = max(0.0, float(t))
    m, s = divmod(t, 60)
    if digits:
        return f'{fa(int(m))}:{fa(f"{s:05.2f}")}'
    return f'{fa(int(m))}:{fa(f"{int(s):02d}")}'


def hms(t):
    t = int(round(float(t)))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f'{fa(h)}:{fa(f"{m:02d}")}:{fa(f"{s:02d}")}'


def load(folder):
    out = []
    for p in sorted(glob.glob(os.path.join(folder, '*.timing.json'))):
        try:
            out.append(json.load(open(p, encoding='utf-8')))
        except Exception as e:                                   # noqa: BLE001
            print('skip', p, e)
    return out


def md(folder):
    data = load(folder)
    if not data:
        return '# هیچ فایل زمان‌بندی‌ای پیدا نشد\n'
    name = os.path.basename(os.path.abspath(folder))
    tot = sum(d['stats']['duration'] for d in data)
    L = [f'# زمان‌بندی محتوای وایت‌بورد روی فایل‌های صوتی — {name}', '',
         f'محتوای {fa(len(data))} درس با زمان‌بندی خودکار (بدون تشخیص گفتار) روی فایل‌های `ogg` تطبیق داده شد؛ '
         f'مجموعاً {hms(tot)} صدا.', '',
         'روش: پاکت انرژی ۱۰میلی‌ثانیه‌ای → تشخیص مکث → تخصیص نرخ گفتار به متن (`txt`) → '
         'قیدزدن (snap) مرز جمله‌ها روی مکث‌های واقعی با برنامه‌ریزی پویا → نگاشت بخش‌های متن و اسلایدهای '
         'وایت‌بورد روی همان خط زمانی.', '',
         '> دقت: این زمان‌بندی تخمینی است، نه همگام‌سازی واژه‌به‌واژه. جزئیات در بخش «دقت و محدودیت‌ها».', '',
         '---', '', '## فهرست درس‌ها', '',
         '| درس | فایل صوتی | مدت | جمله / بخش | اسلاید |', '|---|---|---|---|---|']
    for d in data:
        s = d['stats']
        L.append(f"| `{d['base']}` | `{d['audio']}` | {mmss(s['duration'])} | "
                 f"{fa(s['sentences'])} / {fa(len(d['sections']))} | "
                 f"{fa(len(d.get('slides', [])))} |")
    L += ['', '---', '']
    for d in data:
        title = re.sub(r'\s*\([^)]*\)\s*$', '', d['doc'] or '')
        L += [f"## {d['base']} — {title}".strip(), '',
              f"فایل صوتی: `{d['audio']}` · مدت: {mmss(d['stats']['duration'])} · "
              f"وایت‌بورد: `{d.get('board') or '—'}`", '']
        if d.get('slides'):
            L += ['| # | اسلاید | شروع | پایان | مدت | بخش متناظر |', '|---|---|---|---|---|---|']
            for s in d['slides']:
                L.append(f"| {fa(s['slide'])} | {s['title']} | {mmss(s['start'])} | {mmss(s['end'])} | "
                         f"{mmss(s['end'] - s['start'])} | {s.get('section', '')} |")
            L += ['']
        L += ['<details><summary>زمان‌بندی بخش‌های متن</summary>', '',
              '| شروع | پایان | عنوان بخش |', '|---|---|---|']
        for s in d['sections']:
            L.append(f"| {mmss(s['start'])} | {mmss(s['end'])} | {s['title']} |")
        L += ['', '</details>', '']
    L += ['---', '', '## دقت و محدودیت‌ها', '',
          '* مرزهای بخش‌ها روی مکث‌های واقعی صدا قید می‌شوند؛ جابه‌جایی نتایج با ۱۰ تنظیم مختلف '
          'پارامترها به‌طور میانگین حدود ۰٫۲ ثانیه و حداکثر ۱٫۲ ثانیه است.',
          '* خطای مطلق نسبت به گفتار واقعی معمولاً در حد چند ثانیه است؛ اگر جایی متن `txt` با صدای '
          'خوانده‌شده اختلاف داشته باشد (جملهٔ اضافه/جامانده) خطا می‌تواند تا حدود ۵–۱۰ ثانیه برود.',
          '* برای تطبیق واژه‌به‌واژه (زیرنویس دقیق یا کشیدن خط‌به‌خط وایت‌بورد) باید تشخیص گفتار '
          '(ASR) با مدل فارسی اجرا شود؛ این کار در این محیط بدون دسترسی به مدل انجام نشده است.',
          '* بهترین راه بازبینی: `sync-player.html` (صدا + وایت‌بورد + هایلایت جملهٔ در حال خواندن).', '']
    return '\n'.join(L)


if __name__ == '__main__':
    folder = sys.argv[1] if len(sys.argv) > 1 else '.'
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(folder, 'timing-report.md')
    open(out, 'w', encoding='utf-8').write(md(folder))
    print('wrote', out)
