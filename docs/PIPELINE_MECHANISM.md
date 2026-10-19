# Subtitle Translator — Cơ chế dịch & Glossary Pipeline

> Tài liệu kỹ thuật chi tiết về luồng dịch phụ đề, cơ chế truyền glossary vào prompt, và các chiến lược fallback/validate trong dự án.

**Phiên bản:** v1.2 (đang refactor trên branch `refactor/code-review-2026-10-18`)
**Module chính:** `core/pipeline.py` (1.285 dòng)
**Mục tiêu:** tài liệu hóa hành vi runtime để dev sau hiểu "khi gặp X, code làm Y".

---

## Mục lục

1. [Tổng quan kiến trúc](#1-tổng-quan-kiến-trúc)
2. [Luồng dịch tổng thể (Pipeline phases)](#2-luồng-dịch-tổng-thể-pipeline-phases)
3. [Windowing — cách chia phụ đề](#3-windowing--cách-chia-phụ-đề)
4. [Cấu trúc prompt gửi tới LLM](#4-cấu-trúc-prompt-gửi-tới-llm)
5. [Glossary — 2 nguồn, 1 đích](#5-glossary--2-nguồn-1-đích)
6. [3 chiến lược dịch (Standard / Fallback / Per-line)](#6-3-chiến-lược-dịch-standard--fallback--per-line)
7. [JA 3-pass Pipeline (literal → polish → QA)](#7-ja-3-pass-pipeline-literal--polish--qa)
8. [JA 2-tier (Worker A → B → C)](#8-ja-2-tier-worker-a--b--c)
9. [Post-processing rule-based](#9-post-processing-rule-based)
10. [Recovery & Dead Letter Queue](#10-recovery--dead-letter-queue)
11. [Validation — chống LLM hallucination](#11-validation--chống-llm-hallucination)
12. [Translation Memory (TM)](#12-translation-memory-tm)
13. [Sơ đồ tổng hợp](#13-sơ-đồ-tổng-hợp)

---

## 1. Tổng quan kiến trúc

```
┌─────────────┐
│  main.py    │  CLI / menu tương tác, pre-check Ollama, gọi pipeline
└──────┬──────┘
       │  config (dict) + project_id
       ▼
┌─────────────────────────────────────────────┐
│  core/pipeline.py — TranslationPipeline     │
│  ┌────────────────────────────────────────┐ │
│  │ Phase 1: Pre-processing                │ │
│  │  • Parse SRT → subtitle_items DB       │ │
│  │  • Chia windows → windows DB            │ │
│  │  • Worker A (JA only): context summary │ │
│  └────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────┐ │
│  │ Phase 2: Main translation               │ │
│  │  • Translation memory lookup (TM hit)  │ │
│  │  • Standard JSON pass / JA 3-pass      │ │
│  │  • Fallback (no-JSON, chunked)         │ │
│  │  • Per-line retry (last resort)        │ │
│  │  • Commit → DB + TM cache              │ │
│  └────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────┐ │
│  │ Phase 3: Post-processing               │ │
│  │  • ja_trans2 (batch refine, JA only)   │ │
│  │  • Worker C (scene review, JA only)    │ │
│  │  • RuleBasedPostProcessor (regex/dict) │ │
│  │  • Export SRT (atomic write)           │ │
│  └────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────┐
│  core/translator.py — TranslatorService     │
│  • HTTP client Ollama (POST /api/generate)  │
│  • Circuit breaker (5 lỗi / 60s cooldown)   │
│  • Warmup model khi pipeline start          │
│  • JSON extraction (json_repair → greedy)   │
└─────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────┐
│  core/database.py — SQLite WAL mode         │
│  Tables:                                    │
│   projects, subtitle_items, windows,        │
│   translation_memory, dead_letter_queue,    │
│   glossary, window_contexts                 │
└─────────────────────────────────────────────┘
```

**Nguyên tắc chính:**
- Tất cả state lưu DB → resume project sau crash dễ dàng.
- Mỗi window là 1 task độc lập → claim/process/commit theo cơ chế "claim" (status `pending → processing → completed/dead_letter`).
- LLM call chỉ 1 nơi: `TranslatorService.generate(prompt)` → HTTP `POST /api/generate`.

---

## 2. Luồng dịch tổng thể (Pipeline phases)

### Phase 1 — Pre-processing

```
Input: video_path | srt_path
   │
   ▼
1. WhisperTranscriber.transcribe()  (nếu là video)
   • faster-whisper, model_size từ config.whisper
   • Output: <project>_whisper.srt trong folder project
   • VAD filter, beam_size, noise filter
   │
   ▼
2. WhisperPreCheck.auto_fix_simple()  (file SRT gốc)
   • Strip BOM, zero-width chars
   • Chuẩn hoá newline
   │
   ▼
3. _parse_srt()  (pipeline.py:476)
   • pysrt.open() → list items
   • Lưu vào subtitle_items DB
   • Giữ newline giữa các dòng trong cùng 1 sub
   │
   ▼
4. _create_windows()  (pipeline.py:490)
   • Chia thành các window N dòng (N từ config.window.size)
   • Lưu vào windows DB
   │
   ▼
5. recover_stuck_tasks()  (database.py:283)
   • Reset task kẹt quá heartbeat_timeout (mặc định 600s)
   • Tăng retry_count để tránh loop vô hạn
   │
   ▼
6. (JA only) Worker A — JaContextAnalyzer.analyze_project()
   • Chia SRT thành context-window 18 dòng
   • Gọi LLM 1 lần/window để trích xuất:
     speakers, pronouns_map, tone, setting, summary
   • Lưu vào window_contexts DB
   • Cache: skip nếu context-window đã completed
```

### Phase 2 — Main translation

```
Loop (sequential / parallel):
   │
   ▼
1. claim_task(project_id, max_retries)
   • Lấy 1 window status IN ('pending', 'failed') AND retry_count < max_retries
   • Set status = 'processing', ghi processing_started_at
   │
   ▼
2. _process_single_task(task)
   │
   ├─► Translation Memory lookup
   │     • get_translation_memory(source) cho từng dòng
   │     • Nếu 100% hit → return luôn, không gọi LLM
   │
   ├─► (JA only) ja_translator.literal_translate()  (Pass 1)
   │     • Dịch thô, giữ keigo/particles/onomatopoeia
   │     • Fail-fast nếu length mismatch
   │
   ├─► (JA only) ja_translator.polish_translate()  (Pass 2, có inject context)
   │     • Trau chuốt xưng hô theo pronouns_map từ Worker A
   │
   ├─► (JA only) ja_translator.qa_review()  (Pass 3)
   │     • LLM review translation drift, gợi ý sửa
   │     • Áp suggestion cho dòng ok=false
   │
   ├─► (Non-JA) Standard JSON pass
   │     • _build_prompt() → gọi LLM → extract_json() → validate
   │
   ├─► (Fallback) fallback_translate()  (Pass 2 non-JA)
   │     • Chunk 3 dòng, prompt đơn giản không JSON
   │     • Mỗi dòng độc lập
   │
   ├─► (Last resort) _retry_per_line()
   │     • Từng dòng 1, prompt ngắn nhất
   │     • Tối đa 3 lần/dòng
   │
   ├─► RuleProcessor.process_window()
   │     • Character name canonicalization (Ayunee → Ayune)
   │     • Anti-artifact patterns từ config.yaml
   │
   ├─► Validator.validate_window_content()
   │     • Length match, placeholder, repetition, name consistency
   │     • FAIL → vẫn commit (đỡ mất data, log warning)
   │
   └─► DB.commit_window() + save_translation_memory()
         • Update subtitle_items.translated_text
         • Mark window status = 'completed'
         • Cache vào translation_memory (confidence=0.95)

   │ retry_count >= max_retries
   ▼
   mark_task_dead() → chuyển vào dead_letter_queue
```

### Phase 3 — Post-processing

```
Sau khi tất cả window done:
   │
   ▼
1. _recover_dead_letters()  (3-pass recovery cho window fail hoàn toàn)
   │
   ▼
2. _export_to_folder()  (lần 1 — luôn có output dù pipeline lỗi)
   │
   ▼
3. (JA only) ja_translator2.refine_project()  (batch refine)
   • Step 1: Translate các dòng NULL (NULL → VI)
   • Step 2: Refine các dòng đã dịch (polish nhẹ, model nhỏ 8B)
   │
   ▼
4. _export_to_folder()  (lần 2 — ghi đè sau refine)
   │
   ▼
5. (JA only) Worker C — _worker_c_scene_review()
   • Gom 36 dòng = 1 scene
   • LLM QA review cả scene với context
   • Auto-fix các dòng ok=false (validate trước khi update)
   │
   ▼
6. _export_to_folder()  (lần 3 — sau Worker C)
   │
   ▼
7. (JA only) RuleBasedPostProcessor.process_project()
   • Regex/Dict fix: Sino-VN sai, romaji leak, particles, English leak
   • Không gọi LLM, tốc độ ~1000 dòng/s
   │
   ▼
8. _export_to_folder()  (lần 4 — cuối cùng)
   │
   ▼
9. update_project_status() — 'completed' / 'partial' / 'completed_with_errors'
```

---

## 3. Windowing — cách chia phụ đề

**Khái niệm:** thay vì gọi LLM 3000 lần (mỗi sub 1 lần), ta gom `window.size` dòng (mặc định 6) thành 1 "window" rồi gọi LLM 1 lần → tiết kiệm 6× lượng HTTP call.

**Cấu hình `config.yaml`:**

```yaml
window:
  size: 6          # số dòng hiện tại cần dịch
  history: 12      # số dòng đã dịch trước (context trước)
  future: 4        # số dòng tương lai (context sau)
```

**Per-language preset (`main.py:WINDOW_PRESETS`):**

| Source | size | history | future |
|--------|------|---------|--------|
| `ja`   | 6    | 12      | 4      |
| `ko`   | 8    | 8       | 2      |
| `zh`   | 10   | 12      | 4      |
| `en`   | 10   | 10      | 3      |

**Lý do JA dùng window nhỏ hơn:** JA có nhiều biến thể keigo/xưng hô, cần context ngắn hơn để LLM focus vào 1 cuộc thoại. ZH/EN đã đủ rõ nghĩa nên window to hơn để giảm HTTP call.

**Tại sao có `history` và `future`?** Cho LLM thấy 12 dòng đã dịch trước + 4 dòng tương lai để:
- Duy trì xưng hô nhất quán (anh/em/ông/bà)
- Tránh dịch lặp (dòng 5 nhắc lại dòng 3)
- Dịch câu cắt giữa chừng đúng ngữ cảnh

→ Build trong `_get_context()` (pipeline.py:998), 3 phần: `history`, `future`, `context_block` (từ Worker A nếu JA).

---

## 4. Cấu trúc prompt gửi tới LLM

**Hàm:** `_build_prompt()` (pipeline.py:1117)

**Template cho target = Vietnamese** (khoảng 100 dòng, đặc thù cho JA→VI):

```
[SYSTEM]
Bạn là dịch giả phụ đề chuyên nghiệp, chuyên dịch từ Japanese sang Vietnamese.
Bạn thông thạo văn học, ngôn ngữ, văn hóa và tiếng lóng Nhật Bản.

[GLOSSARY — chỉ xuất hiện nếu có terms]
THUAT NGU BAT BUOC GIU NGUYEN:
- おちんちん -> dương vật (context: từ thân mật, trẻ em nói hoặc người lớn dùng khi đùa)
- ちんちん -> dương vật (context: từ thân mật, tương đương おちんちん)
- ペニス -> dương vật (context: từ y khoa katakana)
...

[CONTEXT — chỉ khi JA 2-tier bật và Worker A có data]
THONG TIN NHAN VAT (tu Worker A - phan tich ngu canh):
--- Context phan 1 ---
- Nhan vat: Takao, Kachiko, Ayumi
- Xung ho (giua cac nhan vat):
  Takao->Kachiko: toi goi em
  Kachiko->Takao: em goi anh
- Giong noi: than mat (anh/em)
- Boi canh: phong ngu ban dem
- Tom tat: Takao va Kachiko dang than mat chuyen tro

CAC DONG TRUOC (da dich xong):
[123] Anh yeu em
[124] Em cung yeu anh
[125] ...

CAC DONG HIEN TAI (can dich):
[126] Xin loi, em khong the gap anh hom nay
[127] ...
[131] ...

CAC DONG TIEP THEO (nguon goc):
[132] ...
[135] ...

YEU CAU NGHIEM NGAT:
- Dich CHINH XAC 6 dong trong phan "HIEN TAI"
- MOI dong phai dich KHAC NHAU, khong lap lai noi dung
... (rất nhiều rules JA-specific: keigo, Whisper artifacts, tên nhân vật, etc.)
- Dung ngay sau dau ] cua JSON

### Output:
```

**Thứ tự ưu tiên trong prompt (ảnh hưởng LLM attention):**

1. System role (định vị model là dịch giả)
2. Glossary block (nếu có)
3. Context block (JA 2-tier, nếu có)
4. History (đã dịch → đảm bảo tính nhất quán)
5. Current lines (cần dịch → nhiệm vụ chính)
6. Future lines (sắp tới → giúp dịch câu cắt)
7. Rules (output format + chính tả + tên nhân vật)

> **Insight:** LLM thường chú ý đầu và cuối prompt hơn. Glossary đặt ở đầu (sau system) để LLM "thấy" trước khi dịch. Output instructions đặt cuối cùng để format JSON chuẩn.

---

## 5. Glossary — 2 nguồn, 1 đích

**Mục đích:** ép LLM dùng đúng thuật ngữ (đặc biệt với phim JAV, anime, phim chuyên ngành) thay vì "sáng tạo".

### 5.1. Nguồn glossary

#### Nguồn 1: `config.yaml` (global, dùng cho MỌI project)

```yaml
glossary:
  - source: おちんちん
    target: dương vật
    context: từ thân mật, trẻ em nói hoặc người lớn dùng khi đùa
  - source: ちんちん
    target: dương vật
    context: từ thân mật, tương đương おちんちん
  - source: ペニス
    target: dương vật
    context: từ y khoa katakana
  # ... hàng trăm terms
```

**Đặc điểm:**
- Load 1 lần khi `TranslationPipeline.__init__()` → lưu `self.glossary_terms`
- Tối đa (~hàng nghìn terms). Prompt size giới hạn bởi `num_ctx` (mặc định 8192 cho JA, 6144 cho default).
- `enable_glossary: true/false` (trong `pipeline:` block) bật/tắt toàn bộ.

#### Nguồn 2: Database (per-project, thêm tay qua menu [6])

```python
db.add_glossary_term(
    project_id,
    source='ガラムついてあげるよ',
    target='Anh sẽ làm em sướng',
    hint='Whisper artifact: không giữ romaji'
)
```

**Đặc điểm:**
- Per-project, không leak sang project khác
- User thêm qua menu `[6] Chỉnh sửa Glossary` (hoặc CLI tương lai)
- Có thể add từ DB trực tiếp qua `INSERT INTO glossary ...`

### 5.2. Cách merge 2 nguồn

**Hàm:** `_get_glossary()` (pipeline.py:1097)

```python
all_terms = []

# 1. Config glossary (ưu tiên)
if self.enable_glossary and self.glossary_terms:
    all_terms.extend(self.glossary_terms)

# 2. DB glossary (per-project, thêm vào nếu chưa có)
db_terms = self.db.get_glossary(project_id)
for t in db_terms:
    if not any(existing['source_term'] == t['source_term'] for existing in all_terms):
        all_terms.append({
            'source_term': t['source_term'],
            'target_term': t['target_term'],
            'context_hint': t.get('context_hint', ''),
        })

return all_terms
```

**Nguyên tắc:** Config glossary có **ưu tiên cao hơn**. Nếu cùng `source_term` thì chỉ giữ bản config (bỏ qua DB). Điều này đảm bảo config "toàn cục" (do dev/anh chị maintain) không bị ghi đè bởi term "tạm thời" của project cụ thể.

### 5.3. Format inject vào prompt

**Hàm:** `_build_prompt()` (pipeline.py:1117-1142)

```python
glossary_block = ""
if glossary:
    glossary_lines = []
    for t in glossary:
        term_line = f"- {t['source_term']} -> {t['target_term']}"
        if t.get('context_hint'):
            term_line += f" (context: {t['context_hint']})"
        glossary_lines.append(term_line)
    glossary_block = p['glossary_hdr'] + "\n" + "\n".join(glossary_lines) + "\n"
```

**Output mẫu (target=VI, source=JA):**

```
THUAT NGU BAT BUOC GIU NGUYEN:
- おちんちん -> dương vật (context: từ thân mật, trẻ em nói hoặc người lớn dùng khi đùa)
- ちんちん -> dương vật (context: từ thân mật, tương đương おちんちん)
- ペニス -> dương vật (context: từ y khoa katakana)
- ガラムついてあげるよ -> Anh sẽ làm em sướng (context: Whisper artifact: không giữ romaji)
- アナルティブ -> làm tình qua hậu môn (context: Whisper artifact: không giữ romaji)
...
```

> **Vấn đề tiềm ẩn:** Glossary càng dài → prompt càng dài → num_ctx (context window LLM) càng đầy. Với ~500 terms × ~50 chars/term = ~25K chars, đã chiếm phần lớn `num_ctx=8192` (tính theo tokens, ~2 chars/token → ~12000 tokens). Đây là 1 ràng buộc cần lưu ý khi mở rộng glossary.

### 5.4. Glossary ảnh hưởng tới đâu?

| Vị trí | Glossary có ảnh hưởng? | Ghi chú |
|--------|:---:|---------|
| Standard pass (non-JA) | ✓ | Inject trực tiếp vào prompt |
| JA 3-pass literal | ✓ | Inject vào `_LITERAL_TEMPLATE` qua `glossary_block` |
| JA 3-pass polish | ✓ | Inject vào `_POLISH_TEMPLATE` |
| JA 3-pass QA | ✓ | Inject vào `_QA_TEMPLATE` |
| Fallback (non-JA) | ✓ | Inject vào `fallback_translate` prompt |
| Per-line retry | ✓ | Inject vào prompt đơn giản "Use these terms:" |
| Recovery (3-pass) | ✓ | Inject vào raw + polish pass |
| ja_trans2 batch refine | ✗ | **Chưa inject** (compact prompt, ưu tiên minimal edit) |
| Worker C scene review | ✓ | Inject giống QA pass |
| RuleBasedPostProcessor | ✗ | Rule-based, không qua LLM |

> **Điểm yếu:** `ja_trans2` (batch refine) hiện KHÔNG dùng glossary → nếu LLM "sáng tạo" từ mới ở pass polish, ja_trans2 sẽ không sửa. Có thể là điểm cần cải thiện.

---

## 6. 3 chiến lược dịch (Standard / Fallback / Per-line)

### 6.1. Standard pass (Pass 1, mặc định)

**Code:** `_translate_window()` (pipeline.py:808-822)

**Đặc điểm:**
- Gọi `_build_prompt()` → trả prompt đầy đủ (system + glossary + context + history + current + future + rules)
- Gửi tới LLM (Ollama `POST /api/generate`)
- `extract_json()` parse output (dùng `json_repair` → fallback greedy regex)
- `validator.validate_json_translation(json_data, sub_indices)`:
  - JSON là list
  - Length khớp
  - ID khớp đúng thứ tự
  - Mỗi item có `id` (int) và `text` (str)
- Nếu pass → return translations, source_lines

**Tại sao cần `json_repair`?** LLM 8B thường trả JSON lỗi (thiếu `,` `}` `"`, dấu nháy đơn thay vì đôi, comment thừa). `json_repair` cố sửa trước khi parse strict.

### 6.2. Fallback pass (Pass 2, khi Pass 1 fail)

**Code:** `_translate_window()` (pipeline.py:825-845), gọi `TranslatorService.fallback_translate()`

**Khi nào chạy:**
- Standard pass raise exception (network, JSON parse fail, validation fail)
- Hoặc `extract_json()` trả None

**Đặc điểm:**
- **Bỏ yêu cầu JSON output** → LLM trả plain text
- Chia input thành chunk 3 dòng → mỗi chunk 1 prompt riêng
- Mỗi chunk trả 1 list line, ghép lại theo thứ tự
- Nếu chunk trả thiếu → dịch độc lập từng dòng bằng `_translate_single()`

**Ví dụ prompt:**

```
Translate from Japanese to Vietnamese.
Return only the translations, one per line, no numbering, no extra text.
MANDATORY GLOSSARY:
- おちんちん -> dương vật
- ちんちん -> dương vật
...
Lines to translate:
1. こんにちは
2. お元気ですか
3. また明日
```

**Ưu điểm:** Ít bị LLM "sai format" vì không bắt JSON. Đơn giản hơn → LLM dễ làm theo.
**Nhược điểm:** Mất tính đồng bộ giữa các dòng (mỗi chunk riêng rẽ), không có context giữa các chunk.

### 6.3. Per-line retry (Pass 3, last resort)

**Code:** `_retry_per_line()` (pipeline.py:849)

**Khi nào chạy:**
- Fallback pass fail (validation fail, LLM trả None, exception)

**Đặc điểm:**
- Dịch **TỪNG DÒNG 1** → 1 prompt cực ngắn cho mỗi dòng
- Retry tối đa **3 lần/dòng**, nếu vẫn fail → trả empty string `''`
- Prompt mẫu:

```
Translate to Vietnamese. Output only the translation, no explanation.
Use these terms:
- おちんちん -> dương vật
- ...

Japanese: こんにちは
Vietnamese:
```

**Tại sao cần pass 3?** Khi LLM "bị stuck" (trả cùng 1 output, hoặc sai format) với 1 input dài, việc tách nhỏ ra 1 dòng 1 thường "reset" được hành vi.

### 6.4. Sơ đồ fallback

```
┌───────────────┐
│ Standard pass │  (JSON, full prompt)
│  Pass 1       │
└───────┬───────┘
        │ fail
        ▼
┌───────────────┐
│ Fallback pass │  (plain text, chunk 3)
│  Pass 2       │
└───────┬───────┘
        │ fail (validation fail hoặc LLM trả None)
        ▼
┌───────────────┐
│ Per-line retry│  (1 dòng 1 prompt, 3 lần)
│  Pass 3       │
└───────┬───────┘
        │ fail
        ▼
┌───────────────┐
│ Commit gốc   │  (translated_text = original_text, status='completed')
│  + warn log   │
└───────────────┘
```

> **Quan trọng:** Mọi pass cuối cùng đều **commit kết quả** (dù là original_text rỗng hoặc chính source) — không bao giờ để window "lủng lẳng" → tránh mất data, dù output xấu thì user còn biết đường sửa tay.

---

## 7. JA 3-pass Pipeline (literal → polish → QA)

**Module:** `core/ja_translator.py` (chỉ dùng khi `source_lang` là Japanese).

**Lý do riêng JA:**
- Keigo (敬語) phức tạp: 敬体/常体/謙譲語/尊敬語 — 1 câu có thể có 3-4 cách dịch tuỳ ngữ cảnh
- Onomatopoeia (擬態語) cần dịch nghĩa, không giữ romaji
- Tên riêng hư cấu cần chuẩn hoá (Tenten, Kokoro...)
- Câu chat lược (省略) cần thêm từ

**Hàm:** `_translate_window_ja()` (pipeline.py:895)

### Pass 1: Literal translate

**Mục đích:** Dịch "nguyên văn", giữ keigo/particles/onomatopoeia.

**Prompt template:** `_LITERAL_TEMPLATE` (ja_translator.py:54)

```
[sYSTEM: chuyên gia dịch phụ đề phim JA→VI, phong cách tự nhiên]

Bản dịch "nguyên văn" từng câu, ưu tiên giữ đúng cấu trúc & cảm xúc gốc.
CHỈ DỊCH, KHÔNG trau chuốt.
- Xưng hô: ghi chú rõ dạng keigo gặp phải (trong đầu) để bước sau chọn từ xưng hô VN nhất quán.
- Onomatopoeia: dịch sát nghĩa ra tiếng Việt (xem system).
- Particles cuối câu: truyền tải cảm xúc, không bỏ.

QUY TẮC ĐẦU RA:
- Trả về đúng số dòng bằng số dòng đầu vào, KHÔNG thêm dòng thừa.
- Mỗi dòng chỉ chứa bản dịch tương ứng, không đánh số, không ghi chú.
- Giữ nguyên xuống dòng giữa các dòng đầu vào.

TUYỆT ĐỐI KHÔNG:
- Bỏ sót hoặc gộp nhiều dòng thành một.
- Trả về câu gốc khi chưa dịch.

[GLOSSARY]

Các dòng cần dịch (đã đánh số):
1. こんにちは
2. ...
```

**Đặc điểm:**
- Chunk 8 dòng (self.chunk_size) — đủ nhỏ để LLM focus
- Output dạng plain text (mỗi dòng 1 dòng dịch)
- Fail-fast nếu length mismatch (return None thay vì pad source gốc — tránh "phụ đề Nhật xen Việt")

### Pass 2: Polish (có inject context)

**Mục đích:** Trau chuốt xưng hô, nghe tự nhiên như phim VN.

**Prompt template:** `_POLISH_TEMPLATE` (ja_translator.py:77)

```
[SYSTEM]

Bản dịch "thô" (ROUGH) cần được trau chuốt cho tự nhiên, nhất quán, mượt như phụ đề phim Việt.

ƯU TIÊN TRAU CHUỐT:
1. Nhất quán xưng hô xuyên suốt (dựa trên danh sách các dạng keigo đã thấy ở bước literal).
2. Câu thoại nghe như người Việt nói, không "dịch máy".
3. Giữ đúng sắc thái cảm xúc (ngạc nhiên, giận, mỉa mai...).
4. Nếu ROUGH còn sót romaji/kana onomatopoeia, dịch lại sang tiếng Việt.

[CONTEXT — từ Worker A, nếu có]
NGỮ CẢNH WINDOW (đã phân tích trước, dùng để chuẩn hóa xưng hô):
- Nhân vật xuất hiện: Takao, Kachiko
- Xưng hô chuẩn hóa (DÙNG XUYÊN SUỐT):
  + Takao->Kachiko: nói 'tôi' là 'anh', gọi đối phương là 'em'
  + Kachiko->Takao: nói 'tôi' là 'em', gọi đối phương là 'anh'
- Tone: intimate
- Bối cảnh: phòng ngủ ban đêm

[GLOSSARY]

Dữ liệu (ORIGINAL là câu gốc, ROUGH là bản dịch thô):
ORIGINAL: こんにちは
TRANSLATION: Xin chào

ORIGINAL: また明日
TRANSLATION: Hẹn gặp lại
```

**Đặc điểm:**
- Output **JSON array** (khác Pass 1) → dễ map về input order
- Length mismatch → fallback về Pass 1 (rough)
- Nếu context_summary = None → baseline 3-pass (không có xưng hô mapping)

### Pass 3: QA review

**Mục đích:** Phát hiện "translation drift" — bản dịch lệch nghĩa, bỏ sót thông tin, còn sót romaji.

**Prompt template:** `_QA_TEMPLATE` (ja_translator.py:104)

```
[sYSTEM]

Bạn là reviewer. So sánh từng cặp (ORIGINAL gốc tiếng Nhật, TRANSLATION bản dịch tiếng Việt).
Nhiệm vụ: phát hiện "translation drift".

QUY TẮC:
- Trả về JSON array, mỗi phần tử:
  {"index": <1-based>, "ok": true|false, "issue": "<mô tả ngắn nếu ok=false>", "suggestion": "<bản dịch đề xuất nếu ok=false>"}
- ok=true nếu bản dịch đúng nghĩa, tự nhiên, không có romaji/kana sót.
- ok=false nếu lệch nghĩa, bỏ sót particle, còn romaji/kana onomatopoeia, hoặc quá máy móc.
- Nếu xưng hô trong TRANSLATION không khớp với bản đồ pronouns_map ở context, đánh ok=false.

[CONTEXT]
[GLOSSARY]

Danh sách cần review:
ORIGINAL: こんにちは
TRANSLATION: Xin chào
...
```

**Đặc điểm:**
- `temperature=0.0` (deterministic, có thể lặp lại)
- Áp suggestion cho dòng `ok=false`
- Nếu fail → giữ nguyên polished

### Sơ đồ JA 3-pass

```
Input: 6 dòng JA
   │
   ▼
[Pass 1: literal]  ──► rough[6] (plain text)
   │ fail ──► return None (window fail)
   │
   ▼
[Pass 2: polish + context]  ──► polished[6] (JSON)
   │ fail ──► fallback về rough
   │
   ▼
[Pass 3: qa review]  ──► qa_results[N] (ok/suggestion)
   │ fail ──► giữ polished
   │
   ▼
[apply_qa_results]  ──► final[6]
   │
   ▼
[Validator]  ──► fail ──► fallback về polished → fail nữa ──► fallback rough
   │
   ▼
[RuleProcessor]  ──► translations[6]
   │
   ▼
[DB.commit_window] + [save_to_TM]
```

---

## 8. JA 2-tier (Worker A → B → C)

**Module chính:** `core/ja_context_analyzer.py` (Worker A), `core/ja_translator.py` (Worker B 3-pass), `core/pipeline.py:_worker_c_scene_review()` (Worker C)

**Lý do 2-tier:** Dịch phụ đề dài (3000 dòng/2h) bằng LLM 8B thường bị "trôi" — nhân vật A ở đầu phim gọi "anh" nhưng cuối phim lại gọi "em". 2-tier giải quyết bằng cách:

### Worker A — Pre-processing (chạy 1 LẦN trước pipeline chính)

**Module:** `core/ja_context_analyzer.py`

**Chức năng:** Chia toàn bộ SRT thành "context-window" 18 dòng, gọi LLM 1 lần/window để trích xuất metadata.

**Output (lưu vào `window_contexts` DB):**

```json
{
  "context_window_index": 0,
  "start_pos": 0,
  "end_pos": 17,
  "speakers": ["Takao", "Kachiko", "Ayumi"],
  "pronouns_map": {
    "Takao->Kachiko": {"self": "anh", "other": "em"},
    "Kachiko->Takao": {"self": "em", "other": "anh"},
    "Takao->Ayumi":   {"self": "con trai", "other": "mẹ"}
  },
  "tone": "intimate",
  "setting": "phòng ngủ ban đêm",
  "summary": "Takao và Kachiko than mật chuyện trò, Ayumi (mẹ Takao) gọi điện hỏi thăm"
}
```

**Đặc điểm:**
- **Cache vĩnh viễn** trong DB → resume project skip Worker A hoàn toàn
- Chạy sequential (1 LLM call tại 1 thời điểm) → tránh OOM
- Cùng model với main pipeline → không cần thêm VRAM

### Worker B — Main pipeline (3-pass + inject context)

**Cách inject:** Ở Pass 2 (polish) và Pass 3 (QA), trước khi build prompt, gọi `_get_ja_context_for_window(task, project_id)`:

```python
ctx_idx = start_pos // 18  # 18 = context_window_size
ctx = self.db.get_window_context(project_id, ctx_idx)
if ctx và ctx['status'] == 'completed':
    return {
        "speakers": json.loads(ctx['speakers_json']),
        "pronouns_map": json.loads(ctx['pronouns_map']),
        "tone": ctx['tone'],
        "setting": ctx['setting'],
        "summary": ctx['summary'],
    }
return None  # fallback baseline 3-pass
```

**Khi render vào prompt:**

```
NGỮ CẢNH WINDOW (đã phân tích trước, dùng để chuẩn hóa xưng hô):
- Nhân vật xuất hiện: Takao, Kachiko
- Xưng hô chuẩn hóa (DÙNG XUYÊN SUỐT):
  + Takao->Kachiko: nói 'tôi' là 'anh', gọi đối phương là 'em'
  + Kachiko->Takao: nói 'tôi' là 'em', gọi đối phương là 'anh'
- Tone: intimate
- Bối cảnh: phòng ngủ ban đêm
- Tóm tắt tình huống: Takao và Kachiko than mật chuyện trò...
```

### Worker C — Scene review (chạy SAU pipeline chính)

**Module:** `core/pipeline.py:_worker_c_scene_review()` (line ~1306)

**Chức năng:**
1. Gom 36 dòng đã dịch = 1 "scene"
2. Lấy context của scene từ Worker A (đã lưu DB)
3. Gọi LLM QA review cả scene (dùng `ja_translator.qa_review()`)
4. **Auto-fix** dòng `ok=false` có `suggestion` hợp lệ (validate trước khi update DB)

**Ví dụ output QA:**

```json
[
  {"index": 1, "ok": true},
  {"index": 2, "ok": false, "issue": "xưng hô sai (Kachiko gọi Takao là 'anh' nhưng ghi 'ông')", "suggestion": "...em yêu anh..."},
  {"index": 3, "ok": true}
]
```

**Đặc điểm:**
- Validate suggestion bằng `Validator.validate_window_content()` trước khi ghi DB → tránh Worker C làm hỏng
- Chỉ chạy khi `_is_japanese_source AND ja_translator AND ja_context_analyzer` đều khác None
- Update DB trực tiếp → export lại SRT

### Sơ đồ 2-tier

```
SRT file (3000 dòng)
   │
   ▼
[Worker A: 1 LLM call / 18 dòng]
   │   ~165 calls tổng
   │   Cache vào window_contexts DB
   │
   ▼
[Worker B: 3-pass × 1 call / 6 dòng]
   │   ~500 windows × 3 pass = 1500 calls
   │   Inject context vào Pass 2 & 3
   │
   ▼
[Worker C: 1 LLM call / 36 dòng]
   │   ~85 calls
   │   Auto-fix inconsistent lines
   │
   ▼
[RuleBasedPostProcessor]
   │   No LLM
   │
   ▼
[Export SRT]
```

**Chi phí:** 2-tier chậm hơn baseline ~3.75× (1 phim 2h từ 1h → 3.75h). Đổi lại: keigo + xưng hô nhất quán xuyên suốt phim.

---

## 9. Post-processing rule-based

**Module:** `core/post_processor.py` (chạy SAU Worker C, JA only)

**Mục đích:** Sửa các lỗi phổ biến mà LLM hay mắc, bằng regex/dict — **không gọi LLM**, tốc độ ~1000 dòng/s.

### Rule 1: Sino-Vietnamese sai

**Vấn đề:** LLM dịch theo nghĩa Hán Việt gốc thay vì nghĩa JA hiện đại.

| JA gốc | Hán Việt gốc (sai) | Tiếng Việt đúng |
|--------|---------------------|-----------------|
| 勉強   | cố gắng            | học (học hành) |
| 汽車   | khí xa             | ô tô / xe lửa   |
| 大丈夫 | đại trượng phu     | không sao       |
| 階段   | giai đoạn          | cầu thang       |
| 勉強   | nỗ lực             | học             |

**Code:** Dict `_SINO_FIXES` (post_processor.py:24) + regex `\b(...)\b`

### Rule 2: Romaji leak

**Vấn đề:** LLM để lại romaji trong bản dịch VI (do Whisper nghe ra romaji cho 1 số từ).

**Code:** Dict `_ROMAJI_TO_VI` (post_processor.py:76) — ~50 entries phổ biến: arigatou, sugoi, yabai, kawaii, ...

### Rule 3: Particles chưa dịch

**Vấn đề:** LLM giữ nguyên です/ます/ね/よ/ぞ trong bản dịch.

**Code:** List `_PARTICLES` (post_processor.py:136) — replace từng cặp (wrong, correct).

### Rule 4: English leak

**Vấn đề:** LLM trộn từ tiếng Anh vào bản dịch VI.

**Code:** Dict `_ENGLISH_FIXES` (post_processor.py:174) — okay → được, thank you → cảm ơn, ...

### Helper

- `_fix_whitespace()`: full-width space → ASCII space, collapse multiple spaces
- `_fix_punctuation()`: `!!` → `!`, `??` → `?`, `。。` → `。`, `...` (giữ nguyên)

**Thứ tự áp dụng (process_line):**

```
1. Fix whitespace
2. Sino-VN replacement
3. Romaji replacement
4. Particles replacement
5. English leak replacement
6. Punctuation fix
7. Final whitespace
```

---

## 10. Recovery & Dead Letter Queue

**Mục đề:** Khi 1 window fail (LLM timeout, JSON parse fail, validation fail), không bỏ — chuyển vào dead_letter_queue để xử lý sau.

### Cơ chế retry per-window

**Code:** `_process_single_task()` (pipeline.py:645)

```python
max_window_retries = 5  # retry tối đa 5 lần cho 1 window
for attempt in range(max_window_retries):
    result = self._translate_window(...)
    if result is not None:
        break
    time.sleep(1)
```

**Đặc điểm:**
- Trong cùng 1 `_process_single_task`, retry 5 lần
- Nếu fail hết → commit `source_lines_fallback` (original text) thay vì None
- KHÔNG drop — luôn commit kết quả (dù xấu)

### Stuck task recovery

**Code:** `database.py:recover_stuck_tasks()` (line 283)

**Khi nào chạy:** Mỗi lần pipeline `run()` (đầu phase 2).

**Logic:** UPDATE window có `status='processing' AND processing_started_at < (now - 600s)`:
- Set status = 'pending'
- Tăng `retry_count += 1`
- Ghi `error_message = 'stuck_timeout'`

→ Tránh loop vô hạn nếu task stuck do lỗi runtime (không phải LLM).

### Dead letter queue

**Khi nào:** `max_retries` (từ config, mặc định 5) đạt → window chuyển vào `dead_letter_queue`.

**Code:** `database.py:mark_task_dead()` (line 240)

```sql
INSERT INTO dead_letter_queue (project_id, window_id, window_index, error_message, original_text)
VALUES (?, ?, ?, ?, ?);

UPDATE windows SET status='dead_letter', processing_started_at=NULL WHERE id=?;
```

### Recovery phase (chạy sau main pipeline)

**Code:** `pipeline.py:_recover_dead_letters()` (line 1164)

**Logic 3-pass:**

1. **Pass 1 (raw):** `TranslatorService.raw_translate()` — dịch thô word-by-word, chunk nhỏ
2. **Pass 2 (polish):** `TranslatorService.polish_translate()` — trau chuốt với context trước/sau
3. **Pass 3 (commit):** Validate → lưu DB

**Đặc điểm:**
- Lấy context ±5 dòng quanh dead window để polish
- Validate fail → commit raw translation (không drop)
- Remove dead_letter entry khi thành công

> **Điểm yếu (ghi nhận P1):** `polished = (polished + source_lines)[:len(source_lines)]` ở line 1252 có thể trộn tiếng Nhật gốc vào phụ đề Việt nếu polish length mismatch. Cần fix fail-fast tương tự main pipeline.

---

## 11. Validation — chống LLM hallucination

**Module:** `core/validator.py`

### validate_json_translation(json_data, expected_ids)

Kiểm tra **structure** của JSON trả về từ LLM:

```python
✓ isinstance(json_data, list)
✓ len(json_data) == len(expected_ids)
✓ Mỗi item: dict, có 'id' (int) và 'text' (str)
✓ Thứ tự ID khớp: json_data[0]['id'] == expected_ids[0]
```

**Tại sao quan trọng:** LLM 8B hay trả JSON lỗi (sai thứ tự, thiếu ID, text None). Nếu không validate, commit_window sẽ crash.

### validate_window_content(originals, translations)

Kiểm tra **nội dung** dịch:

```python
✓ len(originals) == len(translations)
✓ Placeholder preservation: %s, {name} phải có trong trans
✓ Không cả 2 cùng rỗng
✓ Source không rỗng thì trans không được rỗng
✓ Truncate nếu trans / orig > 8 (quá dài)
✓ Character name consistency (Ayunee vs Ayune)
✓ Repetition detection (≥3 cặp dòng giống nhau > 70%)
```

### _check_vietnamese_typos(orig, trans)

**Log warning** (không reject) cho các lỗi phổ biến:
- `'vâng' in trans.lower()` → cảnh báo (số 2 → "hai", không phải "vâng")
- Romaji artifacts: `Garam tsuite`, `an ketsu`, `Vagina`, `Anus`, `hentai`, `ecchi`

### _check_character_name_consistency(translations)

**Reject** nếu trong 1 window có cả 2 cách viết cùng tên:

```python
'Ayunee' + 'Ayune' → REJECT (cùng tên nhân vật, phải thống nhất)
```

### _detect_repetition(translations, threshold=0.7)

Phát hiện dòng lặp lại ≥3 cặp với similarity > 0.7 (Jaccard trên set từ).

**Tại sao quan trọng:** LLM hay "stuck" trong 1 loop, trả cùng 1 dòng cho nhiều input.

### Cơ chế "commit dù validation fail"

**Code:** `pipeline.py:725-743` (`_process_single_task`)

```python
if self.validator.validate_window_content(source_lines, translations):
    # Commit normally
    ...
else:
    # Validation FAILED nhưng vẫn commit (với warning)
    # Lý do: model + validator cùng fail → retry vô hạn
    logger.warning("Window X failed validation, committing as-is")
    self.db.commit_window(...)
    return True
```

→ Trade-off: giữ data thay vì mất. User thấy output xấu thì sửa tay (đỡ mất time retry vô ích).

---

## 12. Translation Memory (TM)

**Module:** `core/database.py` (table `translation_memory`)

**Mục đích:** Cache (source, target) để lần sau dịch lại cùng dòng → skip LLM call.

### Schema

```sql
CREATE TABLE translation_memory (
    id INTEGER PRIMARY KEY,
    source_lang TEXT,
    target_lang TEXT,
    source_text TEXT,
    target_text TEXT,
    usage_count INTEGER DEFAULT 1,
    confidence REAL DEFAULT 1.0,
    char_count INTEGER DEFAULT 0,
    domain TEXT DEFAULT 'global',  -- project name hoặc 'global'
    UNIQUE(source_lang, target_lang, source_text, domain)
);
```

**Domain isolation:** cùng 1 source_text nhưng project khác nhau có thể có target khác nhau (vd: "高田" trong phim kinh dị → "Takada", trong phim hài → "Cao Điền").

### Lookup flow

**Code:** `pipeline.py:784-799`

```python
for src in source_lines:
    trans = self.db.get_translation_memory(
        source_lang, target_lang, src,
        domain=project['name'],
        min_char_count=4  # bỏ qua dòng < 4 chars
    )
    if trans is None:
        all_in_tm = False
        break
    tm_hits.append(trans)

if all_in_tm and len(tm_hits) == len(source_lines):
    # Tất cả dòng đều có trong TM → skip LLM
    return tm_hits, source_lines
```

### Save flow

**Code:** `pipeline.py:_save_to_tm()` (line 1156)

```python
for s, t in zip(source_lines, translations):
    if s.strip() and t.strip():
        self.db.save_translation_memory(
            src_lang, tgt_lang, s, t,
            domain=project['name'],
            confidence=0.95,
            min_char_count=4
        )
```

**Conflict resolution:** nếu existing confidence >= new → chỉ tăng usage_count. Ngược lại → UPDATE target_text (overwrite).

### Ưu điểm

- **Resume project** rất nhanh: nếu dòng đã dịch đúng trước đó, không tốn LLM call
- **Glossary thực tế**: TM ghi nhận tất cả cặp dịch thực tế → glossary dần dần tự "học" thuật ngữ của dự án

### Hạn chế (ghi nhận P2)

- Chỉ exact match, không có fuzzy match → 1 ký tự khác = miss
- Domain isolation quá chặt → re-translate phim khác cùng dòng (dù nghĩa giống)
- Không có TTL → DB phình to theo thời gian

---

## 13. Sơ đồ tổng hợp

### End-to-end (JA→VI, 2-tier bật)

```
┌─────────────┐
│  Video .mp4 │
└──────┬──────┘
       │
       ▼
┌─────────────────────────┐
│  WhisperTranscriber     │  faster-whisper large-v3
│  → _whisper.srt         │  VAD, beam_size=8
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│  WhisperPreCheck        │  Strip BOM, zero-width
│  + JA auto-fix dict     │  Fix: かよこ→Kachiko, etc.
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│  Pipeline._parse_srt    │  pysrt.open → DB
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│  Pipeline._create_windows│  size=6, no overlap
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────┐
│  recover_stuck_tasks    │  Reset task > 600s
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  JaContextAnalyzer (Worker A)           │  1 LLM call / 18 dòng
│  → window_contexts DB                    │  Cache: skip if completed
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  Main pipeline loop                     │
│  for each window:                        │
│    ┌──────────────────────────────┐     │
│    │ 1. TM lookup                  │     │  → skip if all hit
│    │ 2. JA 3-pass (literal→polish→qa) │  │  → inject context in pass 2,3
│    │ 3. RuleProcessor               │     │  → canonical names, anti-artifact
│    │ 4. Validator                   │     │  → commit even if fail (warn)
│    │ 5. DB.commit_window            │     │
│    │ 6. save_to_TM                  │     │
│    └──────────────────────────────┘     │
│    on retry_count >= max_retries:        │
│      → mark_task_dead                    │
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  _recover_dead_letters                  │  3-pass: raw→polish→commit
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  _export_to_folder (1)                  │  Atomic write SRT
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  JaTranslator2 (batch refine)           │  Step 1: NULL→VI
│                                         │  Step 2: polish
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  _export_to_folder (2)                  │
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  _worker_c_scene_review (Worker C)       │  1 LLM call / 36 dòng
│  Auto-fix ok=false lines                │
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  _export_to_folder (3)                  │
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  RuleBasedPostProcessor                 │  Regex/dict, no LLM
│  • Sino-VN                              │
│  • Romaji leak                          │
│  • Particles                            │
│  • English leak                         │
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  _export_to_folder (4) — FINAL          │
│  → <project>/<project>.srt              │
│  → <project>/<project>_whisper.srt      │
│  → <project>/<project>.lnk              │
└─────────────────────────────────────────┘
```

### Non-JA (ZH/KO/EN→VI, 2-tier tắt)

```
SRT file (no video)
   │
   ▼
[Pre-check + parse + window]
   │
   ▼
[Main loop]
   for each window:
     1. TM lookup
     2. Standard JSON pass (Pass 1)
     3. Fallback plain-text (Pass 2) — nếu fail
     4. Per-line retry (Pass 3) — nếu fail
     5. RuleProcessor
     6. Validator (commit dù fail)
     7. Commit + save TM
   │
   ▼
[_recover_dead_letters]  (3-pass)
   │
   ▼
[_export_to_folder]
   │
   ▼
[RuleBasedPostProcessor]  (vẫn chạy cho non-JA)
   │
   ▼
[_export_to_folder] — FINAL
```

> **Lưu ý:** Non-JA KHÔNG chạy Worker A, Worker C, ja_trans2, ja_translator. Chỉ có RuleBasedPostProcessor (vì nó là regex thuần, không phụ thuộc ngôn ngữ).

---

## Phụ lục A — Các config keys quan trọng

```yaml
window:
  size: 6            # số dòng / window
  history: 12        # context trước
  future: 4          # context sau

pipeline:
  enable_glossary: true
  max_retries: 5     # 5 lần / window, sau đó → dead_letter
  retry_delay: 2
  num_workers: 1     # 1 = sequential, >1 = parallel
  checkpoint_interval: 20   # xuất SRT mỗi 20 windows
  heartbeat_timeout: 600    # reset task > 600s
  circuit_breaker_threshold: 5
  circuit_breaker_cooldown: 60

models:
  default:           # cho non-JA
    name: qwen3-abliterated:8b-v2
    temperature: 0.05
    repeat_penalty: 1.3
    num_ctx: 6144
    num_predict: 2048
    timeout: 180
  ja:                # cho JA
    name: qwen3-abliterated:8b-v2
    temperature: 0.1
    repeat_penalty: 1.2
    num_ctx: 8192
    num_predict: 2048
    timeout: 180

ja_context:          # Worker A
  enabled: true      # BẬT/TẮT 2-tier
  window_size: 18
  scene_review_size: 36  # Worker C

ja_trans2:           # batch refine (JA only)
  enabled: true
  batch_size: 5

glossary:            # toàn cục, dùng cho mọi project
  - source: おちんちん
    target: dương vật
    context: ...
```

## Phụ lục B — Glossary pass-through map

| Phase | Module | Glossary inject? | Format |
|-------|--------|:---:|--------|
| Parse SRT | `pipeline._parse_srt` | — | — |
| Worker A | `ja_context_analyzer` | ✗ | — |
| Window creation | `pipeline._create_windows` | — | — |
| TM lookup | `database.get_translation_memory` | — | — |
| **Standard pass (non-JA)** | `pipeline._translate_window` → `_build_prompt` | ✓ | Block "THUAT NGU BAT BUOC GIU NGUYEN" |
| **Fallback pass** | `translator.fallback_translate` | ✓ | Block "MANDATORY GLOSSARY" |
| **Per-line retry** | `pipeline._retry_per_line` | ✓ | Block "Use these terms" |
| **JA literal** | `ja_translator.literal_translate` | ✓ | `_render_glossary()` → "MANDATORY GLOSSARY" |
| **JA polish** | `ja_translator.polish_translate` | ✓ | same |
| **JA QA** | `ja_translator.qa_review` | ✓ | same |
| **Recovery raw** | `translator.raw_translate` | ✓ | "MANDATORY GLOSSARY" |
| **Recovery polish** | `translator.polish_translate` | ✗ (plain text) | — |
| **ja_trans2 batch refine** | `ja_translator2.refine` | ✗ (compact) | — |
| **Worker C scene review** | `pipeline._worker_c_scene_review` → `ja_translator.qa_review` | ✓ | same |
| **RuleBasedPostProcessor** | `post_processor.process_line` | ✗ (regex) | — |

> **Cải thiện có thể làm (P2):** Thêm glossary vào `ja_trans2` để LLM biết "đừng sáng tạo term mới".

---

## Phụ lục C — Glossary loading thứ tự

```
┌────────────────────────────────────────────────────┐
│ 1. Config.yaml (glossary: [...])                   │
│    Load 1 lần trong TranslationPipeline.__init__   │
│    Lưu: self.glossary_terms (List[dict])           │
└─────────────────────┬──────────────────────────────┘
                      │
                      ▼
┌────────────────────────────────────────────────────┐
│ 2. Database (glossary table, per-project)          │
│    Query mỗi lần _get_glossary(project_id)        │
│    Filter: source_term chưa có trong self.glossary │
└─────────────────────┬──────────────────────────────┘
                      │
                      ▼
┌────────────────────────────────────────────────────┐
│ 3. _get_glossary() returns merged List[dict]       │
│    Format: {source_term, target_term, context_hint}│
└─────────────────────┬──────────────────────────────┘
                      │
                      ▼
┌────────────────────────────────────────────────────┐
│ 4. _build_prompt() renders thành text block         │
│    "- おちんちん -> dương vật (context: từ thân mật)"│
└─────────────────────┬──────────────────────────────┘
                      │
                      ▼
┌────────────────────────────────────────────────────┐
│ 5. LLM receives prompt + glossary                  │
│    → Output translation với term theo glossary     │
└────────────────────────────────────────────────────┘
```

---

*Tài liệu này được tạo tự động bởi Cursor Agent trong phiên refactor 2026-10-18. Cập nhật khi có thay đổi ở `pipeline.py`.*
