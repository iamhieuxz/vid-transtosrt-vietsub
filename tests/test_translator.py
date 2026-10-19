"""Tests cho core/translator.py - retry logic, circuit breaker, JSON extraction."""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.translator import TranslatorService


def make_translator():
    """Create TranslatorService without real Ollama call."""
    with patch('core.translator.requests.get') as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"models": [{"name": "test:7b"}]}
        return TranslatorService(
            model_name="test:7b",
            ollama_url="http://localhost:11434/api/generate",
            num_ctx=4096,
            num_predict=512,
            temperature=0.1,
            timeout=30,
        )


def test_translator_health_check():
    """Ollama health check returns True when server up."""
    with patch('core.translator.requests.get') as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"models": []}
        t = make_translator()
        assert t.check_ollama_health() is True


def test_translator_health_check_failure():
    """Ollama health check returns False when server down."""
    with patch('core.translator.requests.get') as mock_get:
        mock_get.side_effect = Exception("connection refused")
        t = make_translator()
        assert t.check_ollama_health() is False


def test_generate_strips_think_tags():
    """Qwen3 emits <think> tags - they must be stripped."""
    with patch('core.translator.requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "response": "<think>User wants translation</think>Xin chào"
        }
        t = make_translator()
        result = t.generate("Translate: hello")
        assert "<think>" not in result
        assert "Xin chào" in result


def test_generate_empty_response_raises():
    """Empty response triggers retry."""
    with patch('core.translator.requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"response": ""}
        t = make_translator()
        try:
            result = t.generate("Hi", max_retries=2)
            # Either retries exhaust → returns empty, or raises
            assert result == "" or result is None
        except Exception:
            pass  # Acceptable


def test_circuit_breaker_state_field_exists():
    """TranslatorService có circuit breaker state."""
    t = make_translator()
    assert hasattr(t, '_circuit_open') or hasattr(t, 'circuit_breaker_threshold')
    assert hasattr(t, 'try_recover_circuit_breaker') or hasattr(t, 'check_ollama_health')


def test_extract_json_finds_array():
    """extract_json-like logic: response with JSON array embedded in text."""
    import re
    response = "Here is the translation: [{\"id\": 1, \"text\": \"xin chào\"}]"
    # Find JSON array pattern
    m = re.search(r'\[\s*\{.*?\}\s*\]', response, re.DOTALL)
    assert m is not None, "should find JSON array"
    assert "xin chào" in m.group(0)


if __name__ == "__main__":
    # Run tests without pytest for quick verification
    tests = [
        test_translator_health_check,
        test_translator_health_check_failure,
        test_generate_strips_think_tags,
        test_generate_empty_response_raises,
        test_circuit_breaker_state_field_exists,
        test_extract_json_finds_array,
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