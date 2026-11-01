"""finish_reason=length có nội dung: nới max_tokens 1 lần, không tiêu lượt retry."""
from unittest.mock import MagicMock, patch

from core.ja_translator import (
    _LITERAL_TEMPLATE, _LITERAL_SYSTEM_COMPACT, _LITERAL_SYSTEM_COMPACT_UNCEN,
    _get_system_prompt,
)
from core.translator import TranslatorService


def _resp(text, finish):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json.return_value = {"choices": [{"message": {"content": text}, "finish_reason": finish}]}
    return r


def _svc():
    return TranslatorService("m", "http://x:8180/v1/chat/completions",
                             num_predict=100, max_retries=0)


def test_truncated_output_is_regenerated_with_bigger_budget():
    s = _svc()
    with patch("core.translator.requests.post",
               side_effect=[_resp("1. cut", "length"), _resp("1. day du", "stop")]) as post:
        out = s.generate("p")
    assert out == "1. day du"
    assert post.call_args_list[1].kwargs["json"]["max_tokens"] == 200


def test_truncation_grows_only_once():
    s = _svc()
    with patch("core.translator.requests.post",
               side_effect=[_resp("a", "length"), _resp("b", "length")]) as post:
        out = s.generate("p")
    assert out == "b" and post.call_count == 2


def test_literal_template_has_no_chinese_label():
    assert "越南" not in _LITERAL_TEMPLATE


def test_family_terms_in_every_prompt():
    for p in (_get_system_prompt("uncen"), _LITERAL_SYSTEM_COMPACT, _LITERAL_SYSTEM_COMPACT_UNCEN):
        assert "兄さん" in p
