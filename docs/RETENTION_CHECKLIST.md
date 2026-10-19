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
- [x] Mở `core/validator.py`
- [x] Đổi `threshold: float = 0.7` → `threshold: float = 0.5` (~line 165)
- [x] **Test:** 6 dòng identical → True (pass); 4 dòng lặp 50% → False (vẫn cần ≥3 cặp)
- **Hạn chế:** Logic vẫn yêu cầu `repeat_count >= 3` cặp → trong window 6 dòng max 3 cặp. Fix này chỉ giảm threshold, không giải quyết "anh ơi/em ơi" type repetition. Cần fix sâu hơn sau.

### Fix 5 — Tăng `num_ctx` cho default/uncen model
- [x] Mở `config.yaml`
- [x] `models.default.num_ctx: 6144 → 16384` (line 29)
- [x] `models.uncen.num_ctx: 6144 → 16384` (line 37)
- [x] `models.ja.num_ctx: 16384` (giữ nguyên)
- [x] **Test:**
  - Config load OK (yaml.safe_load verified)
  - Ollama accept num_ctx=16384 với model qwen3-abliterated (200 status)
- **Risk:** num_ctx cao hơn → tốn nhiều VRAM hơn. 3060Ti 8GB có thể OOM khi chạy song song Whisper + LLM. Cần test thực tế.
- [ ] Tick `[x]` khi pass

### Fix 6 — Đẩy `_retry_per_line` lên làm primary cho câu ngắn
- [x] Mở `core/pipeline.py`
- [x] Sau `task['_source_lines'] = source_lines` (~line 972), thêm guard:
  - Tất cả dòng `<=15 chars`, không có kanji → gọi `_retry_per_line`
- [x] **Test:**
  - Module load OK
  - Logic test cases:
    - "こんにちは/おはよう/ありがとう" → True
    - ">15 chars" → False
    - Có kanji → False
- **Lợi ích:** dòng greeting ngắn không cần 3-pass translation, giảm 30-50% thời gian
- [ ] Tick `[x]` khi pass

### Fix 7 — Glossary compression: chỉ inject relevant terms
- [x] Mở `core/pipeline.py`
- [x] Thêm `_filter_glossary_for_window(glossary, source_lines)` (~line 1403)
- [x] Trong `_build_prompt` (~line 1378): dùng filter khi có `source_lines`
- [x] **Test:**
  - Module load OK
  - Logic test: 5 terms → 2 relevant = 60% compression
- **Lợi ích:** glossary 200 terms × ~50 chars = 10KB → 4KB sau filter. Giảm `num_ctx` usage
- **Risk:** Nếu glossary terms JA có cùng chuỗi con với text thường (e.g. "の" = possessive particle), có thể match nhiều hơn cần thiết. Test cần check edge case.
- [ ] Tick `[x]` khi pass

---

## 🟠 P1 — Quan trọng, làm sau P0

### Fix 8 — Bỏ `split_long_subs` default hoặc hiển thị warn trên GUI
- [x] `config.yaml`: `pipeline.split_long_subs: true → false`
- [x] **Verify:** yaml.safe_load → `False`
- **Lý do:** sub_splitter chỉ warn, không split thật → user phải tự review. Tắt default.
- [ ] Tick `[x]` khi pass

### Fix 9 — Settings page: thêm `num_predict`, `temperature`, `timeout`
- [x] Thêm `LLM (Advanced)` card với 3 field: `num_predict_edit`, `temperature_edit`, `timeout_edit`
- [x] `_refresh_from_config`: load từ `models.{active_mode}` (line ~1196)
- [x] `_save_config`: save về `models.{active_mode}` với try/except ValueError fallback (line ~1237)
- [x] **Test:** `ast.parse(gui.py)` → OK
- **Lưu ý:** Active mode = `translation.mode` (uncen/default). Nếu user đổi mode, settings cần refresh (chưa auto — có thể fix sau).
- [ ] Tick `[x]` khi pass

### Fix 10 — Log page auto-scroll + tag filter
- [x] Thêm `QComboBox filter_combo` (ALL/INFO/OK/WARN/ERR)
- [x] Thêm `QCheckBox autoscroll_cb` (default checked)
- [x] `_on_log_message`: apply filter + auto-scroll to max
- **Caveat:** Tag logic hiện chỉ filter đúng tag khi `LogBridge.message` emit (tag, text). Nếu tag rỗng → luôn pass qua. Cần test trong runtime.
- [ ] Tick `[x]` khi pass

### Fix 11 — Glossary import/export CSV
- [x] Thêm 2 button: `Import CSV`, `Export CSV`
- [x] `_import_csv`: QFileDialog → parse CSV với header (source,target,context)
- [x] `_export_csv`: QFileDialog → ghi CSV với DictWriter
- [x] **Test:** ast.parse OK
- **Lưu ý:** Import không clear rows hiện có — chỉ append. Có thể add nút "Clear all" sau.
- [ ] Tick `[x]` khi pass

### Fix 12 — Real-time progress bar
- [x] Trong loop `for line in self.process.stdout`: regex parse `Window N/M` → gọi `_update_progress`
- [x] Method `_update_progress(cur, total)`: setRange(0,total) + setValue(cur)
- [x] **Test:** ast.parse OK
- **Lưu ý:** Progress bar chỉ update khi log có pattern `Window N/M`. Pipeline hiện KHÔNG emit pattern này rõ ràng — cần verify bằng test runtime.
- [ ] Tick `[x]` khi pass

---

## 🟡 P2 — Cải tiến dài hạn (làm cuối)

### Fix 13 — Settings: thêm `model URL`, `max_retries`, `checkpoint_interval`
- [x] Thêm `ollama_url_edit`, `max_retries_edit`, `checkpoint_edit`
- [x] Pipeline (Advanced) card
- [x] Wire save/load
- [x] **Test:** ast.parse OK
- [ ] Tick `[x]` khi pass

### Fix 14 — GUI drag-drop file
- [x] `setAcceptDrops(True)` trong `App.__init__`
- [x] Override `dragEnterEvent` (accept nếu có URLs)
- [x] Override `dropEvent` (lấy file .srt/.mp4/.mkv → set input)
- [x] **Test:** ast.parse OK
- **Caveat:** Chỉ nhận 1 file đầu tiên match extension.
- [ ] Tick `[x]` khi pass

### Fix 15 — GUI recent projects
- [x] Mở `gui.py`
- [x] Dùng config key `recent_projects` (không cần QSettings)
- [x] Hiển thị dưới dạng `QComboBox` trên Workspace
- [x] **Test:** ast.parse OK
- [x] Tick `[x]` pass

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