"""Live test: pipeline JA→VI với Ollama thật + JUR-708_whisper.srt."""
import sys
import time
import os

sys.path.insert(0, 'F:/vid-transtosrt-vietsub-clone')

from core.database import Database


def main():
    print("=" * 70)
    print("LIVE PIPELINE TEST — JUR-708 với Ollama thật (qwen3-8b)")
    print("=" * 70)

    srt_in = "JUR-708/JUR-708_whisper.srt"
    out_dir = "JUR-708"
    db_path = "_jur708.db"

    if os.path.exists(db_path):
        os.remove(db_path)

    # init fresh DB
    import core.database
    core.database.Database.__init__ = lambda self, db_path=db_path: (
        setattr(self, 'db_path', db_path) or self._init_db()
    )

    db = Database()
    with open(srt_in, encoding='utf-8-sig') as f:  # utf-8-sig for BOM
        content = f.read()
    n_subs = content.count("\n\n") + 1
    print(f"\n[*] Input: {srt_in}")
    print(f"[*] Total subs: {n_subs}")

    pid = db.create_project(
        name="JUR-708-live",
        source_lang="Tiếng Nhật (Japanese)",
        target_lang="Tiếng Việt (Vietnamese)",
        input_srt=srt_in,
        output_srt=out_dir,
        window_size=3,
        history_size=2,
        future_size=1,
    )

    config = {
        'project': {
            'source_lang': 'Tiếng Nhật (Japanese)',
            'target_lang': 'Tiếng Việt (Vietnamese)',
            'name': 'JUR-708-live',
        },
        'model': {
            'name': 'huihui_ai/qwen3-abliterated:8b-v2',
            'ollama_url': 'http://localhost:11434/api/generate',
            'temperature': 0.05,
            'repeat_penalty': 1.3,
            'num_ctx': 4096,
            'num_predict': 1024,
            'timeout': 300,
        },
        'models': {
            'uncen': {
                'name': 'huihui_ai/qwen3-abliterated:8b-v2',
                'ollama_url': 'http://localhost:11434/api/generate',
                'temperature': 0.05,
                'repeat_penalty': 1.3,
                'num_ctx': 4096,
                'num_predict': 1024,
                'timeout': 300,
            },
        },
        'pipeline': {
            'enable_glossary': True,
            'max_retries': 3,
            'retry_delay': 2,
            'num_workers': 1,
            'checkpoint_interval': 50,
            'heartbeat_timeout': 600,
            'circuit_breaker_threshold': 5,
            'circuit_breaker_cooldown': 60,
            'split_long_subs': False,
            'sub_max_duration_ms': 6000,
        },
        'window': {'size': 3, 'history': 2, 'future': 1},
        'ja_context': {'enabled': False, 'window_size': 18},
        'ja_trans2': {'enabled': False, 'batch_size': 5},
        'translation': {'mode': 'uncen', 'literal_mode': False},
    }

    print("[*] Loading pipeline + Ollama...")
    from core.pipeline import TranslationPipeline

    t0 = time.time()
    pl = TranslationPipeline(config)
    print(f"[+] Pipeline ready in {time.time() - t0:.1f}s")

    # Skip Worker A (ja_context disabled)
    class MockContextAnalyzer:
        context_window_size = 18
        def analyze_project(self, pid, db):
            return {"total": 0, "completed": 0, "failed": 0, "skipped": 0}
    pl.ja_context_analyzer = MockContextAnalyzer()

    print("[*] Running pipeline (this will take a while)...")
    t0 = time.time()
    try:
        pl.run(pid)
    except Exception as e:
        print(f"[-] Error: {e}")
        import traceback
        traceback.print_exc()
    elapsed = time.time() - t0
    print(f"\n[+] Pipeline finished in {elapsed:.1f}s")

    # Stats
    items = db.get_all_items(pid)
    translated = [it for it in items if it.get("translated_text")]
    failed = [it for it in items if it.get("status") == "failed"]

    print(f"\n[+] Translated: {len(translated)}/{len(items)} items")
    print(f"[-] Failed: {len(failed)}")

    if translated:
        # Quality snapshot: first 5
        print("\n" + "=" * 70)
        print("FIRST 5 OUTPUTS")
        print("=" * 70)
        for it in translated[:5]:
            orig = (it.get('original_text') or '').replace('\n', ' ')
            vi = (it.get('translated_text') or '').replace('\n', ' ')
            print(f"\n[{it['sub_index']:>3}] JA: {orig!r}")
            print(f"      VI: {vi!r}")

        # Check for issues
        print("\n" + "=" * 70)
        print("QUALITY CHECK")
        print("=" * 70)
        all_vi = "\n".join(it.get('translated_text', '') or '' for it in translated)

        checks = [
            ('Không có raw JSON leak',
             not any(s in all_vi for s in ['{"id":', '[{"id":'])),
            ('Không romaji leak (arigatou/kawaii)',
             not any(w in all_vi.lower() for w in [' arigatou ', ' kawaii ', ' onegai '])),
            ('Không lỗi "vâng người"',
             'vâng người' not in all_vi),
            ('Không "cố gắng" từ 勉強 (Sino-VN trap)',
             'cố gắng' not in all_vi),
            ('Có kanji/katakana Nhật leak (tín hiệu LLM lỗi)',
             not any('\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff' for c in all_vi)),
        ]

        for name, ok in checks:
            print(f"  [{('OK' if ok else 'FAIL')}]  {name}")

    # Cleanup DB
    if os.path.exists(db_path):
        os.remove(db_path)


if __name__ == "__main__":
    sys.exit(main())