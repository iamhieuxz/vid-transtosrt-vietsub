# -*- coding: utf-8 -*-
"""Regression tests cho đợt cải tiến 2026-11-01 (review code + roadmap).

Chạy:  python -m pytest tests/test_improvements_2026_11.py -q
Hoặc:  python tests/test_improvements_2026_11.py   (có bootstrap sys.path)

Bao phủ:
1. Strip leading numbered-list KHÔNG được cắt số giờ/khoảng/decimals tiếng Việt.
2. Gender-lock chỉ đụng từ giải phẫu NỮ rõ ràng.
3. Phonetic name clustering không gộp 2 nhân vật có tần suất tương đương.
4. Film-fixes YAML: load đúng + chỉ áp cho phim tương ứng.
5. DB get_items_by_pos_range trả đúng cửa sổ vị trí.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.ja_translator import _strip_leading_number
from core.ja_translator2 import _strip_leading_number as _strip_leading_number_v2
from core.post_processor import RuleBasedPostProcessor, _LEADING_NUM_LEAK_RE
from core.ja_qa_checker import extract_character_names_from_lines
from core.film_fixes import load_film_fixes
from core.whisper_normalizer import normalize_items
from core.database import Database


# ---------------------------------------------------------------------------
# 1. Leading-number stripping an toàn
# ---------------------------------------------------------------------------
SAFE_STRINGS = [
    '20-30 tuổi',        # khoảng số
    '3:00 sáng mai',     # giờ
    '1-2 tiếng nữa',     # khoảng số
    '2.5 giờ',           # số thập phân
    '1.000 người',       # phân cách nghìn
    '12,5 phần trăm',    # thập phân dấu phẩy
]


@pytest.mark.parametrize('text', SAFE_STRINGS)
def test_strip_leading_number_keeps_legit_numbers(text):
    assert _strip_leading_number(text) == text
    assert _strip_leading_number_v2(text) == text
    assert _LEADING_NUM_LEAK_RE.sub('', text).strip() == text


@pytest.mark.parametrize('text,expected', [
    ('1. Xin chào', 'Xin chào'),
    ('2) Em khỏe không?', 'Em khỏe không?'),
    ('1、こんにちは', 'こんにちは'),
    ('1. 2) Xin chào', 'Xin chào'),
])
def test_strip_leading_number_still_strips_numbered_list(text, expected):
    assert _strip_leading_number(text) == expected
    assert _LEADING_NUM_LEAK_RE.sub('', text).strip() == expected


def test_post_processor_keeps_time_and_range():
    pp = RuleBasedPostProcessor()
    assert pp.process_line('3:00 sáng mai anh về') == '3:00 sáng mai anh về'
    assert pp.process_line('20-30 tuổi') == '20-30 tuổi'
    # numbered-list thật vẫn bị gỡ
    assert pp.process_line('1. Anh về rồi à') == 'Anh về rồi à'


# ---------------------------------------------------------------------------
# 2. Gender-lock thu hẹp
# ---------------------------------------------------------------------------
def test_gender_lock_fixes_female_anatomy_wrong_pronoun():
    pp = RuleBasedPostProcessor()
    out = pp.process_line('Anh nhìn âm đạo của anh đi', source_text='まんこ見て')
    assert 'âm đạo của cô' in out


def test_gender_lock_keeps_male_chest_and_butterfly():
    pp = RuleBasedPostProcessor()
    assert pp.process_line('Anh khoe ngực anh đi') == 'Anh khoe ngực anh đi'
    assert pp.process_line('Con bướm của anh đẹp quá') == 'Con bướm của anh đẹp quá'


# ---------------------------------------------------------------------------
# 3. Phonetic name clustering: không gộp 2 nhân vật tần suất tương đương
# ---------------------------------------------------------------------------
def test_name_clustering_does_not_merge_two_balanced_characters():
    lines = []
    lines += ['ユキさん、おはよう。'] * 3      # Yuki
    lines += ['ユキオさん、おはよう。'] * 3    # Yukio (tên khác, gần âm)
    names = extract_character_names_from_lines(lines)
    canonicals = {n['canonical'] for n in names}
    assert 'Yuki' in canonicals and 'Yukio' in canonicals, canonicals


def test_name_clustering_absorbs_rare_mishearing():
    lines = ['ツヨシさん、こんにちは。'] * 5   # Tsuyoshi (chính)
    lines += ['つよいさん、こんにちは。'] * 1  # nghe lầm hiếm
    names = extract_character_names_from_lines(lines)
    canonicals = {n['canonical'] for n in names}
    assert 'Tsuyoshi' in canonicals
    tsuyoshi = next(n for n in names if n['canonical'] == 'Tsuyoshi')
    assert any('Tsuyoi' in m for m in tsuyoshi.get('mistaken_aliases', [])), tsuyoshi


# ---------------------------------------------------------------------------
# 4. Film-fixes YAML
# ---------------------------------------------------------------------------
def test_load_film_fixes_adn780():
    fixes = load_film_fixes('ADN-780')
    assert len(fixes['homophone']) >= 20
    assert len(fixes['mora_clip']) >= 5
    assert len(fixes['source_conditioned']) >= 10


def test_load_film_fixes_unknown_project_is_empty():
    fixes = load_film_fixes('KHONG-CO-PHIM-NAY')
    assert fixes == {'homophone': [], 'mora_clip': [], 'source_conditioned': []}


def test_homophone_only_applied_when_film_fixes_passed():
    items_plain = [{'index': 1, 'start': '00:00:01,000', 'end': '00:00:02,000', 'text': '仲立ちするの'}]
    items_fixed = [{'index': 1, 'start': '00:00:01,000', 'end': '00:00:02,000', 'text': '仲立ちするの'}]
    normalize_items(items_plain)  # không truyền film_fixes -> giữ nguyên
    normalize_items(items_fixed, film_fixes=load_film_fixes('ADN-780'))
    assert items_plain[0]['text'] == '仲立ちするの'
    assert items_fixed[0]['text'].startswith('中出し')


def test_source_conditioned_only_applied_for_matching_film():
    text = 'Cô ấy mang thai rồi'
    src = '娘を身ごもった'
    pp_plain = RuleBasedPostProcessor()
    assert pp_plain.process_line(text, source_text=src) == text

    pp_film = RuleBasedPostProcessor()
    pp_film.set_film_fixes(load_film_fixes('ADN-780'))
    assert 'tôi mang thai con gái' in pp_film.process_line(text, source_text=src).lower()


# ---------------------------------------------------------------------------
# 5. DB positional window
# ---------------------------------------------------------------------------
def test_db_get_items_by_pos_range(tmp_path):
    db = Database(str(tmp_path / 't.db'))
    pid = db.create_project('T', 'Japanese', 'Vietnamese', 'in.srt', 'out')
    db.save_subtitle_items(pid, [
        {'index': i, 'start': f'00:00:{i:02d},000', 'end': f'00:00:{i + 1:02d},000', 'text': f'line {i}'}
        for i in range(1, 11)
    ])
    window = db.get_items_by_pos_range(pid, 2, 5)
    assert [w['original_text'] for w in window] == ['line 3', 'line 4', 'line 5', 'line 6']
    assert db.get_items_by_pos_range(pid, 5, 2) == []


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))
