# Subtitle Translator v1.2

Dịch phụ đề video tự động bằng LLM (Ollama). Hỗ trợ **JA→VI, ZH→VI, KO→VI, EN→VI**.

## Cài đặt

```bash
pip install -r requirements.txt
```

Cần:
- Python 3.10+
- [Ollama](https://ollama.com) chạy nền (`ollama serve`)
- `ffmpeg` (có trong PATH)
- GPU NVIDIA với CUDA (khuyến nghị)

## Cách dùng

### Chế độ tương tác (khuyến nghị)

```bash
python main.py
```

Menu chính:

```
[1] Chọn file Input      — file video (.mp4/.mkv) hoặc file SRT
[2] Chọn folder Output   — folder xuất phụ đề (mặc định cùng folder video)
[3] Tên Project          — tên thư mục output
[4] Chọn ngôn ngữ        — Nguồn → Đích
[5] Chế độ dịch          — default / uncen
[6] Chỉnh sửa Glossary   — thêm từ điển tùy chỉnh
[7] Bắt đầu dịch
[0] Thoát
```

### Chế độ CLI

```bash
python main.py -i video.mp4 -o output/ --source-lang ja --target-lang vi
```

## Luồng xử lý (Pipeline)

```
Input: Video (.mp4) hoặc SRT
  │
  ▼
┌──────────────────────┐
│  Whisper Transcription │ (nếu input là video)
│  faster-whisper       │
│  → <name>_whisper.srt │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Parse SRT           │
│  → subtitle_items DB  │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Tạo Windows         │ (chunk N dòng)
│  → windows DB        │
└──────────┬───────────┘
           │
     ┌─────┴─────┐
     │            │
     ▼            ▼
┌─────────┐  ┌──────────┐
│ Worker A │  │ Main     │
│(tùy chọn)│  │ Pipeline │
│ Context  │  │          │
│ Analysis │  │          │
└────┬────┘  └────┬─────┘
     │             │
     └──────┬──────┘
            │
            ▼
┌──────────────────────┐
│  Dịch từng Window   │ ×65 windows
│                      │
│  ① JSON pass (LLM)  │
│  ② Fallback (nếu #1 │
│     fail)            │
│  ③ Validate          │
│  ④ Commit DB         │
│      ├ Pass  → save  │
│      └ Fail  → save  │
│         (ko retry)   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Recovery Dead       │ (những window
│  Letters             │  hoàn toàn fail)
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  JaTranslator2       │ (JA→VI only)
│  Batch Refine        │
│                      │
│  Bước 1: Translate   │ những dòng NULL
│       (vòng 2)       │ → dịch lại
│                      │
│  Bước 2: Refine      │ những dòng đã dịch
│       (polish)        │ → trau chuốt
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Export SRT          │
│                      │
│  <video>/<project>/  │
│    ├── <name>.srt    │ (đã dịch)
│    ├── <name>_whisper.srt  │ (gốc)
│    └── <name>.lnk    │ (shortcut video)
└──────────────────────┘
```

## Cấu hình (`config.yaml`)

```yaml
whisper:
  model_size: large-v3-turbo  # tiny / base / small / medium / large-v3-turbo
  device: cuda                # cuda / cpu
  language: ja               # ngôn ngữ nguồn

project:
  name: my-project
  source_lang: Japanese
  target_lang: Vietnamese
  input_srt: E:/video.mp4    # video hoặc SRT
  output_srt: E:/output/     # folder output

window:
  size: 6                   # dòng mỗi window
  history: 12               # dòng context trước
  future: 4                 # dòng context sau

models:
  default: qwen3-abliterated:8b-v2
  uncen: qwen3-abliterated:8b-v2
  ja: qwen3-abliterated:8b-v2

pipeline:
  enable_glossary: true
  max_retries: 3
  num_workers: 1            # 1 = sequential, >1 = parallel
  checkpoint_interval: 10

ja_trans2:
  enabled: true
  batch_size: 20

glossary:
  - source: 頑張る
    target: cố gắng
    context: động viên
```

## Các thành phần

| Module | Chức năng |
|--------|-----------|
| `transcriber.py` | Whisper transcription video→SRT |
| `database.py` | SQLite — lưu projects, subtitles, windows, TM |
| `translator.py` | Ollama LLM wrapper — JSON, fallback, raw, polish |
| `pipeline.py` | Orchestrator — điều phối toàn pipeline |
| `validator.py` | Kiểm tra output LLM — repetition, length, placeholders |
| `ja_translator2.py` | Batch refiner cho JA→VI (translate NULL + polish) |
| `exporter.py` | Xuất SRT, tạo shortcut Windows |

## Database

File: `translation.db` (SQLite)

Bảng chính:
- `projects` — metadata project
- `subtitle_items` — từng dòng phụ đề (gốc + đã dịch)
- `windows` — batch dịch (6 dòng mỗi batch)
- `translation_memory` — TM cache (source→target)
- `dead_letter_queue` — windows fail hoàn toàn
- `glossary` — từ điển tùy chỉnh
- `window_contexts` — Worker A context summaries

## Model khuyến nghị

| Ngôn ngữ | Model |
|----------|-------|
| JA→VI | `huihui_ai/qwen3-abliterated:8b-v2` |
| ZH→VI | `qwen3-abliterated:8b-v2` |
| KO→VI | `qwen3-abliterated:8b-v2` |
| EN→VI | `qwen3-abliterated:8b-v2` |

Pull model: `ollama pull huihui_ai/qwen3-abliterated:8b-v2`

## Xử lý sự cố

**266 subtitles bị `[UNTRANSLATED]`**
- Đã fix: ja_translator2 sẽ translate lại những dòng NULL ở bước post-processing

**Model output lặp lại (repetitive patterns)**
- Đã fix: validator ngưỡng tăng, validation fail vẫn commit

**Export file lỗi PermissionError**
- Đã fix: output path resolve đúng folder `<video>/<project>/<project>.srt`

**Retrain project:**
```bash
# Xóa project trong DB
sqlite3 translation.db "DELETE FROM projects WHERE name='my-project';"
# Hoặc chạy lại — sẽ hỏi có chạy lại không
```
