# Báo Cáo Đánh Giá Kiến Trúc & Lộ Trình Nâng Cấp Dự Án (2026)

> **Dự án**: Subtitle Translator Local AI (JA → VI)  
> **Cập nhật lần cuối**: 2026-11-01  
> **Trạng thái**:
> - Đợt 1 (Kiến trúc & Hiệu năng): Hoàn tất 10/10 hạng mục (`P0.1 → P3.2`) + VRAM Pipeline (`Whisper → Tách âm → Bật LLM → Dịch → Clear VRAM`)
> - Đợt 2 (Chất lượng dịch thực tế `ADN-780`): Hoàn tất 6/6 hạng mục (`Q-P0.1 → Q-P2.1`, `143 passed, 0 failed, 0 warnings`)

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


---

## 6. Đợt 2: Đánh Giá Chất Lượng Chạy Thật (`ADN-780`) & Lộ Trình Nâng Cấp Dịch Thuật

> **Dữ liệu kiểm định**: `ADN-780_whisper.srt` (259 cues, 1.036 dòng) đối chiếu từng dòng với bản dịch `ADN-780.srt` (259 cues).  
> **Ngữ cảnh phim**: Người mẹ đơn thân ~40 tuổi (Sato / `佐藤`), con gái sinh viên đại học năm cuối đang đi xin việc (Miku / `美久`), và bạn trai của Miku (Tsuyoshi / `ツヨシ`).

### 6.1. Điểm đạt được trên bản chạy thật
- **Độ phủ 100% (`259/259` cue)**: Không có dòng trống, không rớt dòng cuối, không rò rỉ chữ Hán/Kana hay ký tự lạ (Thái/Hàn).
- **Vòng đời VRAM**: Pipeline chạy ổn định theo đúng luồng `Whisper → Tách âm → Bật LLM → Dịch → Clear VRAM` mà không bị dừng bởi LLM precheck trước Whisper.

### 6.2. Năm nhóm lỗi thực tế phát hiện trong `ADN-780.srt`
1. **Nhóm 1 (P0 — Nghiêm trọng): Lẫn lộn xưng hô `"tớ - cậu"` & đảo ngược giới tính ở cảnh 18+**
   - Người mẹ 40 tuổi xưng `"tớ - cậu"` với bạn trai của con gái và với cả con gái (#53, #219–#223, #239–#240).
   - Đảo ngược giới tính Nam ↔ Nữ ở cảnh nhạy cảm: #93 dịch thành *"Âm đạo của anh sướng quá nên tớ đã xuất tinh bên trong rồi"*, #127 *"ngực anh cũng to nữa"*, #129 *"tớ cũng đã nhìn dương vật của anh suốt rồi"*.
   - Đoạn độc thoại đầu phim (#1, #3, #10) bị lệch chủ ngữ (*"Cô ấy mang thai khi tôi 20 tuổi"*) và ngược nghĩa (`[非]正規雇用の仕事をてんてんと` → *"làm việc toàn thời gian với hợp đồng chính thức"*).
   - **Nguyên nhân gốc**: Prompt trong `core/ja_translator.py` (dòng 35, 356) ép cứng `だ/する → "tớ/cậu"`; thiếu quy tắc neo giới tính sinh học theo từ vựng giải phẫu (`まんこ`/`おっぱい` vs `ちんぽ`/`中出し`).
2. **Nhóm 2 (P0 — Nghiêm trọng): Chẻ đôi từ vựng qua ranh giới 2 cue làm biến dạng nghĩa**
   - `お母さん` bị cắt thành `#92 (おか)` + `#93 (さんのまんこ...)` và `#174 (お母)` + `#175 (さんの イッチな姿...)` khiến LLM tưởng `さん` là hậu tố tên nam giới.
   - `すいません` bị cắt thành `#29 (...すい)` + `#30 (ません)` → #30 dịch thành *"Không được."*.
   - `そんなこと` và `今まで` bị cắt qua #70–#72; `やめて` và `ったら` bị cắt qua #80–#82; chuỗi #152–#161 bị băm vụn thành 10 cue `< 0.9s`.
3. **Nhóm 3 (P0): Rò rỉ số thứ tự đánh số lặp (`1.`, `2. 2.`, `3. 3.`) & dấu câu rác đầu dòng**
   - Cue #40–#42 bị lọt `1. `, `2. 2. `, `3. 3. ` ra file SRT cuối; Cue #114 bắt đầu bằng dấu phẩy `, để em kiểm tra xem sao?`.
   - **Nguyên nhân gốc**: `_LEADING_NUM_RE` trong `core/ja_translator.py` chỉ strip 1 lần duy nhất.
4. **Nhóm 4 (P1): Whisper VAD cắt mất âm tiết đầu câu (Initial Mora Clipping)**
   - `#75 しぶりに` (mất `ひさ` của `久しぶりに` → dịch *"Lần đầu tiên"*), `#107 きい声` (mất `おお` của `大きい声` → dịch *"tiếng kêu nhỏ"*), `#126 持ちよく` (mất `き` của `気持ちよく` → dịch *"giữ thể lực tốt"*), `#148 けてください` (mất `あ` của `開けてください` → dịch *"cởi ra"*), `#201 関だから` (mất `げん` của `玄関だから`), `#202/#236 活は` (mất `しゅう` của `就活は`).
5. **Nhóm 5 (P1): Whisper nghe nhầm từ đồng âm (Homophone) & biến dạng tên nhân vật**
   - Tên nam chính `ツヨシ` bị biến thành 3 tên (`Tsuyoshi` #33/#96, `Tsuyoi` #38/#238 do `強いさん`, `Tsuyuhi` #63 do `ツユヒ君`).
   - Từ đồng âm 18+ bị chép sai: `#78 生きごはん` (`鳴き声`), `#94 熱心しましょう` (`内緒にしましょう`), `#102 就活典に正処理` (`就活生に性処理`), `#106 仲立ち` (`中出し`), `#167 アッサム` (`発散`), `#175 イッチな姿` (`エッチな姿`), `#179 いらっしゃいませ` (`いっちゃいます`).

### 6.3. Checklist Thực hiện Cải tiến Đợt 2 (Ưu tiên Cao → Thấp)

- [x] **Q-P0.1: Khâu từ bị đứt qua ranh giới 2 cue (`Cross-Cue Word-Boundary Stitcher`) & Gộp mảnh vỡ siêu ngắn trong `core/sub_merger.py`**
  - Mở rộng `_heal_split_tokens_across_cues` và `_KNOWN_SPLIT_WORDS` để nối chuẩn xác các từ bị cắt đôi (`おか`+`さん`, `お母`+`さん`, `すい`+`ません`, `今`+`まで`, `そんな`+`こと`, `や`+`めて`, `っ`+`たら`, `なん`+`で/とか/て`, `いい`+`んじゃ`, `よろしく`+`します`, `う`+`ん`) kể cả khi khoảng cách giữa 2 cue lớn, đồng thời gộp các mảnh vỡ liên tiếp (`#152–#161`).
- [x] **Q-P0.2: Bỏ quy tắc ép `だ/する → "tớ/cậu"` & Bổ sung Neo giới tính giải phẫu + Quan hệ gia đình trong `core/ja_translator.py`, `core/ja_translator2.py`, `core/post_processor.py`**
  - Xóa bỏ hướng dẫn `だ/する → "tớ/cậu"` gây xưng hô `"tớ - cậu"` tràn lan.
  - Bổ sung quy tắc cứng về quan hệ gia đình (`お母さん`/`母` ↔ `娘`/bạn trai của con) và neo giới tính sinh học theo từ vựng giải phẫu (`まんこ`/`おっぱい`/`子宮` = Nữ; `ちんぽ`/`中出し` = Nam) kèm bộ lọc hậu xử lý chặn lỗi *"Âm đạo của anh"*, *"ngực anh"*, *"tớ - cậu"* sai ngữ cảnh.
- [x] **Q-P0.3: Sửa triệt để bộ lọc đánh số lặp (`1. `, `2. 2. `, `3. 3. `) & Dấu câu rác đầu dòng trong `core/ja_translator.py`, `core/ja_translator2.py`, `core/post_processor.py`**
  - Nâng cấp `_strip_leading_number` để xóa lặp mọi tiền tố đánh số (`1. `, `2. 2. `, `3) `) và dọn dấu phẩy/chấm câu bị hở ở đầu dòng dịch (`, để em...` → `Để em...`).
- [x] **Q-P1.1: Bộ khôi phục âm đầu bị VAD cắt cụt (`Initial Mora Recovery`) & Mở rộng từ điển đồng âm trong `core/whisper_normalizer.py`**
  - Thêm bảng `_INITIAL_MORA_CLIP_FIXES` khôi phục âm đầu câu (`^しぶりに` → `久しぶりに`, `^きい声` → `大きい声`, `^持ちよく` → `気持ちよく`, `^けてください` → `開けてください`, `^関だから` → `玄関だから`, `^活は/どう` → `就活は/どう`, `正規雇用の仕事をてんてんと` → `非正規雇用の仕事を転々と`).
  - Mở rộng `_WHISPER_HOMOPHONE_FIXES` cho các từ đồng âm thực tế (`仲立ち` → `中出し`, `正処理` → `性処理`, `就活典` → `就活生`, `イッチな姿` → `エッチな姿`, `熱心しましょう` → `内緒にしましょう`, `生きごはん大きい` → `鳴き声大きい`, `アッサムしちゃい` → `発散しちゃい`, `いらっしゃいませ` ngắn giữa cảnh 18+ → `いっちゃいます`).
- [x] **Q-P1.2: Tinh chỉnh thông số VAD của Whisper (`speech_pad_ms`) trong `core/transcriber.py`**
  - Tăng `speech_pad_ms` trong `vad_parameters` lên `450ms` để chống cắt cụt mora đầu câu ngay từ bước nhận diện giọng nói.
- [x] **Q-P2.1: Đồng nhất tên riêng biến âm (`Phonetic Name Clustering` / `CharacterRegistry`) trong `core/ja_qa_checker.py` & `core/whisper_normalizer.py`**
  - Nhận diện các biến thể nghe nhầm tên riêng kèm hậu tố nhân xưng (`強いさん` / `つよいさん` / `ツユヒ君` ↔ `ツヨシ`) và tự động đồng nhất về tên chuẩn (`Tsuyoshi`).

---

## 7. Kết quả Kiểm thử Tự động Sau Đợt 2

Toàn bộ 10 hạng mục Đợt 1 (`P0.1 → P3.2`), luồng tối ưu VRAM và 6 hạng mục nâng cấp chất lượng Đợt 2 (`Q-P0.1 → Q-P2.1`) đã được kiểm thử tự động trên dữ liệu thực tế của `ADN-780`:

```bash
python -m pytest tests/ -q
# 143 passed in 5.40s (0 failed, 0 warnings)
```

---

## 8. Đợt 3: Đối Chiếu Chạy Thật Lần 1 vs. Lần 2 (`ADN-780`) & Xử Lý Lỗi Character Name / LLM Retry

> **Dữ liệu đối chiếu**: `ADN-780.old_backup.srt` + `ADN-780_whisper.old_backup.srt` (Lần 1 — 259 cues) vs. `ADN-780.srt` + `ADN-780_whisper.srt` (Lần 2 — 254 cues) + `subtitle_translator.log`.

### 8.1. Kết quả cải thiện đạt được ở Lần 2
- **Khâu tự động 59 từ tiếng Nhật bị cắt đôi** (`SubMerger: healed 59 split Japanese word(s) across adjacent cues`).
- **Xóa sổ 100% lỗi xưng hô `"tớ - cậu"`** (Lần 1 có >25 câu mẹ và bạn trai con gái xưng `"tớ - cậu"`, Lần 2 giảm về **0 câu**).
- **Xóa sổ 100% lỗi rò rỉ tiền tố số thứ tự (`1. `, `2. 2. `)** và lệch dòng (#39–#42).
- **Sửa triệt để các câu dịch sai nghiêm trọng ở Lần 1**:
  - #1, #3, #10: *"Tôi mang thai con gái khi mới hai mươi tuổi..."* / *"nuôi con gái một mình"* / *"liên tục chuyển đổi các công việc với chế độ lao động phi chính thức"*.
  - #80: *"Miku, tiếng rên rỉ của em lớn quá, thật tuyệt vời"* (thay vì *"ăn sống đồ ăn"*).
  - #115–#116: *"Âm đạo của mẹ sướng quá nên đã xuất tinh bên trong rồi"* / *"Phải giữ bí mật với Miku nhé"*.
  - #124, #127, #128, #199, #200, #233: Dịch chuẩn 100% ngữ cảnh `就活生`, `性欲処理係`, `中出し`, `玄関`, `就活`.

### 8.2. Bốn nguyên nhân gốc rễ gây lỗi Character Name & LLM báo lỗi liên tục ở Lần 2
1. **Lỗi 1 (P0 — Nghiêm trọng nhất): So khớp chuỗi con `'mik' in 'miku'` trong `Validator._check_character_name_consistency` (`core/validator.py`)**
   - `_generate_mistaken_aliases('Miku')` tạo alias `'Mik'` (`len=3`), trong khi `_check_character_name_consistency` kiểm tra bằng `if v in t_lower:` (substring) thay vì ranh giới từ (`\b`).
   - Hệ quả: **100% số lần LLM viết đúng `"Miku"`** đều khớp cả `'mik'` lẫn `'miku'` → ném **72 dòng `ERROR`** trên 12 Windows (5, 6, 7, 10, 13, 19, 20, 21, 22, 38, 41, 42), ép pipeline **vứt bỏ bản `polished` để hạ cấp về bản dịch thô `rough`**, đồng thời bắt LLM dịch lại gấp đôi (`retrying once`).
2. **Lỗi 2 (P0): `AutoCharacterExtractor` trích xuất nhầm danh từ thường thành tên nhân vật (`Joo`, `Yuki`) & biến âm mới của Whisper Lần 2**
   - `女王様` (*nữ hoàng / tiểu thư kiêu kỳ* ở #123) bị tách `女王` trước hậu tố `様` thành nhân vật **`Joo`** (*"Nhưng vì là Joo..."*).
   - `つゆきくん` (#63) bị tách thành nhân vật ảo **`Yuki`**.
   - `誰かさん` (*ai đó* ở #208) bị dịch thành **`Dai-san`**; `強石さん` (#235) bị dịch thành **`Oishi`**; `強し優しい` (#204) mất tên **`Tsuyoshi`**; `城南大学` (#229) bị đọc ngược thành **`Đại học Minamiyo`**; `ゆう子でどう` (#243) nghe nhầm từ `いうことでどう`.
3. **Lỗi 3 (P1): Tràn `max_tokens=768/1536` ở Pass 3 (`qa_review`) & `_polish_fallback` bỏ phí cả cửa sổ khi thiếu 1 dòng**
   - Window 16: `_polish_fallback` nhận được 5/6 dòng nhưng hủy bỏ toàn bộ để về `rough` thay vì dùng `_map_numbered` giữ lại 5 dòng đã polish.
   - Window 24: `qa_review` bắt LLM xuất JSON cho cả 6 câu (kể cả câu đúng `"ok": true`) làm tràn `max_tokens`.
4. **Lỗi 4 (P1): 5 mẫu từ ghép bị cắt giữa 2 cue chưa được `SubMerger` nối**
   - `#58–#59` (`一` + `日`), `#73–#74` (`可愛` + `い`), `#83–#84` (`そした` + `ら`), `#100–#102` (`わか` + `んない`, `濡` + `らしてる`).

### 8.3. Checklist Thực hiện Cải tiến Đợt 3 (Ưu tiên Cao → Thấp)

- [x] **R-P0.1: Sửa triệt để lỗi `'mik' in 'miku'` trong `core/validator.py`, `core/ja_qa_checker.py` & Chặn hạ cấp oan `polished -> rough` trong `core/pipeline.py`**
  - Chuyển kiểm tra tên nhân vật trong `Validator._check_character_name_consistency` sang Word-Boundary Regex (`\b`) và loại bỏ các alias là tiền tố/chuỗi con ngắn (`< 4` ký tự) của tên gốc.
  - Tự động chuẩn hóa các `mistaken_aliases` về tên `canonical` trước khi validate; trong `core/pipeline.py`, không hạ cấp từ `polished` xuống `rough` nếu `rough` cũng không vượt qua `validate_window_content`.
- [x] **R-P0.2: Chặn trích xuất nhầm danh từ thành tên người (`女王様 -> Joo`, `誰かさん -> Dai-san`) & Chuẩn hóa các biến âm mới (`つゆきくん`, `強石さん`, `強し`, `城南大学`, `ゆう子でどう`)**
  - Mở rộng `_NON_NAME_HONORIFIC_STEMS` trong `core/ja_qa_checker.py` (`女王`, `王様`, `王子`, `姫`, `お姫`, `誰か`, `だれか`, `皆様`, `皆さま`, `神様`, `仏様`, `お客`, `ご近所`...).
  - Cập nhật `core/whisper_normalizer.py` và `core/post_processor.py` để sửa `つゆきくん` → `ツヨシくん`, `強石さん` → `ツヨシさん`, `強し優しい` → `ツヨシ優しい`, `ゆう子でどう` → `いうことでどう`, `城南大学` → `Đại học Jonan`, và chặn dịch nhầm `誰かさん` thành `Dai-san`.
- [x] **R-P1.1: Tối ưu `_polish_fallback` (giữ từng dòng đã polish bằng `_map_numbered`) & Tinh gọn JSON của Pass 3 `qa_review` chống tràn `max_tokens` trong `core/ja_translator.py` và `core/ja_translator2.py`**
  - Sửa `_polish_fallback` dùng `_map_numbered` để giữ toàn bộ các dòng đã polish hợp lệ và chỉ fallback về `rough` cho đúng dòng bị thiếu.
  - Tinh gọn prompt và parser của `qa_review`: chỉ yêu cầu LLM trả về các câu có lỗi (`"ok": false`) hoặc mảng rỗng `[]`, kèm `max_tokens` đủ rộng và giới hạn `"issue"` ngắn gọn.
- [x] **R-P1.2: Mở rộng `SubMerger._heal_split_tokens_across_cues` cho số đếm + đơn vị (`一`+`日`), tính từ đuôi `い` (`可愛`+`い`), và từ nối/động từ khẩu ngữ (`そした`+`ら`, `わか`+`んない`, `濡`+`らしてる`) trong `core/sub_merger.py`**

### 8.4. Kết quả Kiểm thử Tự động Sau Đợt 3

Toàn bộ các hạng mục Đợt 1 (`P0.1 → P3.2`), Đợt 2 (`Q-P0.1 → Q-P2.1`) và Đợt 3 (`R-P0.1 → R-P1.2`) đã được kiểm thử tự động:

```bash
python -m pytest tests/ -q
# 147 passed in 5.36s (0 failed, 0 warnings)
```

---

## 9. Đợt 4: Kiểm Định Chạy Thật `JUL-308` & Khắc Phục Lỗi VAD Xóa 97% Phụ Đề / Lệch Dòng / Đồng Âm NTR

> **Dữ liệu kiểm định**: `JUL-308_whisper.srt` (63 cues) + `JUL-308.srt` (63 cues) + `subtitle_translator.log` + phân tích phổ âm thanh `E:\HIDE\JAV\JUL-308.mp4` (`01:57:34`).

### 9.1. Bốn nhóm lỗi phát hiện trên `JUL-308`
1. **Lỗi 1 (P0 — Cấp bách nhất): Silero VAD xóa 97.2% thời lượng âm thanh (`01:54:16 / 01:57:34`), chỉ còn 63 câu phụ đề**
   - Trong các cảnh 18+ của `JUL-308` (`00:07→00:39`, `00:42→00:59`, `01:02→01:24`, `01:26→01:57`), diễn viên nói bằng giọng thì thầm/hơi thở gấp nên `Silero VAD` chấm xác suất `< 0.35` và xóa trắng trước khi đưa vào Whisper, dù kiểm tra thực tế khi tắt VAD có thoại rõ ràng (`no_speech_prob = 0.17..0.30`).
   - `config.yaml` đang để `audio_filter: highpass=f=80` (thiếu `dynaudnorm`), `vad_threshold: 0.35`, `no_speech_threshold: 0.6` và `log_prob_threshold: -1.0`.
2. **Lỗi 2 (P0): Đứt câu qua ranh giới 2 cue làm ngược nghĩa hoặc cụt câu (#13–#14, #28–#31, #39–#41, #58–#60)**
   - `#13–#14`: `...あなた` | `以外の人となんて` → #14 bị dịch ngược nghĩa thành *"Tôi chỉ yêu người khác ngoài cô ấy"*.
   - `#28–#29`: `...妻` | `と直接連絡を取ったり` → mất chữ `妻` ở đầu #29 khiến câu dịch thành *"liên hệ trực tiếp với tôi mà không qua tôi"*.
   - `#30–#31`: `...わか` | `りました` (`わかりました`); `#39–#41`: `それ`+`から`, `何`+`しに`; `#59–#60`: `この` | `きっかけを作ったのは` (#59 bị dịch cụt thành *"Cái."*).
3. **Lỗi 3 (P0): Whisper nghe nhầm từ đồng âm NTR & tên nữ chính `Maki (真希)`**
   - `#23`: `常にゴムをしていただくこと` (*luôn đeo bao cao su*) bị nghe thành `常に拷問していただくこと` (*luôn tra tấn/hành hạ*).
   - `#24`: `一部始終` (*toàn bộ từ đầu đến cuối*) bị ghi thành `一部指示` (*một phần hướng dẫn*).
   - `#36–#37`: `感じたことのない快感` (*khoái cảm chưa từng cảm nhận*) bị nghe thành `たことのないクエスト` (*nhiệm vụ chưa từng trải qua*).
   - `#10 & #17`: Tên người vợ `真希 (Maki)` bị ghi thành `巻きよう` / `巻き絵の愛`.
   - `#41`: Người vợ hỏi khách nam (`いらしたんですか 主人はいませんけど`) bị dịch thành *"Bà đến đây làm gì vậy?"*.
4. **Lỗi 4 (P1): Nhảy dòng / lệch câu ở Pass 2 (`polish_translate`) khi gặp câu ngắn bị cắt (#25–#30)**
   - Ở Window 4 (#25–#30), câu #26 (`一切持たないこと` = *Tuyệt đối không được có*) bị Pass 2 ghi đè bằng nội dung của câu #28 (*"tôi nghĩ điều này là không thể, nhưng vợ,"*), còn câu #27 (`最後に` = *Cuối cùng,*) bị ghi đè bằng *"Tôi mong muốn vậy."*.

### 9.2. Checklist Thực hiện Cải tiến Đợt 4 (Ưu tiên Cao → Thấp)

- [ ] **J-P0.1: Khắc phục triệt để lỗi VAD xóa âm thanh thoại thì thầm/rên (`Adaptive VAD Gap Recovery` + `dynaudnorm`) trong `core/transcriber.py` & `config.yaml`**
  - Cập nhật `config.yaml` và `WhisperTranscriber`: hạ `vad_threshold` xuống `0.20`, bật `audio_filter: highpass=f=80,dynaudnorm=f=150:g=15`, đồng bộ `no_speech_threshold: 0.85` và `log_prob_threshold: -1.35`.
  - Bổ sung cơ chế **Adaptive VAD Gap Recovery** trong `WhisperTranscriber.transcribe`: tự động phát hiện các khoảng trống dài `>= 180s` bị VAD cắt mất và quét bù (re-transcribe) trên đúng các khoảng trống đó với `vad_filter=False` kèm bộ lọc chống hallucination (`no_speech_prob <= 0.72`, `avg_logprob >= -1.25`, `_is_noise_segment`, khử lặp câu) để khôi phục 100% lời thoại thật mà không sinh rác.
- [ ] **J-P0.2: Mở rộng `SubMerger` khâu các mẫu đứt câu làm ngược nghĩa (`あなた`+`以外`, `妻`+`と直接`, `わか`+`りました`, `それ`+`から`, `何`+`しに`, `この`+`きっかけ`, `僕`+`はお母さん`) trong `core/sub_merger.py`**
  - Mở rộng `_KNOWN_SPLIT_WORDS` (`わかりました`, `わかります`, `わかった`, `それから`, `何しに`, `感じた`, `感じて`, `いいんでしょ`, `いいですか`, `ダメだ`, `だめだ`) và khâu danh từ/đại từ cuối cue A với `以外` / `と直接` / `と会` / `と連絡` ở đầu cue B; gộp cue chỉ gồm 1 từ chỉ định (`この`/`その`/`あの`/`どの`) với cue kế tiếp.
- [ ] **J-P1.1: Bổ sung Từ điển Đồng âm NTR (`拷問 -> ゴム`, `一部指示 -> 一部始終`, `クエスト -> 快感`, `巻き絵の愛 -> 真希への愛`) & Chuẩn hóa xưng hô trong `core/whisper_normalizer.py` và `core/post_processor.py`**
  - Chuẩn hóa các lỗi đồng âm trong `core/whisper_normalizer.py` và bổ sung quy tắc hậu xử lý trong `core/post_processor.py` (kể cả sửa `Bà đến đây làm gì vậy? Chồng tôi...` → `Anh đến đây làm gì vậy? Chồng tôi...`, `Mẹ con gái,` / `Mẹ con xin lỗi` → `Mẹ ơi,`).
- [ ] **J-P1.2: Chống nhảy dòng / lệch câu ở Pass 2 (`polish_translate`) bằng kiểm tra bảo toàn phủ định & độ dài dòng ngắn (`Line Alignment Guard`) trong `core/ja_translator.py`**
  - Trong `JaTranslator.polish_translate`: nếu câu gốc tiếng Nhật ngắn (`<= 8` ký tự như `最後に`, `一切持たないこと`) hoặc có cấu trúc phủ định (`ない`/`ません`/`ダメ`) mà bản `polished[j]` lại dài gấp `> 2.5x` bản `rough[j]` hoặc làm mất từ phủ định/trùng lặp với dòng lân cận trong cùng window, tự động giữ lại `rough[j]` cho dòng đó.

---

## 10. Đề xuất cải tiến từ Code Review 2026-11-01 (đã triển khai & kiểm thử)

> **Nguồn**: review 5 trục (correctness / readability / architecture / security / performance) trên working tree + commit `ce8340f`.
> **Verify cuối đợt**: `python -m pytest tests/ -q` → **167 passed** · `python run_tests.py` → **27/27** · `py_compile` OK.
> Quy ước: `[x]` = đã code + có test hồi quy; `[ ]` = backlog (chưa làm).

### 10.1. P0 — Chặn merge

- [x] **R2-P0.1: Sửa regex strip số đầu dòng phá nội dung hợp lệ**
  - Triệu chứng: `_LEADING_NUM_RE` / `_LEADING_NUM_LEAK_RE` cắt `20-30 tuổi` → `30 tuổi`, `3:00` → `00`, `1-2 tiếng` → `2 tiếng` trên MỌI dòng dịch.
  - Fix: chỉ strip `N.`/`N)` (1–3 chữ số + **bắt buộc** khoảng trắng) hoặc `N、`; bỏ `:` và `-` khỏi separator. Áp ở `core/ja_translator.py`, `core/ja_translator2.py`, `core/post_processor.py`.
  - Test: `tests/test_improvements_2026_11.py::test_strip_leading_number_*`, `::test_post_processor_keeps_time_and_range`.
- [x] **R2-P0.2: `run_tests.py` đỏ 2/27 do mock lỗi thời**
  - Nguyên nhân: mock `literal_translate` thiếu kwarg `history`/`tm_examples` (đã thêm ở P0.2 đợt trước nhưng test chưa cập nhật).
  - Fix: `**kwargs` cho 3 mock. Verify 27/27.

### 10.2. P1 — Chất lượng dữ liệu

- [x] **R2-P1.1: Siết Phonetic Name Clustering** (`core/ja_qa_checker.py`)
  - Triệu chứng: gộp mọi cặp cùng chữ đầu + `fuzz.ratio ≥ 75` → `Yuki/Yukio` (88.9), `Haruka/Haruko` (83.3) bị gộp oan, hậu quả đổi tên nhân vật này thành nhân vật kia trong bản dịch.
  - Fix: chỉ gộp khi tên phụ **xuất hiện hiếm** (`score_phụ × 3 ≤ score_chính`).
  - Test: merge `Tsuyoshi/Tsuyoi` tỉ lệ 5:1; **không** merge `Yuki/Yukio` 3:3.
- [x] **R2-P1.2: Thu hẹp gender-lock** (`core/post_processor.py`)
  - Fix: bỏ `ngực|vú|bướm` khỏi `_FEMALE_ANATOMY_WRONG_MALE_RE` (nam cũng có ngực; "con bướm" là danh từ thật).
  - Test: `ngực anh`, `con bướm của anh` giữ nguyên; `âm đạo của anh` → `của cô/mẹ`.
- [x] **R2-P1.3: Tách data từng phim khỏi code → `film_fixes_<PROJECT>.yaml`**
  - Thêm `core/film_fixes.py` (loader + compile regex + validate, non-blocking khi file lỗi) và `film_fixes_ADN-780.yaml` (24 homophone + 5 mora_clip + 12 source_conditioned).
  - Đã di chuyển toàn bộ entry ADN-780 ra khỏi `core/whisper_normalizer.py` / `core/post_processor.py`; pipeline nạp theo `project.name` cho WhisperNormalizer, RuleBasedPostProcessor và `cue_repair`.
  - Test: chỉ áp khi truyền film_fixes; project khác → pack rỗng; `仲立ち` không còn bị thay ở phim khác.

### 10.3. P2 — Kiến trúc / vận hành

- [x] **R2-P2.1: Guard RuleProcessor no-op** — `pipeline._rules_enabled = bool(anti_artifacts or character_names)`; bỏ 2 lần đọc/parse `config.yaml` mỗi window khi config không khai báo rule.
- [x] **R2-P2.2: `models.llama.name` = alias llama-server** (`qwen35-9b-mtp` thay vì tên model Ollama cũ) trong `config.yaml`.
- [x] **R2-P2.3: `conftest.py` cho pytest** — import `core.*` ổn định khi chạy `python -m pytest` từ root (trước đây `tests/test_roadmap_upgrades.py` crash `ModuleNotFoundError` khi chạy trực tiếp).
- [x] **R2-P2.4: Xác nhận các cải tiến hiệu năng đã có từ đợt trước** — lock chỉ bao quanh đọc context (không giữ khi chờ LLM); `_get_context` dùng `Database.get_items_by_pos_range`; `_save_to_tm` đã batch.
  - Test bổ sung: `tests/test_improvements_2026_11.py::test_db_get_items_by_pos_range`.

### 10.4. Backlog còn lại (chưa triển khai)

- [ ] **R2-B1: Thống nhất một test entrypoint** — hiện có 2 runner (`pytest tests/` + `run_tests.py`); khuyến nghị chuyển hẳn `run_tests.py` sang pytest (giữ wrapper alias để không phá workflow cũ).
- [ ] **R2-B2: Đo hiệu năng thật của `num_workers > 1`** sau khi bỏ lock quanh LLM (bench 2 phim, so wall-clock workers=1 vs 2).
- [ ] **R2-B3: Mở rộng film_fixes cho các phim cũ còn hardcode** — ROE-459 (post_processor/whisper_normalizer), JUR-624/630/837 artifacts — chuyển dần khi chạm lại từng phim.
- [ ] **R2-B4: Gom glossary per-film về `glossaries/`** (đang rải ở root) — kèm migration loader + GUI.
- [ ] **R2-B5: Làm rõ UI "Tách phụ đề dài"** — `sub_splitter.split_long_sub` hiện no-op (giữ timestamp); đổi label/tooltip thành "cảnh báo cue dài" để không gây hiểu lầm.


