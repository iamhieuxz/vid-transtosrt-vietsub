# -*- coding: utf-8 -*-
"""Đo độ phủ của GlossaryMiner trên toàn bộ phim đã dịch.

Câu hỏi cần trả lời: miner có thực sự BẮT ĐƯỢC từ uncen đáng học, hay chỉ
ra rác? Cách kiểm chứng: chạy không lọc (skip_existing=False), rồi đo
candidate nào TRÙNG với glossary_uncen. Trùng = miner tìm đúng từ có thật,
và nếu target khớp luôn thì đó là bằng chứng miner học đúng.

Usage:  python tests/mine_coverage.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from core.database import Database
from core.glossary_miner import GlossaryMiner

# Load glossary_uncen để đối chiếu target
with open('glossary_uncen.yaml', encoding='utf-8') as f:
    UNCEN = {
        (e.get('source_term') or '').strip(): (e.get('target_term') or '').strip()
        for e in (yaml.safe_load(f) or [])
        if isinstance(e, dict)
    }

db = Database()
projects = db.get_all_projects() if hasattr(db, 'get_all_projects') else None
if projects is None:
    import sqlite3
    conn = sqlite3.connect('translation.db')
    conn.row_factory = sqlite3.Row
    projects = [dict(r) for r in conn.execute(
        "SELECT id, name FROM projects WHERE status IN "
        "('completed','parsed','completed_with_errors') ORDER BY id")]
    conn.close()

# Chạy không lọc để thấy hết, kể cả cái trùng uncen
miner = GlossaryMiner(db, {'pipeline': {
    'learn_glossary_min_freq': 3,
    'learn_glossary_min_ratio': 0.5,
    'learn_glossary_max_candidates': 9999,
}})

print('=== Độ phủ miner trên %d phim ===' % len(projects))
print()
print('%-12s %6s %6s %6s %8s' % ('phim', 'lines', 'cand', 'trùng', 'đúng target'))
print('-' * 46)

all_hits = []
for p in projects:
    pid, name = p['id'], p['name']
    cands = miner.mine(pid, skip_existing=False)
    if not cands:
        print('%-12s %6s %6d' % (name[:12], '-', 0))
        continue
    lines = len(db.get_all_items(pid))
    hit = 0
    exact = 0
    for c in cands:
        src = c['source_term']
        if src in UNCEN:
            hit += 1
            all_hits.append((name, src, c['target_term'], UNCEN[src]))
            if UNCEN[src] == c['target_term']:
                exact += 1
    print('%-12s %6d %6d %6d %8d' % (name[:12], lines, len(cands), hit, exact))

print()
print('=== Chi tiết các candidate TRÙNG glossary_uncen ===')
if not all_hits:
    print('(khong co)')
for name, src, mine_tgt, real_tgt in sorted(all_hits, key=lambda x: x[1]):
    ok = 'OK ' if mine_tgt == real_tgt else 'SAI'
    print('  [%s] %-12s %-16s -> mine=%-18s uncen=%s' % (
        ok, name[:12], src, repr(mine_tgt), repr(real_tgt)))

print()
print('Tong so candidate trung uncen: %d' % len(all_hits))