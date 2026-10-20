"""Tests cho _filter_glossary_for_window — matching glossary terms theo window."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipeline import TranslationPipeline


def make_pipeline():
    """Pipeline instance chỉ cần đủ field cho _filter_glossary_for_window."""
    p = TranslationPipeline.__new__(TranslationPipeline)
    p.enable_glossary = True
    return p


def test_cjk_term_matches_inside_japanese_text():
    """Term kanji phải match khi nằm giữa text Nhật (không có space)."""
    p = make_pipeline()
    glossary = [{'source_term': '一部', 'target_term': 'mot phan'}]
    # Truoc fix: '_term_present' reject vi char truoc la CJK -> khong match
    result = p._filter_glossary_for_window(glossary, ['その一部指示を'])
    assert len(result) == 1, f'CJK term should match, got {len(result)}'


def test_cjk_term_matches_multiple_occurrences():
    p = make_pipeline()
    glossary = [{'source_term': '主人', 'target_term': 'chong'}]
    result = p._filter_glossary_for_window(
        glossary, ['主人はいません', '主人を裏切って'])
    assert len(result) == 1


def test_latin_term_still_requires_word_boundary():
    """Latin term van phai ton trong boundary (khong regression 'masu' trong 'summarize')."""
    p = make_pipeline()
    glossary = [{'source_term': 'masu', 'target_term': 'x'}]
    assert p._filter_glossary_for_window(glossary, ['summarize here']) == []
    assert len(p._filter_glossary_for_window(glossary, ['masu desu'])) == 1


def test_empty_inputs_return_empty():
    p = make_pipeline()
    assert p._filter_glossary_for_window([], ['abc']) == []
    # source_lines rong -> fallback dung TAT ca glossary (co chu dich,
    # xem _build_prompt: "khong co source_lines -> dung tat ca")
    assert len(p._filter_glossary_for_window([{'source_term': 'x'}], [])) == 1


if __name__ == '__main__':
    tests = [
        test_cjk_term_matches_inside_japanese_text,
        test_cjk_term_matches_multiple_occurrences,
        test_latin_term_still_requires_word_boundary,
        test_empty_inputs_return_empty,
    ]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f'[OK] {fn.__name__}')
            passed += 1
        except AssertionError as e:
            print(f'[FAIL] {fn.__name__}: {e}')
            failed += 1
        except Exception as e:
            print(f'[ERR]  {fn.__name__}: {type(e).__name__}: {e}')
            failed += 1
    print(f'\n{passed} passed, {failed} failed')
    sys.exit(0 if failed == 0 else 1)