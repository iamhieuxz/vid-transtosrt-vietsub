"""Test normalize glossary entry — schema cũ (source/target) và mới (source_term).

Bug gốc: main.py seed glossary dùng `term['source']` → KeyError khi config.yaml
dùng schema mới (source_term/target_term/context_hint) như file glossary_*.yaml.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipeline import _normalize_glossary_entry


def test_old_schema():
    n = _normalize_glossary_entry(
        {'source': 'おちんちん', 'target': 'dương vật', 'context': 'từ thân mật'})
    assert n == {'source_term': 'おちんちん', 'target_term': 'dương vật',
                 'context_hint': 'từ thân mật'}, n


def test_new_schema():
    n = _normalize_glossary_entry(
        {'source_term': 'レノさん', 'target_term': 'Reno-san',
         'context_hint': 'tên riêng'})
    assert n == {'source_term': 'レノさん', 'target_term': 'Reno-san',
                 'context_hint': 'tên riêng'}, n


def test_mixed_schema_prefers_old_keys():
    """Schema cũ (source/target) được ưu tiên — backward compat với config.yaml cũ."""
    n = _normalize_glossary_entry(
        {'source': 'A', 'target': 'B', 'source_term': 'C', 'target_term': 'D'})
    assert n['source_term'] == 'A', n
    assert n['target_term'] == 'B', n


def test_strips_whitespace():
    n = _normalize_glossary_entry(
        {'source': '  あ  ', 'target': '  b  ', 'context': '  c  '})
    assert n == {'source_term': 'あ', 'target_term': 'b', 'context_hint': 'c'}, n


def test_missing_target_returns_none():
    assert _normalize_glossary_entry({'source': 'あ'}) is None
    assert _normalize_glossary_entry({'target': 'b'}) is None
    assert _normalize_glossary_entry({}) is None


if __name__ == '__main__':
    tests = [
        test_old_schema,
        test_new_schema,
        test_mixed_schema_prefers_old_keys,
        test_strips_whitespace,
        test_missing_target_returns_none,
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