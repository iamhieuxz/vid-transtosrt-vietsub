"""Hồi quy cho các lỗi tìm thấy ở code review (TM nháy, guard post-processor, alias, llama lifecycle, hotword)."""
from unittest.mock import MagicMock, patch

from core.ja_qa_checker import _generate_mistaken_aliases
from core.llama_server import LlamaServerManager
from core.post_processor import RuleBasedPostProcessor


def test_source_fix_applies_even_when_replacement_already_in_line():
    # Trước đây: "dì" có trong câu -> rule bỏ qua, "Bà già" vẫn sót
    out = RuleBasedPostProcessor().process_line("Bà già ơi, dì ơi", source_text="おばさん、おばさん")
    assert "già" not in out.lower() and out.startswith("Dì")


def test_glossary_forbidden_applies_even_when_target_already_in_line():
    pp = RuleBasedPostProcessor()
    pp.set_glossary_terms([{"source_term": "媚薬", "target_term": "thuốc kích dục",
                            "context_hint": 'KHÔNG dịch "thuốc làm đẹp"'}])
    out = pp.process_line("thuốc làm đẹp hay thuốc kích dục", source_text="媚薬")
    assert "làm đẹp" not in out


def test_tsuyoshi_aliases_have_no_unrelated_word():
    assert "Thập Kích" not in _generate_mistaken_aliases("Tsuyoshi")


def _mgr():
    return LlamaServerManager({"llama_server": {"enabled": True, "port": 8180, "auto_stop": True,
                                                "stop_reused": True, "log_dir": "."}})


def test_atexit_does_not_kill_server_pipeline_never_started():
    m = _mgr()
    with patch.object(m, "health", return_value=True), patch.object(m, "stop") as stop:
        m._atexit_hook()
    stop.assert_not_called()


def test_atexit_stops_server_started_by_us():
    m = _mgr()
    m._started_by_us = True
    with patch.object(m, "stop") as stop:
        m._atexit_hook()
    stop.assert_called_once()


def test_pipeline_does_not_start_llama_for_ollama_backend():
    from core import pipeline as P
    mgr = MagicMock()
    mgr.manages_url.return_value = False
    cfg = {"model": {"name": "m", "ollama_url": "http://localhost:11434/api/generate"}}
    # Dừng ngay sau bước gate để không phải dựng cả pipeline
    with patch.object(P, "get_manager", return_value=mgr), patch.object(P, "Database", side_effect=RuntimeError):
        try:
            P.TranslationPipeline(cfg)
        except RuntimeError:
            pass
    mgr.ensure_running.assert_not_called()


def _transcribe_kwargs(adult_boost):
    from core.transcriber import WhisperTranscriber
    t = WhisperTranscriber.__new__(WhisperTranscriber)
    t.__init__.__func__  # noqa: B018 - chỉ để rõ ta không gọi __init__ (nạp model)
    return t, adult_boost


def test_adult_hotwords_only_when_boost_enabled():
    from core.transcriber import WhisperTranscriber
    with patch.object(WhisperTranscriber, "_load_model_with_fallback", return_value=MagicMock()), \
         patch.object(WhisperTranscriber, "_check_model_cache", return_value={"cached": True, "total_mb": 1}):
        plain = WhisperTranscriber({"prompt_names": ["メイ"]})
        boost = WhisperTranscriber({"adult_boost": True})
    assert plain.adult_boost is False and boost.adult_boost is True
