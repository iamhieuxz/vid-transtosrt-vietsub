# -*- coding: utf-8 -*-
import os

from core.database import Database
from core.human_feedback import import_corrected_srt, write_pending

SRT = """1
00:00:01,000 --> 00:00:02,000
Chào anh

2
00:00:03,000 --> 00:00:04,000
Bản sửa tay

"""


def test_import_corrected_srt_logs_human_fix(tmp_path):
    db = Database(str(tmp_path / 't.db'))
    pid = db.create_project('P1', 'ja', 'vi', 'in.srt', 'out.srt')
    db.save_subtitle_items(pid, [
        {'index': 1, 'start': '00:00:01,000', 'end': '00:00:02,000', 'text': 'こんにちは'},
        {'index': 2, 'start': '00:00:03,000', 'end': '00:00:04,000', 'text': '旦那さん'},
    ])
    items = db.get_all_items(pid)
    db.update_item_translation(items[0]['id'], 'Chào anh')
    db.update_item_translation(items[1]['id'], 'Bản máy')
    srt = tmp_path / 'fixed.srt'
    srt.write_text(SRT, encoding='utf-8')

    fixes = import_corrected_srt(db, 'P1', str(srt))

    assert [f['fixed_text'] for f in fixes] == ['Bản sửa tay']
    errs = db.get_translation_errors(pid)
    assert errs[0]['error_type'] == 'human' and errs[0]['wrong_text'] == 'Bản máy'
    assert db.get_all_items(pid)[1]['translated_text'] == 'Bản sửa tay'
    assert db.get_translation_memory('ja', 'vi', '旦那さん', domain='P1') == 'Bản sửa tay'


def test_write_pending_merges_without_duplicates(tmp_path):
    path = str(tmp_path / 'pending.yaml')
    c = {'source_term': '中出し', 'target_term': 'xuất trong', 'freq': 2, 'confidence': 1.0}
    assert write_pending([c], path) == 1
    assert write_pending([c], path) == 0
    assert os.path.exists(path)
