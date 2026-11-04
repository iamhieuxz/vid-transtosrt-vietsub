"""Sửa dữ liệu cũ trong translation.db (ROE-459, JUR-412): cue kéo dài, từ cắt đôi, xưng hô.

Mặc định chỉ in diff (dry-run). --apply: backup DB rồi ghi. --export DIR: xuất <DIR>/<tên>.srt.
Chạy: python -m core.cue_repair --projects 26 27 [--apply] [--export DIR]
"""
import argparse
import os
import re
import sqlite3
import sys
import time

from .database import Database
from .exporter import Exporter
from .post_processor import RuleBasedPostProcessor
from .sub_merger import _format_time, _heal_split_tokens_across_cues, _parse_time
from .whisper_normalizer import _WHISPER_HOMOPHONE_FIXES

MAX_CUE_S = 12.0
_CHARS_PER_S = 5.5
# Cue dài kết bằng trợ từ = mảnh vỡ của câu kế tiếp
_PARTICLE_END = re.compile(r'[はがをにでとも]$')

# Bảng sửa xưng hô theo (tên project, sub_index): (chuỗi cũ, chuỗi mới)
_VI_FIXES = {
    ('ROE-459', 39): [('Với số tiền này mình lại', 'Với số tiền này dì lại')],
    ('ROE-459', 46): [('Mình ăn thôi.', 'Cháu xin phép ăn ạ.')],
    ('ROE-459', 104): [('người dì mà mình hằng', 'dì mà tôi hằng')],
}


def _speech_s(text: str) -> float:
    # shortcut: ước lượng theo số ký tự, không có audio để đối chiếu; sai số giây là chấp nhận được
    return len(re.sub(r'\s+', '', text)) / _CHARS_PER_S + 0.8


def repair_timestamps(rows: list) -> list:
    """rows: [{'start','end' (giây), 'text'}] theo thứ tự cue; sửa tại chỗ.

    1. Đuôi cue không vượt mốc bắt đầu cue kế.
    2. Cue > 12s kết bằng trợ từ: dời cửa sổ áp sát cue kế.
    3. Cue > 12s còn lại: giữ start, rút end theo độ dài lời nói.
    """
    prev_end = 0.0
    for i, r in enumerate(rows):
        nxt = rows[i + 1]['start'] if i + 1 < len(rows) else None
        if nxt is not None and r['start'] < nxt < r['end']:
            r['end'] = nxt
        if r['end'] - r['start'] > MAX_CUE_S:
            est = _speech_s(r['text'])
            if nxt is not None and _PARTICLE_END.search(r['text'].strip()):
                r['end'] = nxt
                r['start'] = max(prev_end, nxt - est)
            else:
                r['end'] = r['start'] + est
        prev_end = r['end']
    return rows


def _fix_homophones(text: str) -> str:
    for wrong, right in _WHISPER_HOMOPHONE_FIXES:
        text = text.replace(wrong, right)
    return text


def plan_project(db: Database, project_id: int) -> list:
    """Trả [(item, {cột: giá trị mới})] chỉ gồm cue có thay đổi."""
    name = db.get_project(project_id)['name']
    items = db.get_all_items(project_id)
    rows = [{'start': _parse_time(it['start_time']), 'end': _parse_time(it['end_time']),
             'text': it['original_text'] or ''} for it in items]
    repair_timestamps(rows)
    healed = _heal_split_tokens_across_cues([{'text': r['text']} for r in rows])

    pp = RuleBasedPostProcessor()
    from .film_fixes import load_film_fixes
    pp.set_film_fixes(load_film_fixes(name))
    plan = []
    for it, r, h in zip(items, rows, healed):
        new = {'original_text': _fix_homophones(h['text'])}
        for col, key in (('start_time', 'start'), ('end_time', 'end')):
            same = abs(r[key] - _parse_time(it[col])) < 0.0005  # tránh lệch 1ms do làm tròn
            new[col] = it[col] if same else _format_time(r[key])
        vi = it['translated_text']
        if vi:
            for old, rep in _VI_FIXES.get((name, it['sub_index']), []):
                vi = vi.replace(old, rep)
            vi = pp.process_line(vi, source_text=new['original_text'])
        new['translated_text'] = vi
        if any(new[c] != it[c] for c in new):
            plan.append((it, new))
    return plan


def backup_db(db_path: str) -> str:
    dst = f"{db_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    src, out = sqlite3.connect(db_path), sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    return dst


def apply_plan(db: Database, plan: list) -> None:
    with db._get_connection() as conn:
        for it, new in plan:
            conn.execute(
                'UPDATE subtitle_items SET start_time=?, end_time=?, original_text=?, translated_text=? WHERE id=?',
                (new['start_time'], new['end_time'], new['original_text'], new['translated_text'], it['id']))
        conn.commit()


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--db', default='translation.db')
    ap.add_argument('--projects', type=int, nargs='+', required=True)
    ap.add_argument('--apply', action='store_true', help='ghi vào DB (có backup)')
    ap.add_argument('--export', metavar='DIR', help='xuất <DIR>/<tên>.srt sau khi sửa')
    args = ap.parse_args(argv)

    db = Database(args.db)
    plans = {pid: plan_project(db, pid) for pid in args.projects}
    for pid, plan in plans.items():
        print(f"== project {pid} ({db.get_project(pid)['name']}): {len(plan)} cue thay đổi")
        for it, new in plan:
            for col in new:
                if new[col] != it[col]:
                    print(f"  #{it['sub_index']} {col}: {str(it[col])[:60]!r} -> {str(new[col])[:60]!r}")

    if args.apply:
        print('backup:', backup_db(args.db))
        for plan in plans.values():
            apply_plan(db, plan)
        print('đã ghi DB')
    if args.export:
        os.makedirs(args.export, exist_ok=True)
        for pid in args.projects:
            Exporter(db).export(pid, os.path.join(args.export, f"{db.get_project(pid)['name']}.srt"))
    return 0


if __name__ == '__main__':
    sys.exit(main())
