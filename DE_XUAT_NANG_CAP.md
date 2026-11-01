# Kế hoạch & Trạng thái Nâng cấp Dịch thuật & Huấn luyện Tự học Từ mới (JA -> VI)

Tài liệu lưu trữ các phân tích, số liệu thực tế từ cơ sở dữ liệu (`translation.db`) và trạng thái triển khai các giải pháp cải tiến chất lượng dịch thuật tiếng Nhật.

---

## 1. Hiện trạng & Ghi chú Kiến trúc Quan trọng

- **Về Worker A (`ja_context`)**: Đã giữ ở trạng thái **TẮT (`ja_context.enabled: false`)** theo thực nghiệm thực tế: với mô hình 8B nhẹ, Worker A trả về chuỗi rỗng 100% và làm tăng đáng kể thời gian dịch.
- **Giải pháp thay thế hoàn toàn Worker A cho Tên riêng**: Đã tách rời hoàn toàn [`CharacterRegistry`](core/character_registry.py) và việc nhận diện tên nhân vật khỏi Worker A bằng module [`AutoCharacterExtractor`](core/ja_qa_checker.py) (`fugashi` MeCab + `pykakasi` Hepburn Romaji) — chạy mất **< 50ms** cho toàn bộ bộ phim mà không tốn bất kỳ lệnh gọi LLM nào.

---

## 2. Các Hạng mục Đã Triển khai Hoàn tất (Nhóm A & Nhóm C)

### ✅ A2. Trích xuất Tự động Tên Nhân vật (`AutoCharacterExtractor` — `fugashi` + `pykakasi`)
- **File triển khai**: [`core/ja_qa_checker.py`](core/ja_qa_checker.py), [`core/character_registry.py`](core/character_registry.py), [`core/pipeline.py`](core/pipeline.py)
- **Cơ chế hoạt động**:
  - Quét toàn bộ dòng phụ đề tiếng Nhật ngay sau bước parse SRT bằng `fugashi` (nhận diện token `固有名詞/人名` và các danh từ đứng trước hậu tố `さん/ちゃん/くん/君/様/さま`).
  - Tự động loại bỏ danh từ chung/đại từ (`旦那さん`, `お母さん`, `彼氏`, `お疲れ様`, `ご馳走様`...).
  - Tự động phiên âm sang **Hepburn Romaji** chuẩn bằng `pykakasi` (`ミク`/`美久` $\rightarrow$ `Miku`, `つよし` $\rightarrow$ `Tsuyoshi`, `佐藤` $\rightarrow$ `Sato`, `川口` $\rightarrow$ `Kawaguchi`).
  - Tự động nạp vào `JaTranslator`, `Validator`, `RuleBasedPostProcessor` và `CharacterRegistry` (kèm so khớp mờ `rapidfuzz` để sửa các biến thể lệch như `Mikuru`/`Mikku` $\rightarrow$ `Miku`, `Tuyoshi` $\rightarrow$ `Tsuyoshi`).

### ✅ A3. Đưa Từ khóa (`hotwords`) vào Faster-Whisper
- **File triển khai**: [`core/transcriber.py`](core/transcriber.py)
- **Cơ chế hoạt động**: Tự động truyền các từ khóa tiếng Nhật dễ nghe nhầm đồng âm (`中出し`, `気持ちいい`, `気持ち良い`, `彼氏`, `彼女`, `旦那`, `主人`, `セフレ`, `不倫`, `浮気`, `婚約`) vào tham số `hotwords` của `Faster-Whisper` khi transcribe tiếng Nhật.

### ✅ C1. Bộ Kiểm định Chất lượng Chuyên biệt Tiếng Nhật (`JaQAChecker`)
- **File triển khai**: [`core/ja_qa_checker.py`](core/ja_qa_checker.py), [`core/pipeline.py`](core/pipeline.py), [`core/validator.py`](core/validator.py)
- **5 tầng kiểm tra & tự động sửa lỗi**:
  1. **Kiểm tra Đảo ngược Phủ định (`check_negation_mismatch`)**: Bắt chính xác câu gốc có `ない/ません/じゃない/ないで/ず` nhưng câu dịch thiếu từ phủ định tiếng Việt (*không, chưa, đừng, chẳng...*), đồng thời loại trừ các câu hỏi đuôi/phỏng đoán mang nghĩa khẳng định (`んじゃないですか`, `かもしれない`, `しか...ない`).
  2. **Làm sạch Rò rỉ Chữ Hán/Kana (`clean_cjk_leak`)**: Tự động dịch hoặc phiên âm Hepburn các từ CJK còn sót trong câu tiếng Việt (đã làm sạch 58 dòng lịch sử trên 20 dự án trong `translation.db`).
  3. **Kiểm tra Tuân thủ Từ điển (`check_glossary_violations`)**: Phát hiện dòng bỏ qua `target_term` bắt buộc.
  4. **Kiểm tra Độ dài Bất thường (`check_length_anomaly`)**: Phát hiện câu bị cắt cụt (`severe_truncation`) hoặc lặp ảo giác (`hallucination_long`).
  5. **Dịch lại Có Định hướng (`audit_and_fix_project`)**: Chỉ gọi LLM dịch lại đúng các dòng bị gắn cờ lỗi phủ định hoặc đứt câu kèm chỉ dẫn ngữ pháp rõ ràng.

### ✅ C2. Kiểm soát Ngưỡng Context Window (`guard_prompt_num_ctx`)
- **File triển khai**: [`core/ja_qa_checker.py`](core/ja_qa_checker.py), [`core/translator.py`](core/translator.py)
- **Cơ chế hoạt động**: Tự động ước lượng số token của mọi prompt trước khi gửi tới Ollama; nếu `prompt + num_predict` vượt quá `num_ctx` cấu hình (`4096`), tự động mở rộng `num_ctx` lên `6144` / `8192` để ngăn Ollama âm thầm cắt cụt System Prompt hoặc Glossary ở đầu.

### ✅ C3. Bộ Kiểm thử & Benchmark Chất lượng (`tests/benchmark_ja_quality.py`)
- **File triển khai**: [`tests/benchmark_ja_quality.py`](tests/benchmark_ja_quality.py)
- **Cách chạy**:
  ```bash
  python tests/benchmark_ja_quality.py
  python core/ja_qa_checker.py --clean-db
  ```

---

## 3. Hạng mục Dự kiến cho Phiên Tiếp theo (Nhóm B — Human Feedback Loop)

1. **B1. Học từ Bản dịch Đã sửa của Người dùng (Aegisub / SRT Import)**:
   - Cho phép import file `.srt` đã chỉnh sửa tay vào DB $\rightarrow$ tự động diff với bản dịch máy và lưu vào `translation_errors` (`error_type='human'`).
   - Cho `GlossaryMiner` ưu tiên học từ các cặp câu do người dùng sửa và đẩy vào hàng chờ duyệt `glossary_pending.yaml`.
2. **B2. Dynamic Few-shot Prompting từ Translation Memory (5.939 cặp câu)**:
   - Dùng `rapidfuzz` lấy 3–5 câu tương đồng nhất trong TM chèn làm ví dụ mẫu cho từng window.
