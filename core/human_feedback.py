# -*- coding: utf-8 -*-
"""B1 — Học từ bản dịch người dùng đã sửa tay (Aegisub / SRT).

Import file .srt đã sửa -> diff với bản dịch máy trong DB:
  - ghi translation_errors (error_type='human')
  - cập nhật translated_text + Translation Memory (confidence 1.0)
  - mine glossary từ các cặp đã sửa -> glossary_pending.yaml (chờ duyệt)

Usage:
    python core/human_feedback.py <project_name> <corrected.srt>
"""
from __future__ import annotations

import os
import sys
from typing import List

PENDING_PATH = 'glossary_pending.yaml'


def import_corrected_srt(db, project_name: str, srt_path: str) -> List[dict]:
    """Trả về list {sub_index, source_text, wrong_text, fixed_text} các dòng đã được sửa."""
    import pysrt
    project = db.get_project_by_name(project_name)
    if not project:
        raise ValueError(f"Khong tim thay project '{project_name}'")
    if not os.path.exists(srt_path):
        raise FileNotFoundError(srt_path)

    # Exporter đánh lại index sau khi sort theo thời gian -> khớp bằng (start, end), không bằng index
    norm = lambda t: str(pysrt.SubRipTime.from_string(t))
    by_time = {}
    for it in db.get_all_items(project['id']):
        by_time.setdefault((norm(it['start_time']), norm(it['end_time'])), []).append(it)

    fixes = []
    for s in pysrt.open(srt_path, encoding='utf-8-sig'):
        bucket = by_time.get((str(s.start), str(s.end)))
        if not bucket:
            continue
        it = bucket.pop(0)
        fixed = s.text.strip()
        wrong = (it.get('translated_text') or '').strip()
        if not fixed or fixed == wrong or fixed.startswith('[UNTRANSLATED]'):
            continue
        fixes.append({'id': it['id'], 'sub_index': it['sub_index'],
                      'source_text': it['original_text'], 'wrong_text': wrong, 'fixed_text': fixed})

    for f in fixes:
        db.log_translation_error(project['id'], f['sub_index'], f['source_text'],
                                 f['wrong_text'], f['fixed_text'], error_type='human')
        db.update_item_translation(f['id'], f['fixed_text'])
        db.save_translation_memory(project['source_lang'], project['target_lang'], f['source_text'],
                                   f['fixed_text'], domain=project['name'], confidence=1.0)
    return fixes


def mine_human_terms(db, config: dict, fixes: List[dict]) -> List[dict]:
    """Mine glossary từ các cặp người sửa; chỉ giữ term mà bản sửa thêm vào (bản máy không có)."""
    from core.glossary_miner import GlossaryMiner
    miner = GlossaryMiner(db, config)
    miner.min_freq = 2  # dữ liệu người sửa ít nhưng tin cậy -> hạ ngưỡng tần suất
    pairs = [(f['source_text'], f['fixed_text'], 0) for f in fixes]
    cands = miner._mine_from_pairs(pairs, skip_existing=True)
    wrong_by_src = [(f['source_text'], f['wrong_text'].lower()) for f in fixes]
    return [
        c for c in cands
        if any(c['source_term'] in src and c['target_term'].lower() not in wrong
               for src, wrong in wrong_by_src)
    ]


def write_pending(cands: List[dict], path: str = PENDING_PATH) -> int:
    """Merge candidate vào glossary_pending.yaml (không ghi đè term đã chờ duyệt). Trả về số term mới."""
    import yaml
    existing = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            existing = yaml.safe_load(f) or []
    known = {e.get('source_term') for e in existing if isinstance(e, dict)}
    new = [{'source_term': c['source_term'], 'target_term': c['target_term'],
            'context_hint': f"human-fix (freq={c['freq']}, confidence={c['confidence']})"}
           for c in cands if c['source_term'] not in known]
    if new:
        with open(path, 'w', encoding='utf-8') as f:
            f.write('# glossary_pending.yaml — term học từ bản sửa tay, CHỜ DUYỆT.\n')
            f.write('# Duyệt xong thì chuyển entry sang glossary_uncen.yaml / glossary_<film>.yaml.\n')
            yaml.safe_dump(existing + new, f, allow_unicode=True, sort_keys=False)
    return len(new)


def learn_from_human_srt(
    db,
    config: dict,
    project_name: str,
    srt_path: str,
    pending_path: str = PENDING_PATH,
) -> dict:
    """Hàm tổng hợp gọi từ GUI hoặc CLI: import SRT đã sửa tay vào TM (confidence=1.0)
    và trích xuất thuật ngữ mới vào glossary_pending.yaml.
    """
    fixes = import_corrected_srt(db, project_name, srt_path)
    cands = mine_human_terms(db, config or {}, fixes) if fixes else []
    added = write_pending(cands, path=pending_path) if cands else 0
    return {
        "fixed_lines": len(fixes),
        "new_terms": added,
        "pending_path": pending_path,
        "candidates": cands,
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print(__doc__)
        return 1
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    import yaml
    from core.database import Database
    with open(os.path.join(root, 'config.yaml'), encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}

    db = Database()
    fixes = import_corrected_srt(db, argv[0], argv[1])
    print(f"[FEEDBACK] {len(fixes)} dong da sua -> translation_errors(human) + TM")
    added = write_pending(mine_human_terms(db, cfg, fixes))
    print(f"[FEEDBACK] {added} term moi -> {PENDING_PATH}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
