# -*- coding: utf-8 -*-
"""Validate tat ca glossary_*.yaml: YAML hop le, khong term rac/empty, khong trung."""
import glob
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import yaml

fail = 0
for f in sorted(glob.glob('glossary*.yaml')):
    raw = io.open(f, encoding='utf-8').read()
    try:
        data = yaml.safe_load(raw)
    except Exception as e:
        print('[FAIL] %s: YAML loi: %s' % (f, str(e)[:120]))
        fail += 1
        continue
    if not isinstance(data, list):
        print('[FAIL] %s: khong phai list' % f)
        fail += 1
        continue
    bad = [e for e in data
           if not isinstance(e, dict)
           or not str(e.get('source_term', '')).strip()
           or not str(e.get('target_term', '')).strip()]
    if bad:
        print('[FAIL] %s: %d entry rac' % (f, len(bad)))
        for b in bad[:5]:
            print('       %r' % b)
        fail += 1
    srcs = [e['source_term'] for e in data]
    dups = sorted({x for x in srcs if srcs.count(x) > 1})
    if dups:
        print('[FAIL] %s: trung source_term %s' % (f, dups))
        fail += 1
    # ky tu la trong source_term (Whisper artifact nham ron)
    weird = [s for s in srcs if re.search(r'[A-Za-z]{3,}[_ ]|VAL_LOG|Veterans|engulf', s)]
    if weird:
        print('[WARN] %s: source_term suspect chua kiem chung: %s' % (f, weird))
    print('[ OK ] %s: %d term' % (f, len(data)))

print('\n%d file loi' % fail)
sys.exit(1 if fail else 0)