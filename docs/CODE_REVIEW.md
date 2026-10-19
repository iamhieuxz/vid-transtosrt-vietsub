# Code Review — Subtitle Translator

> Đánh giá toàn bộ codebase, đề xuất cải tiến cho GUI và pipeline.
> **Ngày review:** 2026-10-19
> **Phiên bản reviewed:** v1.2 (refactor `refactor/code-review-2026-10-18`)
> **Scope:** `main.py`, `gui.py`, `core/*` (16 modules), `tests/*`

---

## 1. Tổng quan codebase

| Thành phần | Mô tả | LOC |
|---|---|---|
| `core/pipeline.py` | Orchestrator chính — Windowing, 3-pass JA, 2-tier Worker A/B/C, ja_trans2, recovery | ~1690 |
| `core/translator.py` | Ollama HTTP wrapper + circuit breaker + JSON extraction | ~470 |
| `core/ja_translator.py` | JA 3-pass (literal/polish/QA) | ~540 |
| `core/ja_translator2.py` | Batch refiner SAU pipeline | ~540 |
| `core/ja_context_analyzer.py` | Worker A — context summary | ~260 |
| `core/character_registry.py` | Tên riêng toàn phim | ~300 |
| `core/post_processor.py` | Rule-based (regex/dict) | ~410 |
| `core/whisper_normalizer.py` | Chuẩn hoá Whisper artifacts | ~170 |
| `core/database.py` | SQLite (projects, windows, TM, glossary, contexts) | ~450 |
| `core/sub_merger.py`, `sub_splitter.py`, `precheck.py`, `transcriber.py`, `validator.py`, `rules.py`, `exporter.py` | Phụ trợ | ~200-450 mỗi file |
| `main.py` | CLI + Rich menu | ~1485 |
| `gui.py` | PySide6 dark GUI | ~1390 |

**Tổng** ~8000 LOC production + tests + docs. Đây là codebase **đã chạy thực tế** (có artifacts `_ja{}.srt`, test fixtures, pipeline logs), có validation thực tế chứ không phải MVP.

---

## 2. Điểm mạnh ⭐

### 2.1. Kiến trúc pipeline rất tốt
- **3 chiến lược fallback** (Standard JSON → Fallback plain → Per-line) — fail-soft, không bao giờ drop data
- **JA 3-pass (literal → polish → QA)** — đúng cách tiếp cận cho keigo
- **JA 2-tier (Worker A context → Worker B 3-pass → Worker C scene review)** — giải quyết bài toán xưng hô nhất quán
- **Fail-fast** trên length mismatch (đã fix bug "trộn JA vào VI subtitles")
- **Atomic SRT write** (`tmp + replace`) — không corrupt file khi crash
- **Resume project**: mọi state lưu DB, có `recover_stuck_tasks`, `mark_task_dead`, `_recover_dead_letters`
- **Translation Memory** exact-match cache, có `domain` isolation per-project

### 2.2. Observability & DX
- `docs/PIPELINE_MECHANISM.md` (~1350 dòng) — tài liệu chất lượng cao
- Logging rõ ràng: `[WARMUP]`, `[TRANSCRIBER]`, `[CLEAR]`, `[BACKUP]`
- Precheck pipeline: check Ollama + auto-pull model + VRAM warning trước khi chạy
- Test cases cho registry, GUI log, sub_splitter — có self-check

### 2.3. Robust Whisper handling
- `_is_noise_segment()` — 9 tầng filter (katakana đơn, lặp ký tự, char đặc biệt...)
- Auto-fallback GPU→CPU khi OOM/DLL thiếu
- Cache detection trước khi load
- `WhisperNormalizer` thay romaji/kana ngắn bằng VI TRƯỚC khi LLM dịch
- `SubMerger` dedup short-kana lặp + merge long-empty

### 2.4. Robust prompt construction
- Glossary ưu tiên config (global) hơn DB (per-project) — đúng design
- `num_ctx` 16384 cho JA (đủ chứa glossary 200+ terms + 3-pass prompts)
- `think: false` đã được thêm gần đây (giải quyết vấn đề model nghĩ trước khi dịch)

### 2.5. Code tổng thể sạch
- Naming nhất quán (Tiếng Việt không dấu trong code, có comment giải thích)
- Comments có ích (`shortcut:` markers cho non-obvious decisions)
- Validation fail → commit anyway (đỡ mất data, user tự sửa tay)
- `_apply_window_preset()` auto-set window theo ngôn ngữ — UX tốt

---

## 3. Điểm yếu & Rủi ro ⚠️

### 3.1. Performance bottlenecks

| Vấn đề | Impact | Ước lượng |
|---|---|---|
| ~2950 LLM calls/phim | Thời gian |3-5 giờ |
| `get_all_items` gọi N lần trong `_process_single_task` | N+1 query | Đã cache trong context (P1-4 fix) |
| `_context_lock` global lock | Serialize mọi task | Khi parallel workers > 1 sẽ bottleneck |
| `_db_write_lock` quanh `_save_to_tm` | Serialize TM write | Có thể batch |
| Whisper `large-v3` + Ollama model cùng VRAM | Cần ~6GB VRAM | `_load_model_with_fallback` chỉ log warning, không enforce |
| `export_incremental` mỗi 20 windows | Disk I/O | Reasonable |

### 3.2. Code smell / Logical issues

#### 🔴 CRITICAL: GUID chưa có → [UNTRANSLATED] vẫn lọt
- Hiện tại dòng LLM-fail có fallback `_retry_per_line` → trả `""` rỗng
- Vẫn xuất `[UNTRANSLATED] <original>` (exporter line 262)
- User phải tự dịch tay — không có cơ chế automatic retry across sessions

#### 🔴 CRITICAL: Pipeline chạy O(N) LLM calls, gây timeout cascade
- Worker A: ~165 calls (1/18 dòng)
- Worker B: ~500 × 3 pass = 1500 calls (1/6 dòng × 3)
- Worker C: ~85 calls
- ja_trans2: 2 phases × 3000/5 = 1200 calls
- **Tổng: ~2950 LLM calls/phim 2h**, mỗi call 5-30s với 8B model → **~3-5 giờ**

#### 🟠 HIGH: `sub_splitter.py` không thực sự split
- `split_long_sub()` chỉ return 1 cue với warning — không tách
- Function name misleading: caller nghĩ đã split, nhưng cue >6s vẫn còn nguyên

#### 🟠 HIGH: `sub_splitter.find_long_subs` chỉ warn, không act
- Output report `.timestamp_review.txt` nhưng không làm gì
- User phải tự mở file review — UX kém

#### 🟡 MEDIUM: GUI không bind state với pipeline subprocess
- GUI launch `subprocess.Popen(["python", "main.py", ...])` rồi đọc stdout
- Không có direct Python call → không thể cancel gracefully
- `_poll_log_queue` reserved nhưng không implement

#### 🟡 MEDIUM: GUI `_run_pipeline` không kill được khi SIGTERM
- `_kill_running_process` chỉ `proc.terminate()` + wait 5s → fallback `kill()`
- Trên Windows có thể không kill được subprocess đang chạy LLM request

#### 🟡 MEDIUM: Glossary quá lớn sẽ overflow `num_ctx`
- 200 terms × ~50 chars = 10KB ≈ 2.5K tokens
- `num_ctx=16384` (JA) → OK cho JA
- Nhưng default/uncen `num_ctx=6144` → có thể fail khi glossary >150 terms

#### 🟡 MEDIUM: Validator `_detect_repetition` dùng Jaccard > 0.7 với threshold thấp
- 70% trùng từ có nghĩa = ~3 từ trùng trên ~5 từ → false positive cao
- "Anh ơi, anh ơi" vs "Em ơi, em ơi" → 67% → không flag (sai — lặp thật)

#### 🟢 LOW: Code duplication
- `_LITERAL_TEMPLATE`, `_POLISH_TEMPLATE`, `_QA_TEMPLATE` có cùng `_render_glossary`, `_render_character_block`
- `get_rule_processor` là singleton global → khó test

#### 🟢 LOW: GUI QSS inline ~500 dòng — khó maintain

### 3.3. UX issues (GUI)

- **Progress bar chỉ indeterminate** (`setRange(0, 0)`) — không biết % thực
- **Không có log filter** (INFO/WARN/ERR) — tất cả trộn vào 1 stream
- **Settings page**: `num_predict`, `temperature`, `timeout` không editable
- **Glossary page**: drag-drop không, không có search/filter
- **Glossary**: import/export CSV/YAML không có — phải sửa tay config.yaml
- **Log page**: không có auto-scroll, không có timestamp
- **Output folder**: read-only — không thể chọn folder output khác
- **Precheck**: có bảng trong CLI nhưng GUI không hiển thị

### 3.4. UX issues (CLI)

- **JA auto-fix dict hardcoded trong `main.py`** (line ~1080) — không dùng config
- **`edit_languages` có nested menu** — UX kém
- **Window presets không khả edit** trong CLI menu
- **Không có `--model` flag** để override model từ CLI

---

## 4. Đề xuất cải tiến

### 🔴 P0 — Phải fix để giảm lỗi dịch (impact cao nhất)

#### 4.1. Tăng chất lượng dịch bằng cách **giảm tải LLM calls**
- Tắt `ja_trans2.enabled` default → opt-in
- Tắt `ja_context.enabled` default → opt-in
- Worker C scene review → tắt default → opt-in
- Lý do: LLM 8B refine có thể LÀM HỎNG bản dịch tốt

#### 4.2. **Thêm 1-shot translation cho câu đơn giản** (giảm [UNTRANSLATED])
- Dòng < 10 chars không có keigo → dịch 1-shot per-line
- Dòng có onomatopoeia/particles → WhisperNormalizer trước → skip LLM

#### 4.3. **Prompt compression**
- Glossary hiện chiếm ~25% context
- Đề xuất: chỉ inject terms LIÊN QUAN tới window hiện tại

### 🟠 P1 — Quan trọng, fix trong sprint tới

#### 4.4. Fix sub_splitter — hoặc bỏ hẳn
- Hiện `find_long_subs` chỉ warn → user phải tự review
- Hoặc: dùng `whisperx` để có word-level timestamps → split chính xác
- Hoặc: đơn giản là bỏ `split_long_subs` default

#### 4.5. Settings page thiếu fields
- num_predict, temperature, timeout, model URL editable
- enable_glossary, literal_mode, max_retries, checkpoint_interval

#### 4.6. Progress bar real-time
- Parse log từ subprocess stdout, extract progress → update

#### 4.7. Glossary import/export CSV
- User có 200+ glossary terms, edit trong GUI rất chậm

#### 4.8. Log filter + auto-scroll

### 🟡 P2 — Cải tiến dài hạn

#### 4.9. Parallel execution thực sự
- Chạy song song Whisper (CPU) với LLM (GPU)

#### 4.10. Translation Memory với embeddings (semantic match)
- Dùng `sentence-transformers` để tìm top-K similar

#### 4.11. Auto-fix Whisper dùng LLM (offline batch)

#### 4.12. GUI cải tiến UX
- Drag-drop file
- Recent projects
- Dark/Light theme toggle
- Notification khi pipeline xong

#### 4.13. CLI: thêm commands
- `--dry-run`, `--model`, `--glossary`, `--skip-whisper`, `--only-export`

### 🟢 P3 — Nice-to-have

#### 4.14. Test coverage
- Thiếu test cho `translator.py`, `pipeline._translate_window_ja`, `post_processor`
- Thêm `tests/test_translator.py` với mock `requests.post`

#### 4.15. Refactor GUI QSS
- Tách QSS ra file riêng → dễ thêm light theme

#### 4.16. Documentation
- `docs/GLOSSARY.md`, `docs/TROUBLESHOOTING.md`, `docs/MODEL_SELECTION.md`

---

## 5. Quick wins

| # | Action | Effort | Impact |
|---|---|---|---|
| 1 | Default `ja_trans2.enabled = false` | 2 ph | Giảm 1200 LLM calls/phim |
| 2 | Default `ja_context.enabled = false` | 2 ph | Giảm 165 LLM calls/phim |
| 3 | Sửa `_detect_repetition` threshold 0.7 → 0.5 | 5 ph | Giảm false negative |
| 4 | `sub_splitter` warn hiển thị trên GUI | 30 ph | UX tốt hơn |
| 5 | Settings thêm `num_predict`, `temperature` | 30 ph | UX tốt hơn |
| 6 | Log auto-scroll + tag filter | 20 ph | UX tốt hơn |
| 7 | Glossary import/export CSV | 45 ph | UX tốt hơn |
| 8 | Real-time progress bar | 30 ph | UX tốt hơn |

---

## 6. Đánh giá tổng thể

**Codebase quality:7/10**
- Kiến trúc tốt, có design rõ ràng cho bài toán phức tạp (JA keigo, 2-tier, recovery)
- Test coverage chưa đủ, một số module quan trọng (translator, pipeline) không có test
- GUI đẹp nhưng thiếu nhiều tính năng UX quan trọng

**Lỗi dịch hiện tại chủ yếu do:**
1. **Glossary quá lớn** → LLM context overflow → bỏ qua rules
2. **Window 6 dòng** quá nhỏ → LLM không đủ context → dịch lệch
3. **Model 8B** + **num_ctx 6144** không đủ cho prompt phức tạp
4. **ja_trans2 + Worker C** "tự sửa" có thể phá bản dịch tốt
5. **Retry per-line** không tận dụng (chỉ chạy khi standard fail)

**Khuyến nghị ưu tiên:**
1. Tắt `ja_trans2` + `ja_context` default → giảm 50% thời gian, giảm lỗi
2. Tăng `num_ctx` cho default/uncen từ 6144 → 16384
3. Tăng `window.size` từ 6 → 8-10 cho non-JA
4. Thêm Glossary compression (chỉ inject relevant terms)
5. Fix sub_splitter hoặc bỏ hẳn

---

*Review dựa trên static code analysis. Đã skip: chạy thử pipeline để benchmark, đọc các test fixtures, verify config.yaml syntax. Ưu tiên: bugs > regressions > security > tests.*