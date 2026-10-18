"""
Test suite cho Subtitle Translator — chạy không cần Ollama.
Đặt tại thư mục gốc project: f:\\vid-transtosrt-vietsub-clone\\tests

Usage: python run_tests.py
"""

import sys, os, io, re, json, sqlite3, tempfile, shutil

# Force UTF-8 cho console cp1252 của Windows
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------- Test framework ----------------
class TestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.tests = []

    def test(self, name):
        def decorator(fn):
            self.tests.append((name, fn))
            return fn
        return decorator

    def assert_eq(self, actual, expected, msg=""):
        if actual == expected:
            return True
        raise AssertionError(f"{msg}\n  expected: {expected!r}\n  actual:   {actual!r}")

    def assert_true(self, val, msg=""):
        if val:
            return True
        raise AssertionError(f"{msg}\n  expected truthy, got: {val!r}")

    def assert_false(self, val, msg=""):
        if not val:
            return True
        raise AssertionError(f"{msg}\n  expected falsy, got: {val!r}")

    def assert_in(self, needle, hay, msg=""):
        if needle in hay:
            return True
        raise AssertionError(f"{msg}\n  needle: {needle!r} not in hay")

    def assert_not_in(self, needle, hay, msg=""):
        if needle not in hay:
            return True
        raise AssertionError(f"{msg}\n  needle: {needle!r} should NOT be in hay")

    def run_all(self):
        print("=" * 70)
        print(f"Running {len(self.tests)} tests")
        print("=" * 70)
        for name, fn in self.tests:
            try:
                fn(self)
                self.passed += 1
                print(f"  PASS  {name}")
            except AssertionError as e:
                self.failed += 1
                print(f"  FAIL  {name}")
                print(f"        {e}")
            except Exception as e:
                self.failed += 1
                print(f"  ERROR {name}: {type(e).__name__}: {e}")
        print("=" * 70)
        print(f"Results: {self.passed} passed, {self.failed} failed / {len(self.tests)} total")
        return self.failed == 0


T = TestRunner()


# ============= TESTS FOR VALIDATOR =============
@T.test("validator: validate_json_translation accepts valid input")
def _(self):
    from core.validator import Validator
    v = Validator()
    data = [{"id": 1, "text": "Xin chào"}, {"id": 2, "text": "Tạm biệt"}]
    self.assert_true(v.validate_json_translation(data, [1, 2]))
    self.assert_true(v.validate_json_translation(data, [1, 2]))

@T.test("validator: rejects ID order mismatch")
def _(self):
    from core.validator import Validator
    v = Validator()
    data = [{"id": 2, "text": "Tạm biệt"}, {"id": 1, "text": "Xin chào"}]
    self.assert_false(v.validate_json_translation(data, [1, 2]))

@T.test("validator: rejects length mismatch")
def _(self):
    from core.validator import Validator
    v = Validator()
    data = [{"id": 1, "text": "Xin chào"}]
    self.assert_false(v.validate_json_translation(data, [1, 2]))

@T.test("validator: detects placeholder missing")
def _(self):
    from core.validator import Validator
    v = Validator()
    # Source có %s nhưng translate không có → FAIL
    self.assert_false(v.validate_window_content(["hello %s world"], ["xin chào thế giới"]))
    # Source có %s, translate có %s → PASS
    self.assert_true(v.validate_window_content(["hello %s world"], ["xin chào %s nha"]))
    # Source không có placeholder → PASS
    self.assert_true(v.validate_window_content(["hello"], ["xin chào"]))

@T.test("validator: empty translation fails")
def _(self):
    from core.validator import Validator
    v = Validator()
    self.assert_false(v.validate_window_content(["hello"], [""]))
    self.assert_true(v.validate_window_content(["hello"], ["xin chào"]))


# ============= TESTS FOR EXPORTER =============
@T.test("exporter: _parse_time handles hh:mm:ss,mmm format")
def _(self):
    from core.exporter import _parse_time
    self.assert_eq(_parse_time("00:00:01,000"), (0, 0, 1, 0))
    self.assert_eq(_parse_time("01:23:45,678"), (1, 23, 45, 678))
    # Fix bug: 00:09:59,999 < 00:10:00,000 phải đúng
    self.assert_true(_parse_time("00:09:59,999") < _parse_time("00:10:00,000"))
    # Fix bug: 00:00:09,999 < 00:00:10,000 phải đúng (sub < 1 phút)
    self.assert_true(_parse_time("00:00:09,999") < _parse_time("00:00:10,000"))

@T.test("exporter: _parse_time handles dot format and invalid input")
def _(self):
    from core.exporter import _parse_time
    self.assert_eq(_parse_time("00:00:01.500"), (0, 0, 1, 500))
    self.assert_eq(_parse_time("invalid"), (0, 0, 0, 0))
    self.assert_eq(_parse_time(""), (0, 0, 0, 0))

@T.test("exporter: atomic write creates valid SRT")
def _(self):
    import tempfile
    from core.exporter import _atomic_write_srt
    items = [
        {'sub_index': 1, 'start_time': '00:00:01,000', 'end_time': '00:00:04,000',
         'original_text': 'こんにちは', 'translated_text': 'Xin chào'},
        {'sub_index': 2, 'start_time': '00:00:05,000', 'end_time': '00:00:08,000',
         'original_text': 'さようなら', 'translated_text': 'Tạm biệt'},
    ]
    tmp = tempfile.NamedTemporaryFile(suffix='.srt', delete=False)
    tmp.close()
    try:
        _atomic_write_srt(items, tmp.name)
        self.assert_true(os.path.exists(tmp.name))
        # Verify no leftover tmp
        self.assert_false(os.path.exists(tmp.name + '.tmp'),
                          "tmp file should be removed after atomic write")
        # Verify content
        with open(tmp.name, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        self.assert_in("Xin chào", content)
        self.assert_in("Tạm biệt", content)
    finally:
        if os.path.exists(tmp.name):
            os.remove(tmp.name)

@T.test("exporter: sorts by time tuple (not string)")
def _(self):
    import tempfile, pysrt
    from core.exporter import _atomic_write_srt
    # Items in WRONG order to verify sort fixes it
    items = [
        {'sub_index': 1, 'start_time': '00:00:30,000', 'end_time': '00:00:32,000',
         'original_text': 'third', 'translated_text': 'C'},
        {'sub_index': 2, 'start_time': '00:00:09,999', 'end_time': '00:00:10,500',
         'original_text': 'second', 'translated_text': 'B'},
        {'sub_index': 3, 'start_time': '00:00:01,000', 'end_time': '00:00:03,000',
         'original_text': 'first', 'translated_text': 'A'},
    ]
    tmp = tempfile.NamedTemporaryFile(suffix='.srt', delete=False)
    tmp.close()
    try:
        _atomic_write_srt(items, tmp.name)
        subs = pysrt.open(tmp.name, encoding='utf-8-sig')
        texts = [s.text for s in subs]
        self.assert_eq(texts, ['A', 'B', 'C'], "phải sort theo thời gian, không phải input order")
    finally:
        if os.path.exists(tmp.name):
            os.remove(tmp.name)


# ============= TESTS FOR DATABASE =============
@T.test("database: create_project + get_project roundtrip")
def _(self):
    from core.database import Database
    db_path = "_test_db.db"
    if os.path.exists(db_path): os.remove(db_path)
    try:
        db = Database(db_path)
        pid = db.create_project(
            name="test1", source_lang="Japanese", target_lang="Vietnamese",
            input_srt="x.srt", output_srt="y", window_size=6, history_size=12, future_size=4,
        )
        proj = db.get_project(pid)
        self.assert_eq(proj['name'], 'test1')
        self.assert_eq(proj['source_lang'], 'Japanese')
        self.assert_eq(proj['status'], 'pending')
    finally:
        if os.path.exists(db_path): os.remove(db_path)

@T.test("database: save_subtitle_items UPSERT (no duplicate)")
def _(self):
    from core.database import Database
    db_path = "_test_db2.db"
    if os.path.exists(db_path): os.remove(db_path)
    try:
        db = Database(db_path)
        pid = db.create_project(
            name="test2", source_lang="Japanese", target_lang="Vietnamese",
            input_srt="x.srt", output_srt="y", window_size=6, history_size=12, future_size=4,
        )
        items = [
            {'index': 1, 'start': '00:00:01,000', 'end': '00:00:04,000', 'text': 'こんにちは'},
            {'index': 2, 'start': '00:00:05,000', 'end': '00:00:08,000', 'text': 'さようなら'},
        ]
        db.save_subtitle_items(pid, items)
        # Call again (simulate restart) — should not duplicate
        db.save_subtitle_items(pid, items)
        all_items = db.get_all_items(pid)
        self.assert_eq(len(all_items), 2, "phải giữ 2 items, không duplicate")
    finally:
        if os.path.exists(db_path): os.remove(db_path)

@T.test("database: claim_task race-safe (returns window once)")
def _(self):
    from core.database import Database
    db_path = "_test_db3.db"
    if os.path.exists(db_path): os.remove(db_path)
    try:
        db = Database(db_path)
        pid = db.create_project(
            name="test3", source_lang="Japanese", target_lang="Vietnamese",
            input_srt="x.srt", output_srt="y", window_size=6, history_size=12, future_size=4,
        )
        # Save 3 windows
        db.save_window(pid, 0, 1, 6, 0, 5, "[1] line1\n[2] line2")
        db.save_window(pid, 1, 7, 12, 6, 11, "[7] line7\n[8] line8")
        db.save_window(pid, 2, 13, 18, 12, 17, "[13] line13\n[14] line14")
        # Claim 3 lần — mỗi lần phải trả về window khác nhau
        t1 = db.claim_task(pid)
        t2 = db.claim_task(pid)
        t3 = db.claim_task(pid)
        self.assert_true(t1 is not None)
        self.assert_true(t2 is not None)
        self.assert_true(t3 is not None)
        self.assert_eq(len({t1['id'], t2['id'], t3['id']}), 3, "3 claim phải trả 3 window khác nhau")
        # Claim thêm lần nữa — phải trả None (no pending)
        t4 = db.claim_task(pid)
        self.assert_eq(t4, None)
    finally:
        if os.path.exists(db_path): os.remove(db_path)

@T.test("database: recover_stuck_tasks increments retry_count (fix bug)")
def _(self):
    import time, gc
    from core.database import Database
    db_path = "_test_db4.db"
    if os.path.exists(db_path): os.remove(db_path)
    try:
        db = Database(db_path)
        pid = db.create_project(
            name="test4", source_lang="Japanese", target_lang="Vietnamese",
            input_srt="x.srt", output_srt="y", window_size=6, history_size=12, future_size=4,
        )
        db.save_window(pid, 0, 1, 6, 0, 5, "[1] line1")
        t1 = db.claim_task(pid)
        # Set started_at quá khứ để recover detect được stuck
        with db._get_connection() as conn:
            conn.execute(
                "UPDATE windows SET processing_started_at=? WHERE id=?",
                (time.time() - 1000, t1['id'])
            )
            conn.commit()
        db.recover_stuck_tasks(pid, timeout_seconds=600)
        # Verify qua API riêng (mở connection mới)
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT status, retry_count, error_message FROM windows WHERE id=?",
                (t1['id'],)
            ).fetchone()
        self.assert_eq(row['status'], 'pending')
        self.assert_eq(row['retry_count'], 1, "phải tăng retry_count (fix bug cũ tăng)")
        self.assert_eq(row['error_message'], 'stuck_timeout', "phải đánh dấu error đặc trưng")
        # Force cleanup trước khi remove file (Windows giữ file)
        del db
        gc.collect()
    finally:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except PermissionError:
                pass

@T.test("database: mark_task_dead moves exhausted window to DLQ")
def _(self):
    import gc
    from core.database import Database
    db_path = "_test_db5.db"
    if os.path.exists(db_path): os.remove(db_path)
    try:
        db = Database(db_path)
        pid = db.create_project(
            name="test5", source_lang="Japanese", target_lang="Vietnamese",
            input_srt="x.srt", output_srt="y", window_size=6, history_size=12, future_size=4,
        )
        db.save_window(pid, 0, 1, 6, 0, 5, "[1] line1")
        t1 = db.claim_task(pid)
        db.mark_task_dead(t1['id'], "test_fail")
        with db._get_connection() as conn:
            row = conn.execute("SELECT status FROM windows WHERE id=?", (t1['id'],)).fetchone()
            dlq = conn.execute("SELECT COUNT(*) c FROM dead_letter_queue WHERE window_id=?",
                               (t1['id'],)).fetchone()
        self.assert_eq(row['status'], 'dead_letter')
        self.assert_true(dlq['c'] >= 1, "phải có entry trong DLQ")
        self.assert_eq(db.count_dead_letter(pid), 1)
        del db
        gc.collect()
    finally:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except PermissionError:
                pass


# ============= TESTS FOR JA TRANSLATOR2 METRIC =============
@T.test("ja_trans2: refine metric counts actually-changed lines")
def _(self):
    from core.ja_translator2 import JaTranslator2

    # Mock translator: trả JSON giả, identifier 'id' khớp, text=refined
    class MockTrans:
        def generate(self, prompt, temperature=None, num_predict=None, _retries=None):
            # Đếm số dòng có "J:" trong prompt (format mới)
            n = len(re.findall(r'(?:^|\n)\s*\d+\.\s*J:', prompt, re.MULTILINE))
            if n == 0:
                # Fallback: đếm "V:" (translated line marker)
                n = len(re.findall(r'(?:^|\n)\s*\d+\.\s*V:', prompt, re.MULTILINE))
            items = [{"id": i+1, "text": f"REFINED-{i+1}"} for i in range(n)]
            return json.dumps(items)

    t = JaTranslator2(MockTrans(), batch_size=2)
    sources = ["line1", "line2"]
    translations = ["translation1", "translation2"]
    out = t.refine(sources, translations)
    self.assert_eq(len(out), 2)

    # Test refine_project metric
    class MockDB:
        def __init__(self):
            self.translated_text = {}
            self.id_counter = 0
            self.items = []
        def get_all_items(self, pid):
            return self.items
        def update_item_translation(self, item_id, text):
            self.translated_text[item_id] = text

    db = MockDB()
    db.items = [
        {'id': 1, 'sub_index': 1, 'original_text': 'orig1',
         'translated_text': 'trans1'},
        {'id': 2, 'sub_index': 2, 'original_text': 'orig2',
         'translated_text': 'trans2'},
    ]
    stats = t.refine_project(db, 1)
    self.assert_eq(stats['total'], 2)
    # Cả 2 dòng đều thay đổi → refined = 2
    self.assert_eq(stats['refined'], 2, "refined phải đếm dòng thực sự thay đổi")


# ============= REGRESSION TESTS FOR P0 BUGS =============
@T.test("BUG 3 REGRESSION: _parse_srt preserves newlines")
def _(self):
    """Multi-line SRT phải giữ newline trong DB."""
    import gc
    from core.database import Database
    from core.pipeline import TranslationPipeline

    db_path = "_test_bug3.db"
    if os.path.exists(db_path): os.remove(db_path)
    try:
        # Patch database path
        import core.database
        orig = core.database.Database.__init__
        core.database.Database.__init__ = lambda self, db_path=db_path: (
            setattr(self, 'db_path', db_path) or self._init_db()
        )

        # Dùng temp SRT
        test_srt = "_test_parse.srt"
        with open(test_srt, 'w', encoding='utf-8') as f:
            f.write("1\n00:00:01,000 --> 00:00:04,000\nこんにちは\nお元気ですか？\n")

        db = Database()
        pid = db.create_project(
            name="test-bug3", source_lang="Japanese", target_lang="Vietnamese",
            input_srt=test_srt, output_srt=".", window_size=6, history_size=12, future_size=4,
        )

        config = {
            'project': {'source_lang': 'Japanese', 'target_lang': 'Vietnamese', 'name': 'test-bug3'},
            'model': {'name': 'mock', 'ollama_url': 'http://localhost:0', 'temperature': 0.1,
                      'num_ctx': 4096, 'num_predict': 1024, 'timeout': 30, 'repeat_penalty': 1.2},
            'pipeline': {'enable_glossary': True, 'max_retries': 3, 'num_workers': 1,
                         'checkpoint_interval': 20, 'retry_delay': 1,
                         'heartbeat_timeout': 600, 'circuit_breaker_threshold': 100,
                         'circuit_breaker_cooldown': 60},
            'window': {'size': 6, 'history': 12, 'future': 4},
            'ja_trans2': {'enabled': False, 'batch_size': 10},
            'ja_context': {'enabled': False, 'window_size': 18},
        }
        pl = TranslationPipeline(config)
        pl._parse_srt(pid, test_srt)

        items = db.get_all_items(pid)
        self.assert_eq(len(items), 1)
        # Phải có newline giữa "こんにちは" và "お元気ですか？"
        self.assert_in('\n', items[0]['original_text'], "Phải giữ newline trong multi-line sub")
        self.assert_eq(items[0]['original_text'], 'こんにちは\nお元気ですか？')

        os.remove(test_srt)
        core.database.Database.__init__ = orig
        del pl, db
        gc.collect()
    finally:
        if os.path.exists(db_path):
            try: os.remove(db_path)
            except PermissionError: pass


@T.test("BUG 1 REGRESSION: _translate_window fail-fast on malformed line")
def _(self):
    """Khi line trong window không parse được → return None, không silent skip."""
    import gc
    from core.database import Database
    from core.pipeline import TranslationPipeline

    db_path = "_test_bug1.db"
    if os.path.exists(db_path): os.remove(db_path)
    try:
        import core.database
        orig = core.database.Database.__init__
        core.database.Database.__init__ = lambda self, db_path=db_path: (
            setattr(self, 'db_path', db_path) or self._init_db()
        )

        db = Database()
        pid = db.create_project(
            name="test-bug1", source_lang="Japanese", target_lang="Vietnamese",
            input_srt="x.srt", output_srt=".", window_size=6, history_size=12, future_size=4,
        )
        # Save window có 1 line malformed
        bad_window = "[1] valid_line\nthis is broken\n[2] another"
        db.save_window(pid, 0, 1, 2, 0, 1, bad_window)

        config = {
            'project': {'source_lang': 'Japanese', 'target_lang': 'Vietnamese', 'name': 'test-bug1'},
            'model': {'name': 'mock', 'ollama_url': 'http://localhost:0', 'temperature': 0.1,
                      'num_ctx': 4096, 'num_predict': 1024, 'timeout': 30, 'repeat_penalty': 1.2},
            'pipeline': {'enable_glossary': True, 'max_retries': 3, 'num_workers': 1,
                         'checkpoint_interval': 20, 'retry_delay': 1,
                         'heartbeat_timeout': 600, 'circuit_breaker_threshold': 100,
                         'circuit_breaker_cooldown': 60},
            'window': {'size': 6, 'history': 12, 'future': 4},
            'ja_trans2': {'enabled': False, 'batch_size': 10},
            'ja_context': {'enabled': False, 'window_size': 18},
        }
        pl = TranslationPipeline(config)
        task = db.claim_task(pid)
        # _translate_window gọi trực tiếp — không có LLM call
        # Mock context rỗng
        result = pl._translate_window(pid, task, context={"history": "", "future": ""}, project=db.get_project(pid))
        # Phải return None (fail-fast) thay vì silently skip
        self.assert_eq(result, None, "fail-fast: trả None khi có line malformed, không skip thầm lặng")

        core.database.Database.__init__ = orig
        del pl, db
        gc.collect()
    finally:
        if os.path.exists(db_path):
            try: os.remove(db_path)
            except PermissionError: pass


@T.test("BUG 2 REGRESSION: _translate_window_ja fail-fast on length mismatch")
def _(self):
    """Khi JA pass trả thiếu dòng → phải return None, KHÔNG pad bằng source gốc Nhật."""
    import gc
    from core.database import Database
    from core.pipeline import TranslationPipeline

    db_path = "_test_bug2.db"
    if os.path.exists(db_path): os.remove(db_path)
    try:
        import core.database
        orig = core.database.Database.__init__
        core.database.Database.__init__ = lambda self, db_path=db_path: (
            setattr(self, 'db_path', db_path) or self._init_db()
        )

        db = Database()
        pid = db.create_project(
            name="test-bug2", source_lang="Japanese", target_lang="Vietnamese",
            input_srt="x.srt", output_srt=".", window_size=6, history_size=12, future_size=4,
        )
        db.save_window(pid, 0, 1, 3, 0, 2, "[1] こんにちは\n[2] さようなら\n[3] また明日")

        config = {
            'project': {'source_lang': 'Japanese', 'target_lang': 'Vietnamese', 'name': 'test-bug2'},
            'model': {'name': 'mock', 'ollama_url': 'http://localhost:0', 'temperature': 0.1,
                      'num_ctx': 4096, 'num_predict': 1024, 'timeout': 30, 'repeat_penalty': 1.2},
            'pipeline': {'enable_glossary': True, 'max_retries': 3, 'num_workers': 1,
                         'checkpoint_interval': 20, 'retry_delay': 1,
                         'heartbeat_timeout': 600, 'circuit_breaker_threshold': 100,
                         'circuit_breaker_cooldown': 60},
            'window': {'size': 6, 'history': 12, 'future': 4},
            'ja_trans2': {'enabled': False, 'batch_size': 10},
            'ja_context': {'enabled': False, 'window_size': 18},
        }
        pl = TranslationPipeline(config)

        # Patch ja_translator (literal trả thiếu 1 dòng)
        class MockJaTranslator:
            def literal_translate(self, source_lines, glossary_terms=None):
                return ["Xin chào", "Tạm biệt"]  # chỉ 2 dòng cho 3 dòng input
            def polish_translate(self, src, rough, glossary_terms=None, context_summary=None):
                return rough
            def qa_review(self, src, trans, glossary_terms=None, context_summary=None):
                return []
            def apply_qa_results(self, trans, qa):
                return trans

        pl.ja_translator = MockJaTranslator()
        pl._is_japanese_source = True
        # ja_context_analyzer phải khác None mới chạy ja path
        pl.ja_context_analyzer = object()

        task = db.claim_task(pid)
        # Wrap context dict
        result = pl._translate_window(pid, task, context={"history": "", "future": ""}, project=db.get_project(pid))
        # Phải return None
        self.assert_eq(result, None, "fail-fast: trả None khi JA length mismatch, không pad bằng Nhật")
        # Verify: rough đã bị pad → fail-fast ngăn chặn
        # (đã verify logic trong code path)

        # Test case polish length mismatch — fallback về rough, fail-fast nếu rough cũng sai
        class MockJaTranslator2:
            def literal_translate(self, source_lines, glossary_terms=None):
                return ["Xin chào", "Tạm biệt", "Hẹn gặp lại"]
            def polish_translate(self, src, rough, glossary_terms=None, context_summary=None):
                return ["polished1"]  # thiếu 2 dòng so với 3
            def qa_review(self, src, trans, glossary_terms=None, context_summary=None):
                return []
            def apply_qa_results(self, trans, qa):
                return trans

        # Reset window
        db.reactivate_window(task['id'])
        pl.ja_translator = MockJaTranslator2()
        result2 = pl._translate_window(pid, task, context={"history": "", "future": ""}, project=db.get_project(pid))
        # Polish trả 1 dòng cho 3 → fallback rough = 3 dòng → validator có thể fail nhưng KHÔNG phải None
        # Tùy validator, ta chỉ verify không có tiếng Nhật trong output
        if result2 is not None:
            translations, sources = result2
            for t in translations:
                # Check không có kana/kanji
                has_jp = any(0x3040 <= ord(c) <= 0x30ff or 0x4e00 <= ord(c) <= 0x9fff for c in t)
                if has_jp:
                    raise AssertionError(f"BUG: Japanese character leaked in translation: {t!r}")

        core.database.Database.__init__ = orig
        del pl, db
        gc.collect()
    finally:
        if os.path.exists(db_path):
            try: os.remove(db_path)
            except PermissionError: pass


# ============= TESTS FOR TRANSLATOR JSON EXTRACTION =============
@T.test("translator: extract_json from messy LLM output")
def _(self):
    # Mock TranslatorService (không cần thật)
    from core.translator import TranslatorService
    # Không cần __init__ đầy đủ - chỉ dùng static method
    t = TranslatorService.__new__(TranslatorService)
    t.max_retries = 0
    # JSON hợp lệ
    valid = t.extract_json('[{"id":1,"text":"A"},{"id":2,"text":"B"}]')
    self.assert_true(valid is not None)
    self.assert_eq(len(valid), 2)
    # JSON có comment text thừa
    messy = t.extract_json('Here is the result:\n[{"id":1,"text":"A"}]\nDone.')
    self.assert_true(messy is not None)
    # JSON rỗng
    self.assert_eq(t.extract_json(''), None)


# ============= E2E PIPELINE TESTS =============
@T.test("E2E: ZH pipeline (non-JA) translates via mock LLM")
def _(self):
    """Test full pipeline với source_lang != Japanese (dùng standard ZH path)."""
    import gc
    from core.database import Database
    from core.pipeline import TranslationPipeline

    db_path = "_test_e2e_zh.db"
    if os.path.exists(db_path): os.remove(db_path)
    try:
        import core.database
        orig = core.database.Database.__init__
        core.database.Database.__init__ = lambda self, db_path=db_path: (
            setattr(self, 'db_path', db_path) or self._init_db()
        )

        # Tạo SRT test 6 sub
        test_srt = "_test_e2e.srt"
        with open(test_srt, 'w', encoding='utf-8') as f:
            for i in range(1, 7):
                t1 = (i-1) * 5
                t2 = i * 5
                f.write(f"{i}\n00:00:{t1:02d},000 --> 00:00:{t2:02d},000\nHello world number {i}\n\n")

        db = Database()
        pid = db.create_project(
            name="test-e2e", source_lang="English", target_lang="Vietnamese",
            input_srt=test_srt, output_srt=".", window_size=6, history_size=12, future_size=4,
        )

        config = {
            'project': {'source_lang': 'English', 'target_lang': 'Vietnamese', 'name': 'test-e2e'},
            'model': {'name': 'mock', 'ollama_url': 'http://localhost:0', 'temperature': 0.1,
                      'num_ctx': 4096, 'num_predict': 1024, 'timeout': 30, 'repeat_penalty': 1.2},
            'pipeline': {'enable_glossary': True, 'max_retries': 3, 'num_workers': 1,
                         'checkpoint_interval': 20, 'retry_delay': 1,
                         'heartbeat_timeout': 600, 'circuit_breaker_threshold': 100,
                         'circuit_breaker_cooldown': 60},
            'window': {'size': 6, 'history': 12, 'future': 4},
            'ja_trans2': {'enabled': False, 'batch_size': 10},
            'ja_context': {'enabled': False, 'window_size': 18},
        }
        pl = TranslationPipeline(config)
        # Patch translator - detect N lines từ task.original_text (đã có sẵn trong DB)
        class MockTranslator:
            def __init__(self):
                self.calls = 0
            def try_recover_circuit_breaker(self): return True
            def reset_circuit_breaker(self): pass
            def warmup(self, timeout=300): return True
            def generate(self, prompt, temperature=None, num_predict=None, _retries=None):
                self.calls += 1
                # Try to get N from "Dich CHINH XAC N dong" (VI) or "Translate EXACTLY N lines" (EN)
                m = re.search(r'CHINH XAC\s+(\d+)\s*dong', prompt)
                if not m:
                    m = re.search(r'EXACTLY\s+(\d+)\s+lines', prompt)
                if not m:
                    m = re.search(r'\{\s*n\s*\}', prompt)  # placeholder {n}
                if m and m.group(1).isdigit():
                    n = int(m.group(1))
                else:
                    # Fallback: count "[N] " in HIEN TAI / CURRENT LINES block only
                    if 'HIEN TAI' in prompt:
                        after = prompt.split('HIEN TAI', 1)[1]
                    elif 'CURRENT LINES' in prompt:
                        after = prompt.split('CURRENT LINES', 1)[1]
                    else:
                        after = prompt
                    n = len(re.findall(r'\[\d+\]', after.split('NEXT')[0] if 'NEXT' in after else after))
                if n == 0:
                    n = 1
                items = [{"id": i+1, "text": f"Ban dich {i+1}"} for i in range(n)]
                return json.dumps(items)
            def extract_json(self, text):
                try: return json.loads(text)
                except: return None
            def fallback_translate(self, *a, **kw): return None
            def raw_translate(self, src, *a, **kw): return src
            def polish_translate(self, src, rough, *a, **kw): return rough
        pl.translator = MockTranslator()

        pl.run(pid)

        # Verify: tất cả 6 items đã được dịch
        items = db.get_all_items(pid)
        translated = [it for it in items if it.get('translated_text')]
        self.assert_eq(len(translated), 6, f"phải dịch đủ 6 items, got {len(translated)}")
        for i, it in enumerate(items, 1):
            self.assert_in(f"Ban dich {i}", it['translated_text'])

        # Export
        out_path = "_test_e2e_out.srt"
        pl.exporter.export(pid, out_path)
        self.assert_true(os.path.exists(out_path))
        with open(out_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        self.assert_in("Ban dich 1", content)
        self.assert_in("Ban dich 6", content)

        # Cleanup
        os.remove(test_srt)
        os.remove(out_path)
        core.database.Database.__init__ = orig
        del pl, db
        gc.collect()
    finally:
        if os.path.exists(db_path):
            try: os.remove(db_path)
            except PermissionError: pass


@T.test("E2E: JA pipeline with 3-pass mock + transliteration")
def _(self):
    """Test JA path với mock 3-pass trả đúng length."""
    import gc
    from core.database import Database
    from core.pipeline import TranslationPipeline

    db_path = "_test_e2e_ja.db"
    if os.path.exists(db_path): os.remove(db_path)
    try:
        import core.database
        orig = core.database.Database.__init__
        core.database.Database.__init__ = lambda self, db_path=db_path: (
            setattr(self, 'db_path', db_path) or self._init_db()
        )

        test_srt = "_test_e2e_ja.srt"
        with open(test_srt, 'w', encoding='utf-8') as f:
            for i in range(1, 7):
                t1 = (i-1) * 5
                t2 = i * 5
                f.write(f"{i}\n00:00:{t1:02d},000 --> 00:00:{t2:02d},000\nこんにちは {i}\n\n")

        db = Database()
        pid = db.create_project(
            name="test-e2e-ja", source_lang="Japanese", target_lang="Vietnamese",
            input_srt=test_srt, output_srt=".", window_size=6, history_size=12, future_size=4,
        )

        config = {
            'project': {'source_lang': 'Japanese', 'target_lang': 'Vietnamese', 'name': 'test-e2e-ja'},
            'model': {'name': 'mock', 'ollama_url': 'http://localhost:0', 'temperature': 0.1,
                      'num_ctx': 4096, 'num_predict': 1024, 'timeout': 30, 'repeat_penalty': 1.2},
            'pipeline': {'enable_glossary': True, 'max_retries': 3, 'num_workers': 1,
                         'checkpoint_interval': 20, 'retry_delay': 1,
                         'heartbeat_timeout': 600, 'circuit_breaker_threshold': 100,
                         'circuit_breaker_cooldown': 60},
            'window': {'size': 6, 'history': 12, 'future': 4},
            'ja_trans2': {'enabled': False, 'batch_size': 10},
            'ja_context': {'enabled': False, 'window_size': 18},
        }
        pl = TranslationPipeline(config)

        # Mock base translator
        class MockBaseTranslator:
            def __init__(self):
                self.calls = 0
            def try_recover_circuit_breaker(self): return True
            def reset_circuit_breaker(self): pass
            def warmup(self, timeout=300): return True
            def generate(self, prompt, temperature=None, num_predict=None, _retries=None):
                self.calls += 1
                # Trả JSON đúng số dòng theo prompt
                n = prompt.count('ORIGINAL:') if 'ORIGINAL:' in prompt else len(re.findall(r'^\d+\.', prompt, re.MULTILINE))
                if n == 0:
                    n = 1
                items = [{"id": i+1, "text": f"ban dich {i+1}"} for i in range(n)]
                return json.dumps(items)
            def extract_json(self, text):
                try: return json.loads(text)
                except: return None
            def fallback_translate(self, *a, **kw): return None
            def raw_translate(self, src, *a, **kw): return src
            def polish_translate(self, src, rough, *a, **kw): return rough

        pl.translator = MockBaseTranslator()

        # Mock ja_translator (3-pass) - chạy đúng
        class MockJaTranslator:
            def literal_translate(self, source_lines, glossary_terms=None):
                return [f"literal {s}" for s in source_lines]
            def polish_translate(self, source_lines, rough_translations,
                                 glossary_terms=None, context_summary=None):
                return [f"polished {s}" for s in source_lines]
            def qa_review(self, source_lines, translations,
                          glossary_terms=None, context_summary=None):
                # Mỗi dòng ok=True
                return [{"index": i+1, "ok": True, "issue": "", "suggestion": ""}
                        for i in range(len(source_lines))]
            def apply_qa_results(self, translations, qa_results):
                return translations

        pl.ja_translator = MockJaTranslator()
        # Mock Worker A context analyzer để không crash
        class MockContextAnalyzer:
            context_window_size = 18
            def analyze_project(self, pid, db):
                return {"total": 0, "completed": 0, "failed": 0, "skipped": 0}
        pl.ja_context_analyzer = MockContextAnalyzer()

        pl.run(pid)

        items = db.get_all_items(pid)
        translated = [it for it in items if it.get('translated_text')]
        self.assert_eq(len(translated), 6, f"JA pipeline phải dịch đủ 6 items, got {len(translated)}")
        # Verify polish output được dùng
        self.assert_in("polished", translated[0]['translated_text'])

        os.remove(test_srt)
        core.database.Database.__init__ = orig
        del pl, db
        gc.collect()
    finally:
        if os.path.exists(db_path):
            try: os.remove(db_path)
            except PermissionError: pass


# ============= TESTS FOR TRANSCIBER =============
@T.test("transcriber: _estimate_min_vram_mb returns sane values")
def _(self):
    from core.transcriber import WhisperTranscriber
    t = WhisperTranscriber.__new__(WhisperTranscriber)
    # Models nhỏ phải < GPU 4GB
    self.assert_true(t._estimate_min_vram_mb('tiny') <= 500)
    self.assert_true(t._estimate_min_vram_mb('small') <= 1500)
    self.assert_true(t._estimate_min_vram_mb('medium') <= 3000)
    # large-v3-turbo cần ~3.5GB - hợp lý cho GPU 4GB (có thể OOM)
    self.assert_true(t._estimate_min_vram_mb('large-v3-turbo') >= 3000)
    self.assert_true(t._estimate_min_vram_mb('large-v3-turbo') <= 5000)


@T.test("transcriber: _load_model_with_fallback catches DLL error and falls back to CPU")
def _(self):
    """Khi WhisperModel raise cublas/cudnn/DLL error → fallback CPU int8."""
    from core import transcriber as tr

    # Patch WhisperModel để raise lỗi cublas khi gọi cuda lần đầu
    class FakeWhisperModel:
        call_count = 0
        def __init__(self, model_size, device, compute_type):
            type(self).call_count += 1
            if device == 'cuda' and compute_type != 'int8':
                raise RuntimeError(
                    "Failed to load library cublas64_12.dll. Error code: 126"
                )
            # Nếu đến đây tức là đã fallback OK
            self.model_size = model_size
            self.device = device
            self.compute_type = compute_type
            self.ok = True

    orig = tr.WhisperModel
    tr.WhisperModel = FakeWhisperModel
    try:
        t = tr.WhisperTranscriber({
            'model_size': 'large-v3-turbo',
            'device': 'cuda',
            'compute_type': 'float16',
            'allow_cpu_fallback': True,
        })
        # Sau init, phải có model ok (đã fallback)
        self.assert_true(hasattr(t, 'model'))
        self.assert_true(t.model.ok)
        # Phải fallback về CPU int8
        self.assert_eq(t.model.device, 'cpu')
        self.assert_eq(t.model.compute_type, 'int8')
    finally:
        tr.WhisperModel = orig


@T.test("transcriber: GPU VRAM < model yêu cầu → fallback int8 (không crash)")
def _(self):
    """GPU 2GB thì dù large-v3-turbo phải được fallback sang int8, không float16."""
    from core import transcriber as tr

    # Mock _detect_vram_mb trả 2000 (2GB)
    original_detect = tr.WhisperTranscriber._detect_vram_mb
    tr.WhisperTranscriber._detect_vram_mb = lambda self: 2000

    captured = []
    class FakeWhisperModel:
        def __init__(self, model_size, device, compute_type):
            captured.append((device, compute_type))
            self.ok = True
            self.device = device
            self.compute_type = compute_type
            self.model_size = model_size

    orig = tr.WhisperModel
    tr.WhisperModel = FakeWhisperModel
    try:
        t = tr.WhisperTranscriber({
            'model_size': 'large-v3-turbo',
            'device': 'cuda',
            'compute_type': 'float16',
            'allow_cpu_fallback': True,
        })
        # Lần gọi đầu phải là cuda int8 (không phải float16)
        self.assert_eq(captured[0], ('cuda', 'int8'),
                       "VRAM thấp phải thử int8 trước, không float16")
    finally:
        tr.WhisperModel = orig
        tr.WhisperTranscriber._detect_vram_mb = original_detect


@T.test("transcriber: disable cpu fallback → raise lỗi thay vì fallback")
def _(self):
    from core import transcriber as tr

    class FakeWhisperModel:
        def __init__(self, model_size, device, compute_type):
            raise RuntimeError("Failed to load library cublas64_12.dll")

    orig = tr.WhisperModel
    tr.WhisperModel = FakeWhisperModel
    try:
        threw = False
        try:
            tr.WhisperTranscriber({
                'model_size': 'large-v3-turbo',
                'device': 'cuda',
                'compute_type': 'float16',
                'allow_cpu_fallback': False,  # TẮT fallback
            })
        except RuntimeError as e:
            threw = True
            self.assert_in("cublas", str(e))
        self.assert_true(threw, "Khi tắt fallback phải raise lỗi, không tự ý fallback")
    finally:
        tr.WhisperModel = orig


@T.test("transcriber: NVIDIA path discovery works in real env")
def _(self):
    """Verify code tự tìm nvidia DLL từ site-packages."""
    # Mô phỏng lại logic ở đầu file transcriber.py
    import site
    nvidia_paths = set()
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        if not sp:
            continue
        for sub in ("cublas", "cuda_nvrtc", "cudnn", "cuda_runtime"):
            p = os.path.join(sp, "nvidia", sub, "bin")
            if os.path.exists(p):
                nvidia_paths.add(p)
    # Trên máy này đã cài nvidia-cublas-cu12 rồi → tìm thấy ít nhất 1 path
    self.assert_true(len(nvidia_paths) >= 1,
                     f"Phải tìm thấy ít nhất 1 nvidia DLL path, got {nvidia_paths}")


@T.test("transcriber: _check_model_cache detect cached/uncached correctly")
def _(self):
    """Verify logic detect model đã trong HF cache chưa (resolve symlink)."""
    from core.transcriber import WhisperTranscriber
    t = WhisperTranscriber.__new__(WhisperTranscriber)

    # Model không tồn tại → cached=False
    info = t._check_model_cache('nonexistent-model-xyz')
    self.assert_true(not info['cached'])
    self.assert_eq(info['has_model_bin'], False)

    # large-v3-turbo: trên máy này cache có file ~1.5GB
    info_turbo = t._check_model_cache('large-v3-turbo')
    self.assert_true(info_turbo['has_model_bin'],
                     f"large-v3-turbo phải có model.bin trong cache, got {info_turbo}")
    # has_model_bin=True thì cached phải True
    self.assert_true(info_turbo['cached'])


# ============= RUN =============
if __name__ == "__main__":
    ok = T.run_all()
    sys.exit(0 if ok else 1)
