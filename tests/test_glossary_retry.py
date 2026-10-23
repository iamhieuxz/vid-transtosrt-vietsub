"""Test _get_glossary_for_window — Fix: retry per-line phải CÓ glossary.

Trước fix: hàm trả `project['glossary']` — cột này không tồn tại trong bảng
`projects` → luôn None → `_retry_per_line` mất sạch glossary (mất xưng hô,
mất tên riêng như レノさん → Reno-san).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipeline import TranslationPipeline


def make_pipeline(glossary_terms):
    p = TranslationPipeline.__new__(TranslationPipeline)
    p.enable_glossary = True
    p.glossary_terms = glossary_terms
    p.db = type('DB', (), {
        'get_glossary': staticmethod(lambda pid: [])
    })()
    return p


def test_returns_glossary_not_none():
    """Không được trả None (bug gốc) — phải là list."""
    p = make_pipeline([{'source_term': 'レノさん', 'target_term': 'Reno-san'}])
    result = p._get_glossary_for_window(
        {'_source_lines': ['レノさんが来た']}, {'id': 4})
    assert result is not None, 'phai tra list, khong phai None'
    assert len(result) == 1, result
    assert result[0]['source_term'] == 'レノさん'


def test_filters_by_window():
    """Chỉ giữ term thực sự xuất hiện trong window."""
    p = make_pipeline([
        {'source_term': 'レノさん', 'target_term': 'Reno-san'},
        {'source_term': 'ほら', 'target_term': 'Nhìn này'},
    ])
    result = p._get_glossary_for_window(
        {'_source_lines': ['レノさんが来た']}, {'id': 4})
    assert [t['source_term'] for t in result] == ['レノさん'], result


def test_no_source_lines_returns_all():
    """Không có source_lines → trả về toàn bộ (fallback an toàn)."""
    p = make_pipeline([
        {'source_term': 'レノさん', 'target_term': 'Reno-san'},
        {'source_term': 'ほら', 'target_term': 'Nhìn này'},
    ])
    result = p._get_glossary_for_window({}, {'id': 4})
    assert len(result) == 2, result


def test_missing_project_id_returns_empty():
    """Project không có 'id' → list rỗng, không crash."""
    p = make_pipeline([{'source_term': 'x', 'target_term': 'y'}])
    assert p._get_glossary_for_window({}, {}) == []


def test_disabled_glossary_returns_empty():
    """enable_glossary=False → không trả term nào."""
    p = make_pipeline([{'source_term': 'レノさん', 'target_term': 'Reno-san'}])
    p.enable_glossary = False
    assert p._get_glossary_for_window(
        {'_source_lines': ['レノさんが来た']}, {'id': 4}) == []


if __name__ == '__main__':
    tests = [
        test_returns_glossary_not_none,
        test_filters_by_window,
        test_no_source_lines_returns_all,
        test_missing_project_id_returns_empty,
        test_disabled_glossary_returns_empty,
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