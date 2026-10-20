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


def make_polish_translator():
    """JaTranslator with all attributes polish_translate touches."""
    t = JaTranslator.__new__(JaTranslator)
    t.translator = MagicMock()
    t.character_names = []
    t.system_prompt = "SYSTEM"
    return t


def test_polish_falls_back_to_numbered_list_on_short_json():
    """JSON thiếu phần tử → retry numbered list, không bỏ cuộc giữ rough.

    Đây là lỗi thật gặp ở JUL-308: model 8B trả JSON 3/6 phần tử cho
    window 6 dòng → polish bị bỏ qua, giữ bản literal thô.
    """
    t = make_polish_translator()
    source = [f"元の文{i}" for i in range(1, 7)]
    rough = [f"bản thô {i}" for i in range(1, 7)]

    def fake_generate(prompt, temperature=None, **kw):
        # Lần 1 (JSON template) + retry temp=0 → cùng ký hiệu numbered-list
        if "OUTPUT FORMAT" in prompt and "Trả về đúng" in prompt:
            return "\n".join(f"{i}. dịch lại {i}" for i in range(1, 7))
        # JSON template → trả JSON thiếu phần tử (3 thay vì 6)
        return '[{"id":1,"text":"a"},{"id":2,"text":"b"},{"id":3,"text":"c"}]'

    t.translator.generate.side_effect = fake_generate
    result = t.polish_translate(source, rough)
    assert len(result) == 6
    assert result == [f"dịch lại {i}" for i in range(1, 7)]


def test_polish_fallback_keeps_rough_when_numbered_list_also_short():
    """Fallback cũng thiếu dòng → giữ rough, không crash."""
    t = make_polish_translator()
    source = [f"元の文{i}" for i in range(1, 7)]
    rough = [f"bản thô {i}" for i in range(1, 7)]
    t.translator.generate.return_value = "chỉ có 1. một dòng"
    result = t.polish_translate(source, rough)
    assert result == rough


def test_polish_fallback_keeps_rough_for_kana_lines_in_numbered_list():
    """Fallback numbered list có dòng kana → giữ rough của dòng đó."""
    t = make_polish_translator()
    source = ["元の文1", "元の文2"]
    rough = ["bản thô 1", "bản thô 2"]

    def fake_generate(prompt, temperature=None, **kw):
        if "Trả về đúng" in prompt:
            # numbered list, dòng 2 là kana echo
            return "1. dịch lại tốt\n2. これはかな"
        return '[{"id":1,"text":"x"}]'  # JSON thiếu phần tử -> kích hoạt fallback

    t.translator.generate.side_effect = fake_generate
    result = t.polish_translate(source, rough)
    assert result[0] == "dịch lại tốt"
    assert result[1] == "bản thô 2"


if __name__ == "__main__":
    tests = [
        test_apply_qa_results_prefers_id_over_index,
        test_apply_qa_results_no_match_keeps_original,
        test_apply_qa_results_fallback_to_index_field,
        test_apply_qa_results_empty_qa_returns_original,
        test_apply_qa_results_handles_length_mismatch,
        test_polish_falls_back_to_numbered_list_on_short_json,
        test_polish_fallback_keeps_rough_when_numbered_list_also_short,
        test_polish_fallback_keeps_rough_for_kana_lines_in_numbered_list,
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