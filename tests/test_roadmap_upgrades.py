"""Tests for architectural upgrades & optimizations (P0 -> P3) from DANH_GIA_VA_LO_TRINH_NANG_CAP.md."""
import ast
import pathlib
import pytest

from core.pipeline import PROMPTS
from core.precheck import WHISPER_COMMON_MISTAKES, WhisperPreCheck

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_p0_1_no_hardcoded_movie_names_in_main_and_prompts():
    """P0.1: Đảm bảo JA_AUTO_FIX trong main.py và PROMPTS['vi'] không chứa tên riêng phim cũ."""
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    # Các tên riêng phim cũ không được nằm trong JA_AUTO_FIX của main.py
    forbidden_names = ["Kachiko", "Takesho", "żeliさん", "Bia đây rồi", "quản lý chi tiêu gia đình"]
    for name in forbidden_names:
        assert name not in main_src, f"Hardcoded movie artifact {name!r} still in main.py"

    vi_rules = PROMPTS["vi"]["rules"]
    for old_name in ["Kachiko", "Takesho", "Ayune", "Shinoda", "Takao"]:
        assert old_name not in vi_rules, f"Old movie character {old_name!r} still in PROMPTS['vi']['rules']"

    # WHISPER_COMMON_MISTAKES không chứa tên riêng phim cũ
    for old_ja in ["かゆこ", "たけお", "タケホー"]:
        assert old_ja not in WHISPER_COMMON_MISTAKES


def test_p0_2_inter_window_history_passed_to_ja_translator():
    """P0.2: Đảm bảo history liên-window được render trong _render_context, literal_translate và polish_translate."""
    from unittest.mock import MagicMock
    from core.ja_translator import JaTranslator, _render_context

    # 1. _render_context có chứa history khi chỉ có history (Worker A tắt)
    ctx_only_hist = {"history": "[1] Anh về rồi đây (JA: ただいま)\n[2] Mừng anh về (JA: おかえり)"}
    rendered = _render_context(ctx_only_hist)
    assert "CÁC CÂU ĐÃ DỊCH Ở WINDOW TRƯỚC" in rendered
    assert "[1] Anh về rồi đây" in rendered

    # 2. literal_translate nhúng history vào prompt của chunk đầu tiên
    t = JaTranslator.__new__(JaTranslator)
    t.translator = MagicMock()
    t.translator.generate.return_value = "1. Xin chào\n2. Cảm ơn"
    t.character_names = []
    t.chunk_size = 8
    t.literal_system_prompt = "SYSTEM"
    t.system_prompt = "SYSTEM"

    out = t.literal_translate(["こんにちは", "ありがとう"], history="[1] Anh ơi (JA: ねえ)")
    assert out == ["Xin chào", "Cảm ơn"]
    called_prompt = t.translator.generate.call_args[0][0]
    assert "PREVIOUS TRANSLATED LINES" in called_prompt
    assert "[1] Anh ơi (JA: ねえ)" in called_prompt

    # 3. polish_translate nhúng history thông qua context_summary
    t.translator.generate.return_value = '[{"id": 1, "text": "Chào anh"}, {"id": 2, "text": "Cảm ơn anh"}]'
    polished = t.polish_translate(
        ["こんにちは", "ありがとう"],
        ["Xin chào", "Cảm ơn"],
        context_summary=ctx_only_hist,
    )
    assert polished == ["Chào anh", "Cảm ơn anh"]
    polish_prompt = t.translator.generate.call_args[0][0]
    assert "CÁC CÂU ĐÃ DỊCH Ở WINDOW TRƯỚC" in polish_prompt
    assert "[2] Mừng anh về (JA: おかえり)" in polish_prompt


def test_p0_3_context_lock_unlocked_during_llm_and_pos_range_query(tmp_path):
    """P0.3: _context_lock không bị giữ khi chờ LLM (_translate_window) và _get_context dùng get_items_by_pos_range."""
    import threading
    from unittest.mock import MagicMock
    from core.database import Database
    from core.pipeline import TranslationPipeline

    # 1. Kiểm tra _context_lock được nhả ra trong lúc gọi _translate_window
    p = TranslationPipeline.__new__(TranslationPipeline)
    p.db = MagicMock()
    p.translator = MagicMock()
    p.validator = MagicMock()
    p.validator.validate_window_content.return_value = True
    p._context_lock = threading.Lock()
    p._get_context = MagicMock(return_value={"history": "", "future": "", "context_block": ""})
    p._save_to_tm = MagicMock()
    p._retry_per_line = MagicMock(return_value=None)
    p._get_glossary_for_window = MagicMock(return_value=[])

    lock_states_during_llm = []

    def fake_translate_window(project_id, task, context, project):
        lock_states_during_llm.append(p._context_lock.locked())
        return (["Xin chào", "Tạm biệt"], ["こんにちは", "さようなら"])

    p._translate_window = fake_translate_window
    task = {
        "id": 1, "window_index": 0, "start_sub_index": 1, "end_sub_index": 2,
        "start_pos": 0, "end_pos": 1, "retry_count": 0,
        "original_text": "[1] こんにちは\n[2] さようなら",
    }
    project = {"source_lang": "ja", "target_lang": "vi", "name": "TestProj"}
    assert p._process_single_task(1, task, project, True) is True
    assert lock_states_during_llm == [False], "Lock must NOT be held during LLM _translate_window!"

    # 2. Kiểm tra get_items_by_pos_range + _get_context khớp tuyệt đối và không gọi get_all_items
    db_file = str(tmp_path / "test_p0_3.db")
    db = Database(db_file)
    proj_id = db.create_project("TestRange", "in.srt", "out.srt", "ja", "vi", 3, 2, 1, 2)
    items_input = [
        {"index": i, "start": f"00:00:0{i},000", "end": f"00:00:0{i},800", "text": f"Câu gốc {i}"}
        for i in range(1, 10)
    ]
    db.save_subtitle_items(proj_id, items_input)
    db.commit_window(proj_id, 1, 1, 3, ["Bản dịch 1", "Bản dịch 2", "Bản dịch 3"])

    p2 = TranslationPipeline.__new__(TranslationPipeline)
    p2.db = db
    p2.ja_2tier_enabled = False
    p2.ja_context_analyzer = None
    proj_row = {"history_size": 2, "future_size": 2}

    # Spy lên get_all_items để chắc chắn không bị gọi
    db.get_all_items = MagicMock(side_effect=AssertionError("get_all_items should not be called!"))
    ctx = p2._get_context(proj_id, start_pos=3, end_pos=5, project=proj_row, use_translated=True)
    assert "[2] Bản dịch 2 (JA: Câu gốc 2)" in ctx["history"]
    assert "[3] Bản dịch 3 (JA: Câu gốc 3)" in ctx["history"]
    assert "[7] Câu gốc 7" in ctx["future"]
    assert "[8] Câu gốc 8" in ctx["future"]
    assert "[9]" not in ctx["future"]


def test_p0_4_gui_tree_kill_and_no_noop_timer(monkeypatch):
    """P0.4: _kill_running_process gọi taskkill /F /T /PID trên Windows và đã bỏ _poll_log_queue."""
    from unittest.mock import MagicMock
    import gui

    assert not hasattr(gui.App, "_poll_log_queue"), "_poll_log_queue no-op timer should be removed"

    dummy_app = gui.App.__new__(gui.App)
    dummy_app.log_bridge = MagicMock()
    dummy_proc = MagicMock()
    dummy_proc.poll.return_value = None
    dummy_proc.pid = 42424
    dummy_app.process = dummy_proc

    called_cmds = []
    monkeypatch.setattr(gui.subprocess, "run", lambda cmd, **kw: called_cmds.append(cmd))
    monkeypatch.setattr(gui.sys, "platform", "win32")

    dummy_app._kill_running_process()
    assert ["taskkill", "/F", "/T", "/PID", "42424"] in called_cmds
    dummy_proc.terminate.assert_called_once()


def test_p1_1_batch_tm_and_incremental_glossary_miner(tmp_path):
    """P1.1: Kiểm tra save_translation_memory_batch và project_ngram_cache của GlossaryMiner."""
    from unittest.mock import MagicMock
    from core.database import Database
    from core.glossary_miner import GlossaryMiner

    db = Database(str(tmp_path / "test_p1_1.db"))
    # 1. Batch TM upsert + confidence priority
    entries = [
        ("ja", "vi", "お兄ちゃん大好き", "Em yêu anh trai lắm", "FILM1", 0.90),
        ("ja", "vi", "気持ちいいです", "Sướng quá đi", "FILM1", 0.95),
    ]
    inserted = db.save_translation_memory_batch(entries, min_char_count=4)
    assert inserted == 2
    assert db.get_translation_memory("ja", "vi", "お兄ちゃん大好き", domain="FILM1") == "Em yêu anh trai lắm"

    # Ghi đè với confidence thấp hơn (0.80 < 0.90) -> giữ nguyên bản dịch cũ
    db.save_translation_memory_batch([("ja", "vi", "お兄ちゃん大好き", "Bản dịch kém", "FILM1", 0.80)])
    assert db.get_translation_memory("ja", "vi", "お兄ちゃん大好き", domain="FILM1") == "Em yêu anh trai lắm"

    # Ghi đè với confidence cao hơn (1.0 > 0.90) -> cập nhật bản dịch mới
    db.save_translation_memory_batch([("ja", "vi", "お兄ちゃん大好き", "Em rất thích anh hai", "FILM1", 1.0)])
    assert db.get_translation_memory("ja", "vi", "お兄ちゃん大好き", domain="FILM1") == "Em rất thích anh hai"

    # 2. Incremental GlossaryMiner với project_ngram_cache
    pid = db.create_project("FILM1", "ja", "vi", "in.srt", "out.srt")
    items = [
        {"index": i, "start": "00:00:01,000", "end": "00:00:02,000", "text": "中出ししてください"}
        for i in range(1, 5)
    ]
    db.save_subtitle_items(pid, items)
    db.commit_window(pid, 1, 1, 4, ["Hãy xuất tinh bên trong"] * 4)
    db.update_project_status(pid, "completed", total_lines=4)

    miner = GlossaryMiner(db, {"pipeline": {"learn_glossary_min_freq": 3, "learn_glossary_min_ratio": 0.5}})
    cands1 = miner.mine_all_projects(skip_existing=False)
    assert any("中出し" in c["source_term"] for c in cands1)

    # Lần chạy thứ 2: spy lên _ja_ngrams, phải lấy từ project_ngram_cache mà không cần gọi _ja_ngrams
    miner._ja_ngrams = MagicMock(side_effect=AssertionError("_ja_ngrams should not be called on cached project!"))
    cands2 = miner.mine_all_projects(skip_existing=False)
    assert cands2 == cands1


def test_p1_2_singleton_mecab_tagger_across_modules():
    """P1.2: Đảm bảo cả 4 module dùng chung duy nhất 1 instance fugashi.Tagger từ core/mecab_utils.py."""
    from core.mecab_utils import get_tagger
    from core.pipeline import _get_ja_tagger
    from core.ja_qa_checker import _get_tagger as qa_tagger
    from core.sub_merger import _get_merger_tagger
    from core.glossary_miner import _get_tagger as miner_tagger

    t0 = get_tagger()
    assert t0 is not None
    assert _get_ja_tagger() is t0
    assert qa_tagger() is t0
    assert _get_merger_tagger() is t0
    assert miner_tagger() is t0


def test_p2_1_dynamic_few_shot_from_translation_memory(tmp_path):
    """P2.1: Tìm cặp câu tương tự bằng rapidfuzz từ TM và chèn làm few-shot vào JaTranslator."""
    from unittest.mock import MagicMock
    from core.database import Database
    from core.ja_translator import JaTranslator, _render_context

    db = Database(str(tmp_path / "test_p2_1.db"))
    db.save_translation_memory_batch([
        ("Japanese", "Vietnamese", "本当に気持ちいいです", "Thực sự sướng lắm anh ơi", "FILM1", 0.95),
        ("Japanese", "Vietnamese", "お兄ちゃんやめてください", "Anh hai ơi dừng lại đi mà", "global", 0.95),
        ("Japanese", "Vietnamese", "天気がいいですね", "Thời tiết đẹp quá nhỉ", "global", 0.95),
    ])

    # Query câu gần giống (nhưng không trùng 100%)
    examples = db.find_similar_tm_examples(
        "Japanese", "Vietnamese",
        ["すごく気持ちいいです", "お兄ちゃんやめてよ"],
        domain="FILM1",
        top_k=2,
        min_score=60.0,
    )
    assert len(examples) == 2
    srcs = {e["source_text"] for e in examples}
    assert "本当に気持ちいいです" in srcs
    assert "お兄ちゃんやめてください" in srcs

    # Kiểm tra _render_context và literal_translate nhúng tm_examples
    rendered_ctx = _render_context({"tm_examples": examples})
    assert "VÍ DỤ CÂU TƯƠNG TỰ TỪ BỘ NHỚ DỊCH" in rendered_ctx
    assert "Thực sự sướng lắm anh ơi" in rendered_ctx

    t = JaTranslator.__new__(JaTranslator)
    t.translator = MagicMock()
    t.translator.generate.return_value = "1. Sướng quá đi"
    t.character_names = []
    t.chunk_size = 8
    t.literal_system_prompt = "SYSTEM"
    out = t.literal_translate(["すごく気持ちいいです"], tm_examples=examples)
    assert out == ["Sướng quá đi"]
    prompt_sent = t.translator.generate.call_args[0][0]
    assert "SIMILAR TRANSLATED EXAMPLES" in prompt_sent
    assert "本当に気持ちいいです -> Thực sự sướng lắm anh ơi" in prompt_sent


def test_p2_2_sub_splitter_and_human_feedback_integration(tmp_path):
    """P2.2: Kiểm tra split_long_sub tách câu dài >6s và learn_from_human_srt cập nhật TM."""
    import gui
    from core.database import Database
    from core.human_feedback import learn_from_human_srt
    from core.sub_splitter import split_long_sub

    # 1. split_long_sub tách câu dài theo dấu câu Nhật
    parts = split_long_sub("今日はいい天気ですね。明日も晴れるといいな。", 0, 8000, max_duration_ms=6000)
    assert len(parts) == 2
    assert parts[0] == (0, 4000, "今日はいい天気ですね。")
    assert parts[1] == (4000, 8000, "明日も晴れるといいな。")

    # 2. learn_from_human_srt + GlossaryPage có hàm _learn_from_human_srt
    assert hasattr(gui.GlossaryPage, "_learn_from_human_srt")
    db = Database(str(tmp_path / "test_p2_2.db"))
    pid = db.create_project("FILM_HF", "ja", "vi", "in.srt", "out.srt")
    db.save_subtitle_items(pid, [
        {"index": 1, "start": "00:00:01,000", "end": "00:00:03,000", "text": "中出ししてください"},
        {"index": 2, "start": "00:00:04,000", "end": "00:00:06,000", "text": "中出ししないで"},
    ])
    db.commit_window(pid, 1, 1, 2, ["Hãy làm bên trong", "Đừng làm bên trong"])

    corrected_srt = tmp_path / "corrected.srt"
    corrected_srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nHãy xuất tinh bên trong\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nĐừng xuất tinh bên trong\n",
        encoding="utf-8",
    )
    pending_file = tmp_path / "pending.yaml"
    stats = learn_from_human_srt(db, {}, "FILM_HF", str(corrected_srt), pending_path=str(pending_file))
    assert stats["fixed_lines"] == 2
    assert db.get_translation_memory("ja", "vi", "中出ししてください", domain="FILM_HF") == "Hãy xuất tinh bên trong"


def test_p3_1_unified_cjk_leak_map_and_config_validator():
    """P3.1: Đảm bảo post_processor và ja_qa_checker dùng chung COMMON_CJK_LEAK_MAP, và main.validate_config dùng config_validator."""
    import main
    from core.ja_qa_checker import _COMMON_RESIDUAL_CJK_MAP
    from core.post_processor import COMMON_CJK_LEAK_MAP, RuleBasedPostProcessor

    assert _COMMON_RESIDUAL_CJK_MAP is COMMON_CJK_LEAK_MAP
    pp = RuleBasedPostProcessor()
    assert "xuất tinh trong" in pp.process_line("Anh muốn 中出し ngay bây giờ.")

    # main.validate_config chấp nhận mode 'cen' và bắt lỗi range số học từ config_validator
    valid_cen_cfg = {
        "project": {"input_srt": "a.srt", "output_srt": "out", "name": "P"},
        "translation": {"mode": "cen"},
        "model": {"name": "qwen", "temperature": 0.2},
    }
    assert main.validate_config(valid_cen_cfg) == []

    invalid_num_cfg = {
        "project": {"input_srt": "a.srt", "output_srt": "out", "name": "P"},
        "translation": {"mode": "uncen"},
        "model": {"name": "qwen", "temperature": 9.9},
    }
    errs = main.validate_config(invalid_num_cfg)
    assert any("model.temperature" in e for e in errs)


def test_p3_2_glossaries_subdir_and_gui_lazy_batch_rendering(tmp_path, monkeypatch):
    """P3.2: Kiểm tra _merge_glossary_files đọc từ glossaries/ và GlossaryPage lazy rendering."""
    import gui
    from core.pipeline import _merge_glossary_files

    monkeypatch.chdir(tmp_path)
    (tmp_path / "glossary_common.yaml").write_text(
        "- source_term: 先生\n  target_term: thầy giáo\n", encoding="utf-8"
    )
    gdir = tmp_path / "glossaries"
    gdir.mkdir()
    (gdir / "glossary_TESTFILM.yaml").write_text(
        "- source_term: 先生\n  target_term: bác sĩ\n- source_term: 治療室\n  target_term: phòng khám\n",
        encoding="utf-8",
    )

    merged = _merge_glossary_files("glossary_common.yaml", "glossary_TESTFILM.yaml")
    by_src = {e["source_term"]: e["target_term"] for e in merged}
    assert by_src["先生"] == "bác sĩ"
    assert by_src["治療室"] == "phòng khám"

    # Kiểm tra GlossaryPage có cơ chế lazy batch rendering (_unrendered_entries & _render_next_batch)
    assert hasattr(gui.GlossaryPage, "_render_next_batch")









