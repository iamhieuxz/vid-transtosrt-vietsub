"""Evidence tests for AUDIT_REPORT.md v2.

These encode the *verified* findings. Tests marked `xfail(strict=True)` assert the
CORRECT behaviour and therefore FAIL against current code — they turn green the moment
the bug is fixed, at which point pytest reports XPASS and the marker must be removed.

Run:
    python -m pytest tests/test_audit_evidence.py -v

Ref: AUDIT_REPORT.md §1 (C-1), §2 (C-2), §5 (S-1)
"""
import logging
import os

import pytest

from core.exporter import _sanitize_folder_name
from core.ja_qa_checker import JaQAChecker
from core.ja_translator import JaTranslator


def _bare_translator() -> JaTranslator:
    """JaTranslator without __init__ (needs no LLM/config for apply_qa_results)."""
    return JaTranslator.__new__(JaTranslator)


# --------------------------------------------------------------------------- C-1
def test_c1_applies_fixes_when_all_lines_flagged():
    t = _bare_translator()
    translations = ["te1", "te2", "te3", "te4"]
    qa = [{"index": i + 1, "ok": False, "suggestion": f"fix{i + 1}"} for i in range(4)]

    out = t.apply_qa_results(translations, qa)

    assert out == ["fix1", "fix2", "fix3", "fix4"], (
        "BUG C-1: every suggestion was thrown away because >50% of lines were flagged"
    )


def test_c1_keeps_fixes_when_majority_flagged():
    """4 of 6 flagged (> half) -> those 4 valid suggestions must still apply.

    Boundary probed empirically: len>=4 and flagged > len/2 triggers the skip.
    4/6 = 0.66 > 0.5 -> currently discarded.
    """
    t = _bare_translator()
    translations = [f"line{i}" for i in range(6)]
    qa = [
        {"index": i + 1, "ok": i >= 4, "suggestion": f"fix{i + 1}"}
        for i in range(6)
    ]

    out = t.apply_qa_results(translations, qa)

    assert out[0] == "fix1" and out[3] == "fix4", (
        "BUG C-1: 4/6 flagged -> all four valid fixes discarded"
    )


def test_c1_boundary_exactly_half_is_applied():
    """Guard: exactly half flagged is NOT skipped (documents the current boundary)."""
    t = _bare_translator()
    translations = [f"line{i}" for i in range(6)]
    qa = [{"index": i + 1, "ok": i >= 3, "suggestion": f"fix{i + 1}"} for i in range(6)]

    out = t.apply_qa_results(translations, qa)

    assert out[0] == "fix1", "3/6 == half must still apply (boundary is strict >)"


# --------------------------------------------------------------------------- C-2
@pytest.mark.parametrize("ja", ["食べない", "行かない", "飲まない", "遊ばない", "出かけない"])
def test_c2_common_verb_negation_dropped_in_vi_is_flagged(ja):
    """JA says "do not X", VI says "X" -> must be flagged.

    These are >=4 chars so they reach the pseudo-negation check. Before the C-2 fix
    they were listed in _JA_PSEUDO_NEGATION_RE and silently accepted.
    """
    assert JaQAChecker.check_negation_mismatch(ja, "ăn") is True


@pytest.mark.parametrize("ja", ["しない", "見ない"])
def test_c2_short_line_guard_is_intentional(ja):
    """Documents a deliberate limitation, NOT a bug.

    `しない` / `見ない` are 3 chars and are excluded by the `len(ja) < 4` guard in
    check_negation_mismatch, not by the pseudo-negation list.

    Evidence for keeping the guard (measured on translation.db, 2635 lines): only 6
    lines are <=3 chars and contain a negation, and all 6 were already translated
    correctly (5 are ambiguous `ない?` fragments). Lowering the guard would catch zero
    real errors while adding false positives on `ない?` tag fragments.
    """
    assert JaQAChecker.check_negation_mismatch(ja, "ăn") is False


@pytest.mark.parametrize(
    "ja,vi",
    [
        ("食べない", "không ăn"),
        ("行かないで", "đừng đi"),
        ("すみません", "xin lỗi"),
        ("かもしれない", "có lẽ"),
        ("行かなければならない", "phải đi"),
        ("しか食べない", "chỉ ăn"),
        ("わからない", "không hiểu"),
    ],
)
def test_c2_no_false_positive_on_correct_translations(ja, vi):
    """Guard: the checker must NOT fire on correct/idiomatic translations."""
    assert JaQAChecker.check_negation_mismatch(ja, vi) is False


def test_c2_detects_unambiguous_negation_loss():
    """Guard: a case that does work today, to protect against over-correction."""
    assert JaQAChecker.check_negation_mismatch("知らない", "biết") is True


# --------------------------------------------------------------------------- S-1
def test_s1_parent_dir_component_is_neutralised():
    base = os.path.normpath("E:/OUT")
    joined = os.path.normpath(os.path.join(base, _sanitize_folder_name("..")))

    assert joined == base or joined.startswith(base + os.sep), (
        f"S-1: '..' resolved outside base -> {joined}"
    )


@pytest.mark.parametrize("name", ["EYAN-029", "../..", "..\\..\\etc", "a/../../b", "C:\\Windows"])
def test_s1_other_inputs_stay_inside(name):
    """Guard: slash replacement already neutralises these forms."""
    base = os.path.normpath("E:/OUT")
    joined = os.path.normpath(os.path.join(base, _sanitize_folder_name(name)))

    assert joined == base or joined.startswith(base + os.sep)
    assert _sanitize_folder_name(name) != ""


@pytest.mark.parametrize("name", ["..", ".", "...", "   ", ""])
def test_s1_degenerate_names_fall_back_to_output(name):
    """Names made only of dots (or blank) must not become a path component."""
    assert _sanitize_folder_name(name) == "output"


# --------------------------------------------------------------------------- C-1 edge cases
@pytest.mark.parametrize(
    "item",
    [
        {"index": 99, "ok": False, "suggestion": "x"},      # out of range
        {"index": None, "ok": False, "suggestion": "x"},     # None index
        {"index": 1, "ok": False, "suggestion": "   "},      # blank suggestion
        {"ok": False, "suggestion": "x"},                    # no index at all
    ],
)
def test_c1_malformed_entries_are_ignored(item):
    """apply_qa_results must never raise on malformed QA entries."""
    t = _bare_translator()
    assert t.apply_qa_results(["a"], [item]) == ["a"]


def test_c1_string_index_is_coerced():
    """LLM sometimes emits "index": "1" — must still apply, not crash."""
    t = _bare_translator()
    out = t.apply_qa_results(["a"], [{"index": "1", "ok": False, "suggestion": "B"}])
    assert out == ["B"]


# --------------------------------------------------------------------------- C-3
def test_c3_exporter_logs_untranslated_sub_indices(tmp_path, caplog):
    """The [UNTRANSLATED] warning must name which subs were lost."""
    from core.exporter import _atomic_write_srt

    items = [
        {"sub_index": 11, "translated_text": "ok", "original_text": "a",
         "start_time": "00:00:01,000", "end_time": "00:00:02,000"},
        {"sub_index": 12, "translated_text": "", "original_text": "b",
         "start_time": "00:00:03,000", "end_time": "00:00:04,000"},
        {"sub_index": 77, "translated_text": "", "original_text": "c",
         "start_time": "00:00:05,000", "end_time": "00:00:06,000"},
    ]
    out_path = str(tmp_path / "x.srt")

    with caplog.at_level(logging.WARNING):
        _atomic_write_srt(items, out_path)

    warn = [r.message for r in caplog.records if "not translated" in r.message]
    assert len(warn) == 1, caplog.text
    assert "12" in warn[0] and "77" in warn[0], warn[0]
    assert os.path.exists(out_path)


# --------------------------------------------------------------------------- R-2
def test_r2_named_constants_keep_original_values():
    """The extracted constants must equal the magic numbers they replaced."""
    from core.validator import MAX_SUBTITLE_CHARS, MAX_TRANSLATION_RATIO

    assert MAX_SUBTITLE_CHARS == 150
    assert MAX_TRANSLATION_RATIO == 8.0


# --------------------------------------------------------------------------- P3
# Truncation: threshold derived from 2640 real JA/VI pairs (median ratio 2.54,
# p05 1.42, p01 0.94). Vietnamese runs ~2.5x longer than Japanese, so a ratio
# below 0.90 means clauses were dropped.
@pytest.mark.parametrize(
    "ja,vi",
    [
        # the two real defects found in the NSFS-504 run
        ("判断が甘いんだよ 利益を削って それで数字出して 意味ないだろ バカなのか?",
         "Phán đoán của anh quá ngây thơ."),
        ("奥さんとも 今日が最後です ストレスの吐け口になってくれて ありがとうございました それ",
         "Với bà xã, hôm nay là ngày cuối cùng."),
        # garbage output
        ("ちいいかって 聞いてんだよ", "Rams."),
        ("細かいところでわからないことがあったらぜひ私の方にお問い合わせくださいそれで",
         "Và thế là..."),
    ],
)
def test_p3_dropped_clauses_are_flagged(ja, vi):
    assert JaQAChecker.check_length_anomaly(ja, vi) == "severe_truncation"


@pytest.mark.parametrize(
    "ja,vi",
    [
        ("君はこの数字をどう見ている?", "Cậu nhìn con số này như thế nào?"),
        ("ありがとうございます", "Cảm ơn."),                    # ja<12 guard
        ("うん うん うん うん うん ん", "Ừ ừ ừ ừ ừ ừm"),        # repetition is legit
        ("何か話している", ""),                                  # different issue
    ],
)
def test_p3_no_false_positive(ja, vi):
    got = JaQAChecker.check_length_anomaly(ja, vi)
    assert got != "severe_truncation", f"{got} on {ja!r} -> {vi!r}"


def test_p3_empty_translation_still_its_own_issue():
    assert JaQAChecker.check_length_anomaly("何か話している", "") == "empty_translation"


# --------------------------------------------------------------------------- validator boundary cut
def test_p3_truncate_at_boundary_never_splits_a_word():
    from core.validator import _truncate_at_boundary

    long_ws = ("từ " * 100).strip()
    out = _truncate_at_boundary(long_ws, 150)
    assert len(out) <= 150
    assert out.endswith("từ")           # cut at space, not mid-token

    vi = ("Phán quyết của anh quá mềm yếu, cắt lợi nhuận rồi mới đưa ra con số, "
          "có ý nghĩa gì chứ? Anh còn là người ngốc không?")
    out = _truncate_at_boundary(vi, 60)
    assert out[-1] in ",.?!…;:", out     # ends on a clause boundary


def test_p3_truncate_at_boundary_passthrough():
    from core.validator import _truncate_at_boundary

    assert _truncate_at_boundary("Cảm ơn.", 150) == "Cảm ơn."
    assert _truncate_at_boundary("", 150) == ""


# --------------------------------------------------------------------------- P1
# Whisper hallucinates subtitle boilerplate over non-dialogue audio. Measured on
# NSFS-504: lowering VAD or boosting gain produced these instead of real speech.
def test_p1_boilerplate_always_dropped():
    from core.transcriber import WhisperTranscriber

    t = WhisperTranscriber.__new__(WhisperTranscriber)
    assert t._is_hallucination_segment("ご視聴ありがとうございました", 2.0) is True
    assert t._is_hallucination_segment("チャンネル登録お願いします", 1.0) is True


def test_p1_ambiguous_phrase_needs_implausible_duration():
    """'おやすみなさい' is real dialogue in a drama — only drop it when the
    timestamps are absurd (15 s of audio for 7 characters)."""
    from core.transcriber import WhisperTranscriber

    t = WhisperTranscriber.__new__(WhisperTranscriber)
    assert t._is_hallucination_segment("おやすみなさい", 15.0) is True
    assert t._is_hallucination_segment("おやすみなさい", 1.5) is False


@pytest.mark.parametrize(
    "text,duration",
    [
        ("気持ちいい", 3.0),
        ("前年同月と比べても大きな差はないので、おおむね順調かと", 5.0),
        ("", 10.0),
    ],
)
def test_p1_real_dialogue_kept(text, duration):
    from core.transcriber import WhisperTranscriber

    t = WhisperTranscriber.__new__(WhisperTranscriber)
    assert t._is_hallucination_segment(text, duration) is False


@pytest.mark.parametrize("text", ["ダメダメダメ", "イクイクイク", "やめてやめてやめて"])
def test_p1_noise_filter_keeps_adult_repetition(text):
    """The noise filter must not eat repeated adult exclamations."""
    from core.transcriber import WhisperTranscriber

    t = WhisperTranscriber.__new__(WhisperTranscriber)
    assert t._is_noise_segment(text) is False


# --------------------------------------------------------------------------- C-1b
# Regression: QA returns suggestions whose `id` is right but whose TEXT belongs to
# the previous line. Applying them shifts the whole window by one and duplicates
# the first line — the pipeline logs `DUP_TRANSLATION line k->k+1 ratio=1.00`.
# Observed on NSFS-504 cues 140-144 after the C-1 fix started applying QA.
_POLISHED = [
    "Tôi đã không còn muốn phản bội chồng nữa.",
    "Chính vì thế tôi mới muốn bị cưỡng ép bằng bạo lực.",
    "Giấc mơ của tôi đã tan biến như ảo ảnh.",
    "Vâng, tôi là Hoshina, đã xảy ra chuyện gì rồi?",
    "Vẫn chưa liên lạc được với anh ấy.",
]
_SHIFTED = [
    "Tôi đã không còn muốn phản bội chồng nữa.",           # dup of idx0
    "Tôi đã không còn muốn phản bội chồng nữa.",           # <- belongs to idx0
    "Chính vì thế tôi mới muốn bị cưỡng ép bằng bạo lực.",  # <- belongs to idx1
    "Giấc mơ kỳ lạ của tôi đã tan biến như ảo ảnh.",        # <- belongs to idx2
    "Vâng, đây là Hoshina, đã xảy ra chuyện gì rồi?",       # <- belongs to idx3
]


def test_c1b_shifted_suggestion_detected():
    from core.ja_translator import _looks_shifted

    assert _looks_shifted(_SHIFTED[1], _POLISHED, 1) is True
    assert _looks_shifted(_SHIFTED[2], _POLISHED, 2) is True
    assert _looks_shifted(_SHIFTED[3], _POLISHED, 3) is True


def test_c1b_genuine_suggestion_not_rejected():
    from core.ja_translator import _looks_shifted

    assert _looks_shifted("Nội dung thì hoàn toàn khác hẳn.", _POLISHED, 1) is False
    assert _looks_shifted("", _POLISHED, 1) is False


def test_c1b_shifted_batch_does_not_corrupt_window():
    """The whole shifted batch must be rejected: no duplicate, text unchanged."""
    t = _bare_translator()
    qa = [{"index": i + 1, "ok": False, "suggestion": s}
          for i, s in enumerate(_SHIFTED)]
    out = t.apply_qa_results(list(_POLISHED), qa)
    assert out[0] != out[1], "duplicate created — shift was applied"
    assert out == _POLISHED, "shifted suggestions must not overwrite polished text"


def test_c1b_real_fix_still_applies():
    """Guard must not block a legitimate one-line correction."""
    t = _bare_translator()
    qa = [{"index": 2, "ok": False,
           "suggestion": "Chính vì vậy tôi mới muốn bị cưỡng bức bằng bạo lực."}]
    out = t.apply_qa_results(list(_POLISHED), qa)
    assert out[1] == "Chính vì vậy tôi mới muốn bị cưỡng bức bằng bạo lực."


# --------------------------------------------------------------------------- Sentence Fragment Merger
def test_submerger_heals_sentence_predicate_fragments():
    from core.sub_merger import merge_srt_items

    items = [
        {'index': 1, 'start': '00:04:45,210', 'end': '00:04:53,600',
         'text': 'その苛立ちが内村さんに'},
        {'index': 2, 'start': '00:04:53,600', 'end': '00:04:55,240',
         'text': '向けられているのも分かっていた'},
    ]
    out = merge_srt_items(items)
    assert len(out) == 1
    assert out[0]['text'] == 'その苛立ちが内村さんに 向けられているのも分かっていた'
    assert out[0]['start'] == '00:04:45,210'
    assert out[0]['end'] == '00:04:55,240'


def test_submerger_heals_copula_fragment():
    from core.sub_merger import merge_srt_items

    items = [
        {'index': 1, 'start': '00:03:00,000', 'end': '00:03:07,560',
         'text': '助言を'},
        {'index': 2, 'start': '00:03:07,560', 'end': '00:03:08,060',
         'text': 'している'},
    ]
    out = merge_srt_items(items)
    assert len(out) == 1
    assert out[0]['text'] in ('助言をしている', '助言を している')


# --------------------------------------------------------------------------- De-translationese Prompt Guard
def test_system_prompt_contains_detranslationese_rules():
    from core.ja_translator import _BASE_SYSTEM_PROMPT, _POLISH_TEMPLATE

    assert 'DE-TRANSLATIONESE' in _BASE_SYSTEM_PROMPT
    assert 'Khử bị động gượng gạo' in _BASE_SYSTEM_PROMPT
    assert 'ĐỘC LẬP TỪNG DÒNG' in _BASE_SYSTEM_PROMPT
    assert 'De-translationese' in _POLISH_TEMPLATE


# --------------------------------------------------------------------------- Uncen Level 2 Configuration
def test_uncen_level_2_glossary_and_prompt():
    """Verify Level 2 natural explicit adult lexicon across glossary and prompts."""
    import yaml
    from core.ja_translator import _UNCEN_ADDON, _LITERAL_SYSTEM_COMPACT_UNCEN

    with open('glossary_uncen.yaml', 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    lookup = {item['source_term']: item['target_term'] for item in data}

    assert lookup['ちんぽ'] == 'con cặc'
    assert lookup['まんこ'] == 'bướm'
    assert lookup['セックス'] == 'làm tình'
    assert lookup['中出し'] == 'bắn vào trong'
    assert lookup['フェラ'] == 'bú cu'
    assert lookup['潮'] == 'nước dâm'

    assert 'bắn vào trong' in _UNCEN_ADDON
    assert 'bú cu' in _UNCEN_ADDON
    assert 'làm tình' in _UNCEN_ADDON
    assert 'bắn vào trong' in _LITERAL_SYSTEM_COMPACT_UNCEN
    assert 'bú cu' in _LITERAL_SYSTEM_COMPACT_UNCEN


# --------------------------------------------------------------------------- Optimizations 2026-11-05
def test_opt1_looks_shifted_catches_nsfs301_near_duplicate():
    """NSFS-301 cue 105/106: wording changed slightly at the end (ratio=0.816).
    Lowered threshold (0.78) + prefix check must detect and catch it."""
    from core.ja_translator import _looks_shifted

    sug = "Mọi thứ bà có thể làm đều tôi sẽ làm."
    lines = ["Đoạn trước", "Mọi thứ bà có thể làm, tôi đều làm hết.", "Đoạn sau"]
    assert _looks_shifted(sug, lines, 2) is True


def test_opt2_post_processor_uncen_level2_safety_net():
    """PostProcessor auto-converts leftover medical/formal terms to Level 2 in uncen mode."""
    from core.post_processor import RuleBasedPostProcessor

    pp_uncen = RuleBasedPostProcessor(mode="uncen")
    assert pp_uncen.process_line("Anh nhìn dương vật của tôi đi", source_text="ちんぽ見て") == "Anh nhìn con cặc của tôi đi"
    assert pp_uncen.process_line("Hai người đang quan hệ tình dục", source_text="セックスしてる") == "Hai người đang làm tình"
    assert pp_uncen.process_line("Anh xuất tinh trong rồi à", source_text="中出ししたの") == "Anh bắn vào trong rồi à"
    assert pp_uncen.process_line("Em hút dương vật cho anh nhé", source_text="フェラしてあげる") == "Em bú cu cho anh nhé"

    # Default mode must NOT touch them
    pp_default = RuleBasedPostProcessor(mode="default")
    assert pp_default.process_line("Hai người đang quan hệ tình dục", source_text="セックスしてる") == "Hai người đang quan hệ tình dục"


def test_opt3_submerger_heals_interjection_command():
    """NSFS-301 cue 115/116: 'いいの?' (1.03s) + '早く脱げよ それ' (1.58s) must be merged."""
    from core.sub_merger import merge_srt_items

    items = [
        {'index': 1, 'start': '00:18:42,200', 'end': '00:18:43,230', 'text': 'いいの?'},
        {'index': 2, 'start': '00:18:43,230', 'end': '00:18:44,810', 'text': '早く脱げよ それ'},
    ]
    out = merge_srt_items(items)
    assert len(out) == 1
    assert out[0]['text'] == 'いいの? 早く脱げよ それ'
    assert out[0]['start'] == '00:18:42,200'
    assert out[0]['end'] == '00:18:44,810'


def test_opt4_config_fast_profiling_and_beam_size():
    """Verify config.yaml has speed and 2-tier context optimizations enabled."""
    import yaml

    with open('config.yaml', 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    assert cfg['whisper']['beam_size'] == 5
    assert cfg['ja_context']['enabled'] is True
    assert cfg['ja_context']['window_size'] == 20



