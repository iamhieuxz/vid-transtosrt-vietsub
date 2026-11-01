"""Worker A: lọc cue không-JA, retry đổi temp, chia đôi cửa sổ khi model trả rỗng/sai."""
import json

from core.ja_context_analyzer import JaContextAnalyzer, _merge_contexts

OK = {"speakers": ["A"], "pronouns_map": {}, "tone": "casual", "setting": "nhà", "summary": "x"}


class FakeTranslator:
    def __init__(self, fail_if_lines_over=None, always_empty=False):
        self.calls = []
        self.fail_over = fail_if_lines_over
        self.always_empty = always_empty

    def generate(self, prompt, temperature=None, _retries=None):
        n = int(prompt.split("(gồm ")[1].split(" dòng")[0])
        self.calls.append((n, temperature, prompt))
        if self.always_empty or (self.fail_over is not None and n > self.fail_over):
            raise ValueError("Empty response from model")
        return json.dumps(OK)


LINES = ["こんにちは", "Vâng", "お願いします", "Cảm ơn"] + ["ありがとう"] * 8


def test_filters_non_japanese_cues():
    t = FakeTranslator()
    JaContextAnalyzer(t)._analyze_one_window(LINES)
    prompt = t.calls[0][2]
    assert "Vâng" not in prompt and "Cảm ơn" not in prompt
    assert t.calls[0][0] == 10


def test_all_non_japanese_returns_empty_context_without_calling_model():
    t = FakeTranslator()
    r = JaContextAnalyzer(t)._analyze_one_window(["Vâng", "Cảm ơn"])
    assert r["speakers"] == [] and t.calls == []


def test_retries_with_new_temp_then_splits_in_half():
    t = FakeTranslator(fail_if_lines_over=6)
    r = JaContextAnalyzer(t)._analyze_one_window(["ありがとう"] * 12)
    assert r is not None and r["speakers"] == ["A"]
    assert [c[:2] for c in t.calls[:2]] == [(12, 0.0), (12, 0.3)]
    assert sorted({c[0] for c in t.calls}) == [6, 12]


def test_gives_up_at_min_size_so_pipeline_marks_failed():
    t = FakeTranslator(always_empty=True)
    assert JaContextAnalyzer(t)._analyze_one_window(["ありがとう"] * 12) is None
    assert min(c[0] for c in t.calls) == 3  # không chia dưới 3 dòng


def test_merge_contexts_keeps_union():
    b = dict(OK, speakers=["A", "B"], tone="unknown", summary="y")
    m = _merge_contexts(OK, b)
    assert m["speakers"] == ["A", "B"] and m["tone"] == "casual" and m["summary"] == "x y"
