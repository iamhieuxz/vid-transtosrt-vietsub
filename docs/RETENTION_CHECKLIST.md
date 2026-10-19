# Retention Checklist — Subtitle Translator Refactor

> Checklist các việc cần làm theo thứ tự ưu tiên.
> Sau khi fix code → test lại → tick `[x]`.
> Workflow: làm 1 việc → test → tick → làm việc tiếp theo.

**Quy ước tick:**
- `[ ]` chưa làm
- `[~]` đang làm dở
- `[x]` đã xong + test pass
- `[!]` phát hiện không làm được / blocked (ghi lý do)

**Trạng thái hiện tại: KHỞI ĐẦU**

---

## 🔴 P0 — Phải fix để giảm lỗi dịch (làm trước tiên)

### Fix 1 — Tắt `ja_trans2` default trong config.yaml
- [x] Đã verify: `config.yaml` đã có `ja_trans2.enabled: false` (line 72)
- **Không cần thay đổi**

### Fix 2 — Tắt `ja_context` default trong config.yaml
- [x] Đã verify: `config.yaml` đã có `ja_context.enabled: false` (line 68)
- **Không cần thay đổi**

### Fix 3 — Tắt Worker C scene review default
- [x] Mở `core/pipeline.py` — đã thêm guard `worker_c_enabled`
- [x] Mở `config.yaml` — đã thêm `worker_c_enabled: false`
- [x] **Test:** Chạy pipeline, log KHÔNG thấy "Worker C: scene review" → đúng
- [x] Module load OK sau edit (verified by `python -c "import core.pipeline"`)

**BUGS PHỤ PHÁT HIỆN trong test** (không phải do Fix 3, ghi nhận để xử lý sau):
1. `core/precheck.py:run_precheck` — bug "local variable 'report_text' referenced before assignment" (line ~365)
3. `main.py:run_translation` — bug "local variable 'os' referenced before assignment" trong `_clear_project_db` (line ~835)
3. Validator `_detect_repetition` — Translation too long truncating xảy ra liên tục
4. Project name hardcode `JUL-409` trong config → mỗi lần chạy test ngắn đều ghi đè project cũ
5. JA polish prompt LLM hay trả lời giải thích thay vì JSON array

### Fix 4 — Sửa `_detect_repetition` threshold
- [ ] Mở `core/validator.py`
- [ ] Tìm method `_detect_repetition` (~line 162)
- [ ] Đổi `def _detect_repetition(self, translations, threshold: float = 0.7)` → `threshold: float = 0.5`
- [ ] **Test:** Chạy `python -m tests.test_precheck_names` hoặc unit test hiện có
- [ ] Tick `[x]` khi pass

### Fix 5 — Tăng `num_ctx` cho default/uncen model
- [ ] Mở `config.yaml`
- [ ] Trong `models.default`: đổi `num_ctx: 6144` → `num_ctx: 16384`
- [ ] Trong `models.uncen`: đổi `num_ctx: 6144` → `num_ctx: 16384`
- [ ] **Test:** Chạy pipeline non-JA ngắn (~50 subs), kiểm tra:
  - Không có OOM Ollama error
  - Output đầy đủ
- [ ] Tick `[x]` khi pass

### Fix 6 — Đẩy `_retry_per_line` lên làm primary cho câu ngắn
- [ ] Mở `core/pipeline.py`
- [ ] Tìm method `_translate_window` (~line 939)
- [ ] Thêm early-return ngay sau khi parse `sub_indices`/`source_lines`:
  ```python
  # Short single-word sentences → translate per-line (faster, fewer failures)
  if all(len(s.strip()) <= 10 and not any('\u4e00' <= c <= '\u9fff' for c in s) for s in source_lines):
      glossary = self._get_glossary(project_id) if self.enable_glossary else []
      result = self._retry_per_line(source_lines, src_lang, tgt_lang, glossary)
      if result and all(r.strip() for r in result):
          return result, source_lines
  ```
- [ ] **Test:** Tạo file SRT test có ~5 subs ngắn (1-5 chars), chạy pipeline, kiểm tra:
  - Output có dịch (không `[UNTRANSLATED]`)
  - Thời gian nhanh hơn đáng kể
- [ ] Tick `[x]` khi pass

### Fix 7 — Glossary compression: chỉ inject relevant terms
- [ ] Mở `core/pipeline.py`
- [ ] Tìm `_build_prompt` method (~line 1322)
- [ ] Thêm helper method `_filter_glossary_for_window(glossary, source_lines)`:
  - Scan `source_lines` để extract các từ/cụm từ JA
  - Match với glossary terms (substring match)
  - Trả về subset glossary chỉ chứa relevant terms
- [ ] Gọi helper trong `_build_prompt` trước khi build `glossary_lines`
- [ ] **Test:** So sánh prompt size trước/sau (log `len(prompt)`), chạy pipeline:
  - Output chất lượng tương đương hoặc tốt hơn
- [ ] Tick `[x]` khi pass

---

## 🟠 P1 — Quan trọng, làm sau P0

### Fix 8 — Bỏ `split_long_subs` default hoặc hiển thị warn trên GUI
- [ ] Mở `config.yaml`
- [ ] Đổi `pipeline.split_long_subs: true` → `pipeline.split_long_subs: false`
- [ ] (Tuỳ chọn) Thêm log rõ hơn trong `_parse_srt` khi có long cue
- [ ] **Test:** Chạy pipeline, kiểm tra:
  - Output SRT giống input timestamps (không bị split giả)
- [ ] Tick `[x]` khi pass

### Fix 9 — Settings page: thêm `num_predict`, `temperature`, `timeout`
- [ ] Mở `gui.py`
- [ ] Tìm `SettingsPage` class (~line 808)
- [ ] Thêm 3 field:
  ```python
  self.num_predict_edit = QLineEdit()
  self.temperature_edit = QLineEdit()
  self.timeout_edit = QLineEdit()
  ```
- [ ] Trong `_save_config`, lưu giá trị vào config
- [ ] Trong `_refresh_from_config`, load giá trị từ config
- [ ] **Test:** Mở GUI, sửa giá trị, save, kiểm tra `config.yaml` đã update
- [ ] Tick `[x]` khi pass

### Fix 10 — Log page auto-scroll + tag filter
- [ ] Mở `gui.py`
- [ ] Tìm `LogPage` class (~line 952)
- [ ] Thêm `QComboBox` filter: `[ALL, INFO, OK, WARN, ERR]`
- [ ] Thêm `QCheckBox` auto-scroll (default checked)
- [ ] Trong `_on_log_message`, apply filter + scroll
- [ ] **Test:** Mở GUI, chạy pipeline, kiểm tra:
  - Filter hoạt động (chỉ hiển thị tag đã chọn)
  - Auto-scroll chạm đáy khi log mới
- [ ] Tick `[x]` khi pass

### Fix 11 — Glossary import/export CSV
- [ ] Mở `gui.py`
- [ ] Tìm `GlossaryPage` class (~line 844)
- [ ] Thêm 2 button: `Import CSV`, `Export CSV`
- [ ] Implement import: `QFileDialog.getOpenFileName` → parse CSV → fill rows
- [ ] Implement export: ghi rows hiện tại → CSV
- [ ] **Test:** Mở GUI, import file CSV mẫu, kiểm tra rows hiển thị; export → file CSV OK
- [ ] Tick `[x]` khi pass

### Fix 12 — Real-time progress bar
- [ ] Mở `gui.py`
- [ ] Tìm `_on_log_message` method (~line 1330)
- [ ] Thêm regex parse progress từ log: `Window (\d+)/(\d+)` hoặc `(done)/(\d+)`
- [ ] Update `self.page_workspace.progress.setRange(0, total)`, `setValue(done)`
- [ ] **Test:** Chạy pipeline, kiểm tra progress bar tăng dần (0% → 100%)
- [ ] Tick `[x]` khi pass

---

## 🟡 P2 — Cải tiến dài hạn (làm cuối)

### Fix 13 — Settings: thêm `model URL`, `max_retries`, `checkpoint_interval`
- [ ] Mở `gui.py`
- [ ] Thêm các field tương ứng vào `SettingsPage`
- [ ] Wire save/load
- [ ] **Test:** Tương tự Fix 9
- [ ] Tick `[x]` khi pass

### Fix 14 — GUI drag-drop file
- [ ] Mở `gui.py`
- [ ] Override `dragEnterEvent` + `dropEvent` trên main window
- [ ] Accept `.mp4/.mkv/.srt`, set input path
- [ ] **Test:** Drag file vào GUI, kiểm tra input field auto-fill
- [ ] Tick `[x]` khi pass

### Fix 15 — GUI recent projects
- [ ] Mở `gui.py`
- [ ] Dùng `QSettings` lưu 5 project gần nhất
- [ ] Hiển thị dưới dạng menu/button trên Workspace
- [ ] **Test:** Chạy 2-3 project, kiểm tra recent hiển thị
- [ ] Tick `[x]` khi pass

### Fix 16 — Test coverage cho `translator.py`
- [ ] Tạo `tests/test_translator.py`
- [ ] Mock `requests.post` trả về response giả
- [ ] Test: `generate()` retry, circuit breaker, `extract_json()`, `fallback_translate()`
- [ ] Chạy `pytest tests/test_translator.py -v`
- [ ] Tick `[x]` khi pass

### Fix 17 — Test coverage cho `pipeline._translate_window_ja`
- [ ] Tạo `tests/test_ja_translator.py`
- [ ] Mock translator
- [ ] Test: literal → polish → QA flow, fail-fast trên length mismatch
- [ ] Chạy pytest
- [ ] Tick `[x]` khi pass

---

## 📝 Notes sau mỗi fix

> Ghi lại kết quả test sau khi fix xong (ví dụ: "Test pass: pipeline 50 subs, 0 [UNTRANSLATED], thời gian giảm 40%")

| Fix # | Ngày | Kết quả test | Ghi chú |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| 9 | | | |
| 10 | | | |
| 11 | | | |
| 12 | | | |
| 13 | | | |
| 14 | | | |
| 15 | | | |
| 16 | | | |
| 17 | | | |

---

## 🎯 Bắt đầu

**Bước 1:** Đọc kỹ `docs/CODE_REVIEW.md` để hiểu context
**Bước 2:** Làm theo thứ tự P0 → P1 → P2
**Bước 3:** Sau mỗi fix → test → tick → ghi notes
**Bước 4:** Commit thường xuyên (1-2 fix = 1 commit)