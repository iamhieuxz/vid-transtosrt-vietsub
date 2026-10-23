# -*- coding: utf-8 -*-
"""Dedupe glossary_per-film -> gợi ý đẩy lên common.

Scan tất cả `glossary_*.yaml` + `glossary_common.yaml` trong thư mục hiện tại,
tìm source_term xuất hiện ở ≥ MIN_FILMS phim, in suggestion để user copy vào common.

Usage:
    python dedupe_glossary.py            # default: chỉ in báo cáo
    python dedupe_glossary.py --promote  # in format sẵn để paste vào common (KHÔNG sửa file)
"""
import sys, glob, os, re
sys.stdout.reconfigure(encoding='utf-8')

import yaml

os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..' if __name__ == '__main__' else '.')

PROMOTE = '--promote' in sys.argv
MIN_FILMS = 2  # tối thiểu xuất hiện ở N phim


def load(p):
    """Load yaml, skip None, return list of normalized dicts."""
    try:
        with open(p, encoding='utf-8') as f:
            raw = yaml.safe_load(f) or []
    except Exception as e:
        print(f"[WARN] Failed to load {p}: {e}")
        return []
    out = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        src = (e.get('source') or e.get('source_term') or '').strip()
        tgt = (e.get('target') or e.get('target_term') or '').strip()
        if src and tgt:
            out.append({'source_term': src, 'target_term': tgt,
                        'context_hint': (e.get('context') or e.get('context_hint') or '').strip()})
    return out


# index[source_term] = [(film, target, hint), ...]
idx = {}
film_files = sorted(glob.glob('glossary_*.yaml'))
common_files = [p for p in film_files if os.path.basename(p) == 'glossary_common.yaml']
film_files = [p for p in film_files if p not in common_files]

print(f'Found {len(film_files)} per-film glossary file(s):')
for f in film_files:
    print(f'  - {f}')
print(f'+ {len(common_files)} common file(s)')
print()

for path in film_files:
    film = os.path.basename(path).replace('glossary_', '').replace('.yaml', '')
    for entry in load(path):
        key = entry['source_term']
        idx.setdefault(key, []).append((film, entry['target_term'], entry['context_hint']))

# Term xuất hiện ở ≥ MIN_FILMS films
candidates = [(src, rows) for src, rows in idx.items() if len(rows) >= MIN_FILMS]
candidates.sort(key=lambda kv: (-len(kv[1]), kv[0]))

print(f'CANDIDATES cho common (xuất hiện ở ≥{MIN_FILMS} phim): {len(candidates)} term(s)')
print()

if not candidates:
    print('(khong co term nao trung)')
    sys.exit(0)

if not PROMOTE:
    # Báo cáo
    for src, rows in candidates:
        films = [r[0] for r in rows]
        targets = set(r[1] for r in rows)
        if len(targets) == 1:
            print(f'  {src!r:25} -> {list(targets)[0]!r:30} ({len(films)} films: {", ".join(films)})')
        else:
            print(f'  {src!r:25} -> CONFLICT ({len(targets)} versions):')
            for r in rows:
                print(f'      - {r[0]:15} -> {r[1]!r}')
    print()
    print(f'Chay lai voi --promote de lay format paste vao glossary_common.yaml')
else:
    # Format ready-to-paste
    print('# Paste vao glossary_common.yaml:')
    print()
    for src, rows in candidates:
        targets = set(r[1] for r in rows)
        if len(targets) == 1:
            tgt = list(targets)[0]
            films = ', '.join(sorted(set(r[0] for r in rows)))
            print(f'- source_term: {src}')
            print(f'  target_term: {tgt}')
            print(f'  context_hint: xuat hien o {len(rows)} phim ({films})')
        else:
            # Conflict: nếu khác target thì KHÔNG promote, giữ per-film
            print(f'# CONFLICT {src!r} khong promote (cac phim khac target):')
            for r in rows:
                print(f'#   - {r[0]:15} -> {r[1]!r}')