# Báo Cáo Đánh Giá Kiến Trúc & Lộ Trình Nâng Cấp Dự Án (2026)

> **Dự án**: Subtitle Translator Local AI (JA → VI)  
> **Cập nhật lần cuối**: 2026-11-01  
> **Trạng thái**: Hoàn tất toàn bộ 10/10 hạng mục nâng cấp từ P0 → P3 (`138 passed, 0 failed, 0 warnings` trên toàn bộ test suite)

---

## 1. Tổng quan Đánh giá Dự án

Dự án không chỉ là một wrapper gọi LLM đơn thuần mà đã phát triển thành một **pipeline dịch thuật chuyên biệt cho tiếng Nhật sang tiếng Việt**, tối ưu hóa sâu cho phần cứng tầm trung (**GPU 8GB VRAM** như RTX 3060 Ti / 4060). Codebase thể hiện rõ quá trình tinh chỉnh thực chiến qua nhiều bộ phim thực tế (xử lý từ lóng, lược chủ ngữ tiếng Nhật, ảo giác của Whisper, và giới hạn của model 7B–9B).

---

## 2. Điểm mạnh nổi bật (Strengths)

### 2.1. Quản lý vòng đời VRAM thông minh trên GPU 8GB
- **Phối hợp nhịp nhàng giữa Whisper và LLM**: `LlamaServerManager` (`core/llama_server.py`) và `WhisperTranscriber` (`core/transcriber.py`) tự động nhường VRAM cho nhau. Trong `main.py`, hệ thống gọi `llama_mgr.free_vram()` trước khi nạp Faster-Whisper `large-v3`, sau đó giải phóng CTranslate2 model + `torch.cuda.empty_cache()` trong `clear_vram()` rồi mới khởi động lại `llama-server`.
- **Tối ưu `llama-server` thực chiến**: Cấu hình sẵn Flash Attention (`-fa on`), lượng tử hóa KV cache (`--cache-type-k q8_0 --cache-type-v q8_0`) và Speculative Decoding (`--spec-type draft-mtp`) cho dòng Qwen3.5 trong `LlamaServerManager.start`, kèm cơ chế PID file + kiểm tra tên tiến trình `_reap_orphan` chống rò rỉ VRAM khi crash.

### 2.2. Tiền xử lý âm thanh & Phụ đề tiếng Nhật rất sâu
- **Chia cue theo word-level timestamps**: `WhisperTranscriber._split_by_words` (`core/transcriber.py`) chặn độ dài từng từ bằng `_estimate_speech_s` (tránh lỗi Whisper kéo giãn 1 từ qua cả đoạn nhạc dạo), ngắt cue theo `hard_gap_split_s` (1.2s) và tôn trọng ranh giới từ ghép tiếng Nhật.
- **Khâu từ bị cắt đôi qua 2 cue liên tiếp**: `SubMerger._heal_split_tokens_across_cues` (`core/sub_merger.py`) kết hợp `fugashi` (MeCab) + danh sách `_KNOWN_SPLIT_WORDS` + nhận diện trợ từ đầu câu (`の/を/が/へ/に`) để nối các từ bị Whisper cắt ngang giữa 2 phụ đề mà không làm lệch timestamp.
- **Lọc Glossary theo hình thái học (Lemma Matching)**: `TranslationPipeline._filter_glossary_for_window` (`core/pipeline.py`) dùng `fugashi` lemma để khớp cả động từ/tính từ đã chia (ví dụ: `濡れる` khớp được `濡れていた`), ưu tiên term dài bao phủ term ngắn để tiết kiệm context prompt.

### 2.3. Kiến trúc Hậu kiểm & Sửa lỗi đa tầng không tốn LLM (Zero-LLM Overhead)
- **Trích xuất tên nhân vật bằng NLP (<50ms)**: `AutoCharacterExtractor` (`core/ja_qa_checker.py`) dùng `fugashi` (`固有名詞/人名` + hậu tố `さん/ちゃん/くん/様`) + `pykakasi` (Hepburn Romaji) + `rapidfuzz` để gom cụm tên nhân vật toàn phim mà không cần gọi LLM.
- **Hậu xử lý có điều kiện theo câu gốc JA**: `RuleBasedPostProcessor` (`core/post_processor.py`) sửa lỗi âm Hán-Việt sai ngữ cảnh dựa trên Kanji câu gốc (`_SOURCE_CONDITIONED_FIXES`), sửa lỗi dịch nhầm số 2 thành `"vâng"` (`_VANG_RE`), và tự động đọc luật cấm `KHÔNG dịch "..."` từ `context_hint` của Glossary.
- **QA tự động**: `JaQAChecker` (`core/ja_qa_checker.py`) bắt lỗi đảo ngược phủ định (`check_negation_mismatch` có loại trừ câu hỏi đuôi `_JA_PSEUDO_NEGATION_RE`) và chỉ gọi LLM retry có định hướng cho tối đa 15 câu lỗi nặng mỗi phim.

### 2.4. Cơ chế Tự học xuyên dự án & Độ bền dữ liệu
- `GlossaryMiner` (`core/glossary_miner.py`) tự động đào từ vựng mới qua nhiều phim bằng điểm co-occurrence 2 chiều; `core/human_feedback.py` hỗ trợ học từ file `.srt` đã sửa tay trên Aegisub.
- SQLite chạy chế độ WAL (`core/database.py`), có Dead-Letter Queue, xoay vòng 5 bản backup DB (`_rotate_db_backups`) và ghi file SRT nguyên tử (`_atomic_write_srt`).

---

## 3. Điểm yếu & Tồn đọng kỹ thuật (Weaknesses & Technical Debt)

### 🔴 Mức độ Nghiêm trọng Cao (Critical / High)

1. **Chế độ đa luồng (`num_workers > 1`) thực chất bị khóa tuần tự hoàn toàn**:
   - **Vị trí**: `TranslationPipeline._process_single_task` (`core/pipeline.py`)
   - **Nguyên nhân**: Khối `with self._context_lock:` bao bọc toàn bộ thân hàm, bao gồm cả vòng lặp gọi LLM `self._translate_window(window, context)`. Các worker threads phải xếp hàng chờ nhau từng window một. Ngoài ra, trong `_process_parallel`, lệnh `progress.update(task, advance=1, completed=completed_cnt)` truyền cả `advance` lẫn `completed` gây lệch thanh tiến độ khi có window lỗi.
2. **Truy vấn `db.get_all_items()` thừa ở mỗi Window nhưng `context` lại bị bỏ quên trong luồng dịch JA**:
   - **Vị trí**: `_process_single_task` và `_translate_window_ja` (`core/pipeline.py`)
   - **Nguyên nhân**: Mỗi window đều gọi `self.db.get_all_items(project_id)` đọc toàn bộ hàng nghìn dòng từ SQLite chỉ để cắt lấy 3 câu trước (`history`) và 2 câu sau (`future`), dù `Database.get_items_by_sub_range` đã có sẵn. Nghiêm trọng hơn, trong `_translate_window_ja(self, window, context)`, biến `context` (`history` và `future`) hoàn toàn không được truyền vào `JaTranslator`, gây đứt mạch xưng hô ở ranh giới giữa hai window.
3. **Hardcode tên nhân vật của phim cũ trong `main.py` làm biến dạng phụ đề gốc tiếng Nhật**:
   - **Vị trí**: `JA_AUTO_FIX` (`main.py`) và `PROMPTS['vi']` (`core/pipeline.py`)
   - **Nguyên nhân**: Từ điển `JA_AUTO_FIX` hardcode tên riêng của một bộ phim cũ (`'かよこ': 'Kachiko'`, `'たけお': 'Takao'`, `'あゆみ': 'Ayumi'`, `'高田': 'Takada'`) và ghi đè trực tiếp vào file SRT tiếng Nhật ngay sau khi Whisper transcribe xong, làm trộn chữ Latin vào câu gốc trước khi qua MeCab và gây sửa nhầm từ ở các phim khác.

### 🟠 Mức độ Trung bình (Medium)

4. **Mở/đóng kết nối SQLite liên tục và `GlossaryMiner` quét lại toàn bộ lịch sử DB**:
   - `_save_to_tm` (`core/pipeline.py`) gọi `save_translation_memory` từng dòng, mở/đóng 2 connection mỗi dòng thay vì batch transaction.
   - Cuối mỗi phim, `miner.mine_all_projects(skip_existing=True)` (`core/glossary_miner.py`) đọc lại toàn bộ các bộ phim lịch sử trong DB và chạy `fugashi` tách từ lại từ đầu.
5. **Chồng chéo giữa các module hậu kiểm, cấu hình và `fugashi.Tagger`**:
   - `_CJK_LEAK_FIXES` (`core/post_processor.py`) và `_COMMON_RESIDUAL_CJK_MAP` (`core/ja_qa_checker.py`) trùng lặp nhưng nằm ở 2 file.
   - `main.py` vẫn định nghĩa và gọi `validate_config()` cũ thay vì dùng `core/config_validator.py`.
   - 4 module (`pipeline.py`, `ja_qa_checker.py`, `sub_merger.py`, `glossary_miner.py`) khởi tạo 4 instance `fugashi.Tagger()` riêng lẻ trong RAM.
6. **Nút "Tách phụ đề dài (> 6 giây)" (`split_long_subs`) hiện là hàm rỗng (No-op)**:
   - `split_long_sub` trong `core/sub_splitter.py` chỉ trả về nguyên câu gốc `[(start_ms, end_ms, text)]`, khiến tùy chọn tách câu dài khi nạp SRT ngoài không có tác dụng.

### 🟡 Mức độ Thấp / UX & Tổ chức Code (Low)

7. **Quản lý tiến trình con & hiệu năng hiển thị trên `gui.py` + Cấu trúc thư mục**:
   - `_kill_running_process` trên `gui.py` gọi `proc.terminate()` trên Windows không đóng được cây tiến trình con (`ffmpeg`, `llama-server.exe`).
   - `_poll_log_queue` trong `gui.py` chạy timer 500ms vô tận không làm gì.
   - `GlossaryPage` tạo hàng nghìn `QWidget` trực tiếp khi từ điển lớn; các file `glossary_<FILM>.*` nằm rải rác ở thư mục gốc thay vì trong `glossaries/`.

---

## 4. Lộ trình Nâng cấp & Checklist Thực hiện (Ưu tiên Cao → Thấp)

### Giai đoạn P0 — Sửa lỗi Nghiêm trọng & Tối ưu Tức thì (Critical Quick Wins)
- [x] **P0.1: Dọn sạch tên nhân vật hardcode trong `JA_AUTO_FIX` (`main.py`) & `PROMPTS['vi']` (`core/pipeline.py`)**
  - Giữ lại các cặp sửa lỗi đồng âm thực sự của Whisper, loại bỏ tên riêng phim cũ (`かよこ`, `たけお`, `あゆみ`, `高田`, `しの田`) để không làm hỏng câu gốc tiếng Nhật.
- [x] **P0.2: Truyền ngữ cảnh liên-window (`history`) vào `JaTranslator` (`core/ja_translator.py` & `core/pipeline.py`)**
  - Truyền các cặp câu `(JA → VI)` cuối của window liền trước từ `context['history']` vào Pass 2 (`polish_translate` & `_polish_fallback`) để giữ mạch xưng hô xuyên suốt giữa các window.
- [x] **P0.3: Mở khóa đa luồng thực sự & Tối ưu truy vấn context SQLite (`core/pipeline.py`)**
  - Thu hẹp phạm vi `self._context_lock` trong `_process_single_task` (không giữ lock khi gọi LLM `_translate_window`).
  - Thay `db.get_all_items(project_id)` ở mỗi window bằng truy vấn theo khoảng `db.get_items_by_sub_range`.
  - Sửa lỗi `progress.update` trong `_process_parallel`.
- [x] **P0.4: Dọn sạch cây tiến trình con khi dừng Pipeline & Xóa timer thừa trên `gui.py`**
  - Dùng `taskkill /F /T /PID` trên Windows trong `_kill_running_process` để đóng cả tiến trình con (`ffmpeg`, `llama-server`).
  - Loại bỏ hàm timer rỗng `_poll_log_queue`.

### Giai đoạn P1 — Tối ưu Hiệu năng & Tài nguyên (Performance)
- [x] **P1.1: Batch Transaction cho Translation Memory & Incremental Mining cho `GlossaryMiner`**
  - Thêm `save_translation_memory_batch` trong `core/database.py` dùng 1 transaction duy nhất cho toàn bộ câu trong phim.
  - Bổ sung theo dõi trạng thái đã mine (`mined_projects`) để `GlossaryMiner` chỉ phân tích các project mới thay vì quét lại toàn bộ DB mỗi lần chạy.
- [x] **P1.2: Singleton `fugashi.Tagger` dùng chung (`core/mecab_utils.py`)**
  - Tạo module `core/mecab_utils.py` quản lý tagger dùng chung cho `pipeline.py`, `ja_qa_checker.py`, `sub_merger.py` và `glossary_miner.py`.

### Giai đoạn P2 — Nâng cấp Chất lượng Dịch thuật & Tính năng (Quality & Features)
- [x] **P2.1: Dynamic Few-Shot từ Translation Memory (TM) bằng `rapidfuzz`**
  - Tìm các cặp câu tương đồng cao (65%–95%) từ TM để chèn làm ví dụ tham khảo (few-shot) cho Pass 2 (`polish_translate`).
- [x] **P2.2: Khôi phục `split_long_sub` thông minh cho file SRT ngoài & Tích hợp nút Học từ SRT sửa tay (`human_feedback`) lên GUI**
  - Cải tiến `core/sub_splitter.py` tách câu dài (>6s và >45 ký tự) theo dấu ngắt câu tiếng Nhật/Latin với nội suy thời gian theo tỷ lệ ký tự.
  - Thêm nút "Học từ SRT đã sửa" trên `GlossaryPage` của `gui.py` gọi trực tiếp `learn_from_human_srt`.

### Giai đoạn P3 — Chuẩn hóa Kiến trúc & Bảo trì (Architecture & Maintainability)
- [x] **P3.1: Hợp nhất bảng sửa lỗi CJK rò rỉ & Đồng nhất `config_validator.py`**
  - Hợp nhất `_CJK_LEAK_FIXES` (`core/post_processor.py`) và `_COMMON_RESIDUAL_CJK_MAP` (`core/ja_qa_checker.py`).
  - Xóa hàm `validate_config` trùng lặp trong `main.py` và chuyển sang sử dụng `core/config_validator.py`.
- [x] **P3.2: Quy hoạch thư mục `glossaries/` & Tối ưu tải danh sách lớn trên `GlossaryPage` (`gui.py`)**
  - Hỗ trợ lưu/đọc các file `glossary_<FILM>.*` trong thư mục `glossaries/` (tự động tương thích ngược với file ở thư mục gốc) và di chuyển các file glossary tự sinh (`.learned.yaml`, `.characters.yaml`) vào `glossaries/`.
  - Tối ưu hiển thị trên `GlossaryPage` (lazy batch rendering `RENDER_BATCH_SIZE = 120` + nút "Hiển thị thêm…", bỏ gọi lặp `_update_count_badge` $O(N^2)$ trong lúc nạp) để UI không bị khựng khi từ điển vượt hàng trăm từ.

---

## 5. Kết quả Kiểm thử Tự động (Verification Summary)

Toàn bộ các cải tiến từ **P0.1 → P3.2** đã được kiểm thử bằng bộ test chuyên biệt `tests/test_roadmap_upgrades.py` cùng toàn bộ regression suite của dự án:

```bash
python -m pytest tests/ -q
# 138 passed in 5.70s (0 failed, 0 warnings)
```
