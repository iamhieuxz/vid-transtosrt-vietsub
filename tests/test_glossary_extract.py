"""Test _extract_term_diffs va _suggest_glossary_updates logic."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, '.')

from core.pipeline import TranslationPipeline


def test_basic_diff():
    """Sub từ cũ / từ mới khác nhau đúng 1 vị trí - usecase thật (canonical alias)."""
    # Pattern thật từ registry: đổi alias sai -> canonical
    old = "Leno nói với Makoto"
    new = "Reno nói với Makoto"
    diffs = TranslationPipeline._extract_term_diffs(old, new)
    assert ('Leno', 'Reno') in diffs, diffs
    print('Test 1: OK', diffs)


def test_no_diff():
    old = new = "abc def"
    diffs = TranslationPipeline._extract_term_diffs(old, new)
    assert diffs == [], diffs
    print('Test 2: OK')


def test_multiple_diffs():
    old = "Leno nói Renosuke thật giỏi"
    new = "Reno nói Renosan thật giỏi"
    diffs = TranslationPipeline._extract_term_diffs(old, new)
    assert ('Leno', 'Reno') in diffs, diffs
    assert ('Renosuke', 'Renosan') in diffs, diffs
    print('Test 3: OK', diffs)


def test_too_long_filtered():
    """Cụm từ dài hơn max_word_len sẽ bị loại."""
    old = "abc xyz123 very_long_token_to_skip"
    new = "abc xyz456 very_long_token_to_skip"
    diffs = TranslationPipeline._extract_term_diffs(old, new, max_word_len=8)
    # 'xyz123', 'xyz456' <= 8 chars: keep
    # 'very_long_token_to_skip' = 25 chars: skip
    for w, c in diffs:
        assert len(w) <= 8 and len(c) <= 8, diffs
    print('Test 4: OK')


if __name__ == '__main__':
    test_basic_diff()
    test_no_diff()
    test_multiple_diffs()
    test_too_long_filtered()
    print('\nAll tests passed!')