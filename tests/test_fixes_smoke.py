"""Smoke tests for the new shared modules."""
from core.json_utils import extract_json_array, parse_json_response
from core.config_validator import validate_top_level, validate_all, validate_glossary


def test_json_extraction():
    """Extract JSON from model output (well-formed, greedy, with preamble)."""
    # Well-formed
    r = extract_json_array('[{"id": 1, "text": "hello"}]')
    assert r is not None and len(r) == 1, f"Well-formed failed: {r}"

    # With preamble text (model often adds "Here is the JSON:")
    text = 'Here is the JSON:\n[{"id": 1, "text": "x"}, {"id": 2, "text": "y"}]\nDone.'
    r = extract_json_array(text)
    assert r is not None and len(r) == 2, f"With preamble failed: {r}"

    # Empty
    assert extract_json_array("") is None
    assert extract_json_array("no json here") is None

    # Non-JSON garbage
    assert extract_json_array("]not valid[") is None
    print("test_json_extraction: OK")


def test_parse_response():
    """Parse JSON items aligned with source positions."""
    parsed = [{"id": 1, "text": "a"}, {"id": 3, "text": "c"}]
    out, is_valid = parse_json_response(parsed, n=3)
    # missing index 2 → not valid
    assert out[0] == "a"
    assert out[1] is None
    assert out[2] == "c"
    assert is_valid is False

    parsed_full = [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]
    out, is_valid = parse_json_response(parsed_full, n=2)
    assert is_valid is True
    assert out == ["a", "b"]
    print("test_parse_response: OK")


def test_config_validator():
    """Top-level + glossary validators surface errors clearly."""
    # Empty config → multiple errors
    errors = validate_top_level({})
    assert any("project" in e for e in errors), f"missing project error: {errors}"

    # Bad mode
    errors = validate_top_level({"translation": {"mode": "bad"}})
    assert any("mode" in e for e in errors)

    # Bad num_predict
    errors = validate_top_level({"model": {"num_predict": 999999}})
    assert any("num_predict" in e for e in errors)

    # Glossary
    errors = validate_glossary([{"source": "", "target": "x"}])
    assert any("source" in e for e in errors)

    # Valid minimal
    cfg = {
        "project": {"input_srt": "x", "output_srt": "y", "name": "p"},
        "model": {"name": "m", "num_predict": 1024, "temperature": 0.5},
    }
    errors = validate_top_level(cfg)
    assert errors == [], f"valid config rejected: {errors}"
    print("test_config_validator: OK")


def test_glossary_filter_word_boundary():
    """Verify glossary filter does NOT match 'masu' in 'summarize'."""
    # We don't import the pipeline here (heavy deps), just inline the logic
    def _term_present(joined, term):
        idx = joined.find(term)
        if idx < 0:
            return False
        if idx > 0:
            prev = joined[idx - 1]
            if prev.isalnum() or '\u3040' <= prev <= '\u309f' or '\u4e00' <= prev <= '\u9fff':
                idx = joined.find(term, idx + 1)
                if idx < 0:
                    return False
                if idx > 0:
                    prev = joined[idx - 1]
                    if prev.isalnum() or '\u3040' <= prev <= '\u309f' or '\u4e00' <= prev <= '\u9fff':
                        return False
        return True

    assert _term_present("summarize this", "masu") is False, "False positive: masu in summarize"
    assert _term_present("desu ka", "desu") is True
    assert _term_present("desu", "desu") is True
    assert _term_present("", "desu") is False
    print("test_glossary_filter_word_boundary: OK")


if __name__ == "__main__":
    test_json_extraction()
    test_parse_response()
    test_config_validator()
    test_glossary_filter_word_boundary()
    print("\nAll smoke tests passed.")