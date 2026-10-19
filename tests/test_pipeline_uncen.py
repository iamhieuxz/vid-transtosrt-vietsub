"""Pipeline integration test: SRT JA uncen → SRT VI với full mock LLM.

Mục đích:
- Verify pipeline xử lý đúng dữ liệu uncen JA end-to-end (SRT in → SRT out)
- Verify post-processor được áp dụng
- Verify keigo + glossary được respect
- Không cần Ollama thật — full mock ở cả pipeline level.

Test scenarios (trong test-uncen.srt):
  - Keigo (先輩, おはようございます) → honorific translation
  - Particles (ね, よ, の)
  - Explicit (セックス, 中に出して) → honest translation
  - Sino-VN trap (勉強 → "học" KHÔNG "cố gắng")
  - Loan words (BMW)
  - Counters, casual (やだ, びっくり)
"""
import os
import sys
import re

sys.path.insert(0, 'F:/vid-transtosrt-vietsub-clone')

from core.database import Database
from core.pipeline import TranslationPipeline


# Mock LLM trả về bản dịch "lý tưởng" — giả định LLM hiểu system prompt.
# Mục tiêu test: pipeline logic + post-processor, không phải chất lượng LLM.
class MockIdealLLM:
    def __init__(self):
        # JA → VI ideal translation, hardcoded for each subtitle
        self.expected_vi = {
            1: "Ồ, Takumi tiền bối, chào buổi sáng ạ.",
            2: "À, khát nước quá... cho tôi xin một ly nước.",
            3: "Nè, quan hệ tình dục thế này, sướng quá...",
            4: "Thúc sâu hơn nữa đi, tiền bối.",
            5: "Aaa... chỗ đó sướng quá.",
            6: "Muốn cùng nhau sung sướng...",
            7: "Sắp ra rồi... không nhịn được nữa.",
            8: "Xuất vào trong cũng được... không sao đâu.",
            9: "Lúc nãy con đang học hả? Bài khó lắm hả?",
            10: "Ừ, ngày mai có bài kiểm tra mà.",
            11: "Nè, cái xe đó bao nhiêu tiền? BMW hả?",
            12: "Ôi, giật mình quá...",
            13: "Hừm... hóa ra là vậy, hiểu rồi.",
            14: "Thỉnh thoảng có thất bại cũng không sao đâu nhỉ.",
            15: "Haa, cảm ơn... cậu thật sự hiền quá đấy nhỉ.",
        }

    def _vi_for_index(self, idx):
        return self.expected_vi.get(idx, f"[VI sub {idx}]")

    def try_recover_circuit_breaker(self): return True
    def reset_circuit_breaker(self): pass
    def warmup(self, timeout=300): return True
    def reset_context(self): return True

    def generate(self, prompt, temperature=None, num_predict=None, _retries=None):
        """Trả JSON đúng số items trong prompt."""
        # Find n items in prompt
        # Literal pass: "1|source line"
        # Standard pass: "1. source line" hoặc numbered list
        nums = re.findall(r'^\s*(\d+)[\.|]\s*\S', prompt, re.MULTILINE)
        if not nums:
            nums = re.findall(r'^(\d+)\|', prompt, re.MULTILINE)
        if not nums:
            # JSON-mode ja-translator uses "id: N" + alignment: count "ORIGINAL:" lines
            if "ORIGINAL:" in prompt:
                nums = re.findall(r'ORIGINAL:\s*[^\n]+', prompt)
            else:
                nums = ["1"]
        # Dedupe, keep first
        nums_seen = []
        for n in nums:
            try:
                ni = int(re.search(r'\d+', n).group())
                if ni not in nums_seen:
                    nums_seen.append(ni)
            except Exception:
                continue
        if not nums_seen:
            nums_seen = [1]
        items = [{"id": n, "text": self._vi_for_index(n)} for n in nums_seen]
        return ('[' + ','.join(
            '{"id":' + str(it['id']) + ',"text":' +
            json.dumps(it['text'], ensure_ascii=False) + '}'
            for it in items
        ) + ']')

    def extract_json(self, text):
        import json
        try:
            return json.loads(text)
        except Exception:
            try:
                from json_repair import repair_json
                return json.loads(repair_json(text))
            except Exception:
                return None

    def fallback_translate(self, source_lines, src_lang, tgt_lang, glossary_terms=None):
        return [self._vi_for_index(i + 1) for i in range(len(source_lines))]


import json


def main():
    print("=" * 70)
    print("PIPELINE INTEGRATION TEST — uncen JA → VI")
    print("=" * 70)

    db_path = "_test_uncen.db"
    srt_in = "test-uncen/test-uncen.srt"
    srt_out_dir = "test-uncen"

    if os.path.exists(db_path):
        os.remove(db_path)

    import core.database
    core.database.Database.__init__ = lambda self, db_path=db_path: (
        setattr(self, 'db_path', db_path) or self._init_db()
    )

    db = Database()
    with open(srt_in, encoding='utf-8') as f:
        content = f.read()
    n_subs = content.count("\n\n") + 1
    print(f"\n[*] Input: {srt_in} ({n_subs} subtitle items)")

    pid = db.create_project(
        name="test-uncen-pipeline",
        source_lang="Tiếng Nhật (Japanese)",
        target_lang="Tiếng Việt (Vietnamese)",
        input_srt=srt_in,
        output_srt=srt_out_dir,
        window_size=4,
        history_size=2,
        future_size=1,
    )
    print(f"[+] Project created: pid={pid}")

    config = {
        'project': {
            'source_lang': 'Tiếng Nhật (Japanese)',
            'target_lang': 'Tiếng Việt (Vietnamese)',
            'name': 'test-uncen-pipeline',
        },
        'model': {
            'name': 'mock', 'ollama_url': 'http://localhost:0',
            'temperature': 0.1, 'num_ctx': 2048, 'num_predict': 512,
            'timeout': 30, 'repeat_penalty': 1.2,
        },
        'pipeline': {
            'enable_glossary': True, 'max_retries': 1, 'num_workers': 1,
            'checkpoint_interval': 20, 'retry_delay': 1,
            'heartbeat_timeout': 600,
            'circuit_breaker_threshold': 5,
            'circuit_breaker_cooldown': 60,
        },
        'window': {'size': 4, 'history': 2, 'future': 1},
        'ja_trans2': {'enabled': False, 'batch_size': 5},
        'ja_context': {'enabled': False, 'window_size': 18},
        'translation': {'mode': 'uncen', 'literal_mode': False},
    }

    print("[*] Loading pipeline (mock LLM)...")
    pl = TranslationPipeline(config)
    mock = MockIdealLLM()
    pl.translator = mock
    # Critical: JaTranslator captured self.translator at init, swap it too.
    if pl.ja_translator is not None:
        pl.ja_translator.translator = mock

    # Skip Worker A
    class MockContextAnalyzer:
        context_window_size = 18
        def analyze_project(self, pid, db):
            return {"total": 0, "completed": 0, "failed": 0, "skipped": 0}
    pl.ja_context_analyzer = MockContextAnalyzer()

    print("[*] Running pipeline...")
    print("-" * 70)

    try:
        pl.run(pid)
    except Exception as e:
        print(f"[-] Pipeline error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print("-" * 70)

    items = db.get_all_items(pid)
    translated = [it for it in items if it.get("translated_text")]

    print(f"\n[+] Translated: {len(translated)}/{len(items)} items")
    print("\n" + "=" * 70)
    print("OUTPUT SAMPLE")
    print("=" * 70)
    for it in items:
        original = (it.get("original_text") or "").replace("\n", " ")
        translated_text = (it.get("translated_text") or "[EMPTY]").replace("\n", " ")
        print(f"\n[{it['sub_index']:>2}] JA: {original!r}")
        print(f"     VI: {translated_text!r}")

    # Verification
    print("\n" + "=" * 70)
    print("VERIFICATION")
    print("=" * 70)

    all_vi = "\n".join(it.get("translated_text", "") or "" for it in items)

    checks = [
        ("Tất cả 15 item đã được dịch",
         len(translated) == 15),
        ("Không có romaji leak (arigatou/kawaii)",
         not any(w in all_vi.lower() for w in [" arigatou ", "kawaii "])),
        ("Không lỗi 'vâng người' typo",
         "vâng người" not in all_vi),
        ("Keigo honored (先輩 → 'tiền bối')",
         "tiền bối" in all_vi),
        ("Explicit 'セックス' → 'quan hệ tình dục' (không dùng từ né tránh)",
         "quan hệ tình dục" in all_vi),
        ("'気持ちいい' → 'sướng quá' (không dịch 'dễ chịu')",
         "sướng quá" in all_vi),
        ("Sino-VN '勉強' → 'học' (KHÔNG 'cố gắng')",
         "cố gắng" not in all_vi and "học" in all_vi),
        ("Counter/loanword 'BMW' giữ nguyên",
         "BMW" in all_vi),
        ("Particle 'ね' → 'nhỉ' (không bỏ sót)",
         "nhỉ" in all_vi),
    ]

    for name, passed in checks:
        icon = "[OK]" if passed else "[FAIL]"
        print(f"  {icon}  {name}")

    if os.path.exists(db_path):
        os.remove(db_path)

    all_passed = all(c[1] for c in checks)
    print(f"\n{'=' * 70}")
    print(f"RESULT: {'ALL PASSED ✓' if all_passed else 'SOME FAILED ✗'}")
    print(f"{'=' * 70}")
    print(f"\nNote: Mock LLM trả 'lý tưởng'. Test này chỉ verify pipeline logic.")
    print(f"      Để test chất lượng thật của model, cần Ollama chạy + GUI mode.")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
