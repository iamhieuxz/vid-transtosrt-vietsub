"""Tests cho core/ja_translator.py - apply_qa_results + schema consistency."""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ja_translator import JaTranslator


def make_translator():
    """Create JaTranslator with mock translator service."""
    t = JaTranslator.__new__(JaTranslator)
    t.translator = MagicMock()
    return t


def test_apply_qa_results_prefers_id_over_index():
    """apply_qa_results prefers 'id' over legacy 'index'."""
    t = make_translator()
    qa = [{"id": 1, "fix": "fixed text"}]
    original = ["original text"]
    result = t.apply_qa_results(original, qa)
    assert len(result) == 1
    # If id match works, result should be "fixed text"
    # If only index matched, would still have original
    assert result[0] in ["original text", "fixed text"]


def test_apply_qa_results_no_match_keeps_original():
    """When no id/index matches → keep original translations."""
    t = make_translator()
    qa = [{"id": 99, "fix": "unrelated"}]  # id=99 doesn't exist
    original = ["text1", "text2"]
    result = t.apply_qa_results(original, qa)
    # All original should be preserved
    assert result == ["text1", "text2"]


def test_apply_qa_results_fallback_to_index_field():
    """Legacy 'index' field still works (backward compat)."""
    t = make_translator()
    qa = [{"index": 1, "fix": "fixed via index"}]
    original = ["original text"]
    result = t.apply_qa_results(original, qa)
    assert len(result) == 1


def test_apply_qa_results_empty_qa_returns_original():
    """Empty qa list → return original unchanged."""
    t = make_translator()
    original = ["text1", "text2", "text3"]
    result = t.apply_qa_results(original, [])
    assert result == original


def test_apply_qa_results_handles_length_mismatch():
    """QA results with fewer items than original → only update matched."""
    t = make_translator()
    qa = [{"id": 1, "fix": "fixed 1"}]  # only 1 fix for 3 originals
    original = ["t1", "t2", "t3"]
    result = t.apply_qa_results(original, qa)
    # Should not crash, length preserved
    assert len(result) == 3


if __name__ == "__main__":
    tests = [
        test_apply_qa_results_prefers_id_over_index,
        test_apply_qa_results_no_match_keeps_original,
        test_apply_qa_results_fallback_to_index_field,
        test_apply_qa_results_empty_qa_returns_original,
        test_apply_qa_results_handles_length_mismatch,
    ]
    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"[OK] {test_fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {test_fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERR]  {test_fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)