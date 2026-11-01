# -*- coding: utf-8 -*-
"""Bộ kiểm thử & Benchmark chất lượng dịch JA->VI (Group A2, A3 & Group C1, C2, C3).

Kiểm tra:
1. AutoCharacterExtractor (fugashi + pykakasi): trích xuất tự động tên riêng không cần Worker A.
2. CharacterRegistry độc lập Worker A: tự động sửa Mikuru/Mikku -> Miku, Tuyoshi -> Tsuyoshi.
3. JaQAChecker:
   - Phát hiện đảo ngược phủ định (Negation Inversion: ない/ません/じゃない) và bỏ qua giả phủ định (んじゃないですか, かもしれない).
   - Phát hiện và làm sạch rò rỉ ký tự CJK/Kana (clean_cjk_leak).
   - Kiểm tra tuân thủ từ điển bắt buộc (Glossary Compliance).
   - Kiểm tra bất thường độ dài (Truncation / Hallucination).
4. Context Window Token Guard (guard_prompt_num_ctx).
5. Benchmark trên tập câu chuẩn (Gold Standard) & làm sạch toàn bộ lịch sử DB.
"""

import io
import os
import sys

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ja_qa_checker import (
    JaQAChecker,
    estimate_prompt_tokens,
    extract_character_names_from_lines,
    guard_prompt_num_ctx,
    ja_to_hepburn,
)
from core.character_registry import CharacterRegistry

FAIL = []


def check(cond: bool, label: str):
    print(('  [PASS] ' if cond else '  [FAIL] ') + label)
    if not cond:
        FAIL.append(label)


def main():
    print('=== 1. Test ja_to_hepburn (fugashi + pykakasi) ===')
    check(ja_to_hepburn('ミクさん') == 'Miku', f"ミクさん -> {ja_to_hepburn('ミクさん')!r}")
    check(ja_to_hepburn('美久') == 'Miku', f"美久 -> {ja_to_hepburn('美久')!r}")
    check(ja_to_hepburn('つよしさん') == 'Tsuyoshi', f"つよしさん -> {ja_to_hepburn('つよしさん')!r}")
    check(ja_to_hepburn('佐藤さん') == 'Sato', f"佐藤さん -> {ja_to_hepburn('佐藤さん')!r}")
    check(ja_to_hepburn('川口くん') == 'Kawaguchi', f"川口くん -> {ja_to_hepburn('川口くん')!r}")
    check(ja_to_hepburn('あゆみちゃん') == 'Ayumi', f"あゆみちゃん -> {ja_to_hepburn('あゆみちゃん')!r}")

    print('\n=== 2. Test AutoCharacterExtractor (KHÔNG cần Worker A) ===')
    sample_film_lines = [
        'ミクさん、おはよう。',
        'ミクお水持ってきて。',
        '彼氏のつよしさんですか？',
        'つよしさんは優しいね。',
        '佐藤美久です、よろしくお願いします。',
        '佐藤さん、ちょっといいですか。',
        '旦那さんは今日遅くなるかも。',
        'お母さんには内緒だよ。',
        '気持ちいいセックスしよう。',
    ]
    extracted = extract_character_names_from_lines(sample_film_lines)
    canonicals = {e['canonical'] for e in extracted}
    print('  Extracted characters:', [(e['canonical'], e['aliases'][:4]) for e in extracted])
    check('Miku' in canonicals, 'Trích xuất được tên riêng Miku (từ ミクさん / 美久)')
    check('Tsuyoshi' in canonicals, 'Trích xuất được tên riêng Tsuyoshi (từ つよしさん)')
    check('Sato' in canonicals, 'Trích xuất được họ Sato (từ 佐藤さん / 佐藤美久)')
    check(
        not ({'Danna', 'Okaa', 'Kareshi', 'Sekkusu'} & canonicals),
        'KHÔNG nhận nhầm danh từ chung (旦那さん, お母さん, 彼氏, セックス) làm tên riêng',
    )

    print('\n=== 3. Test CharacterRegistry hoạt động độc lập khi Worker A tắt ===')
    class DummyDBNoWorkerA:
        def list_context_windows(self, pid):
            return []  # Worker A tắt hoàn toàn
        def get_all_items(self, pid):
            return [{'original_text': ln, 'translated_text': '', 'sub_index': i}
                    for i, ln in enumerate(sample_film_lines, 1)]

    reg = CharacterRegistry(DummyDBNoWorkerA(), project_id=99, character_names=extracted)
    reg.build()
    fixed_miku = reg.validate_translation('ミクお水', 'Mikuru, nước uống đây.')
    check(fixed_miku == 'Miku, nước uống đây.', f"Sửa Mikuru -> Miku: {fixed_miku!r}")
    fixed_tsuyoshi = reg.validate_translation('彼氏のつよしさん', 'Bạn trai là anh Tuyoshi.')
    check(fixed_tsuyoshi == 'Bạn trai là anh Tsuyoshi.', f"Sửa Tuyoshi -> Tsuyoshi: {fixed_tsuyoshi!r}")

    print('\n=== 4. Test JaQAChecker (Phủ định, CJK Leak, Glossary, Độ dài) ===')
    glossary = [
        {'source_term': '彼氏', 'target_term': 'bạn trai'},
        {'source_term': '中出し', 'target_term': 'xuất tinh trong'},
    ]
    qa = JaQAChecker(glossary_terms=glossary, character_names=extracted)

    # 4a. Phủ định thật sự bị dịch ngược -> phải bắt được
    check(
        qa.check_negation_mismatch('私は絶対に否定しません', 'Tôi hoàn toàn đồng ý'),
        'Bắt được lỗi dịch ngược phủ định (否定しません -> khẳng định)',
    )
    check(
        not qa.check_negation_mismatch('私は絶対に否定しません', 'Tôi tuyệt đối không phủ nhận.'),
        'Chấp nhận câu dịch đúng nghĩa phủ định (không phủ nhận)',
    )
    # 4b. Giả phủ định (hỏi đuôi んじゃないですか / phỏng đoán かもしれない) -> KHÔNG báo lỗi giả
    check(
        not qa.check_negation_mismatch('昨日気持ち良かったんじゃないんですか', 'Hôm qua sướng lắm đúng không?'),
        'KHÔNG báo lỗi giả cho câu hỏi đuôi んじゃないんですか',
    )
    check(
        not qa.check_negation_mismatch('今日遅くなるかもしれない', 'Hôm nay có lẽ sẽ về muộn.'),
        'KHÔNG báo lỗi giả cho câu phỏng đoán かもしれない',
    )

    # 4c. Làm sạch rò rỉ chữ Hán / Kana (clean_cjk_leak)
    cleaned1 = qa.clean_cjk_leak('私は絶対に否定しません', 'Tôi绝对不会 phủ nhận.')
    check(cleaned1 == 'Tôi tuyệt đối không phủ nhận.', f"Clean CJK leak 1: {cleaned1!r}")
    cleaned2 = qa.clean_cjk_leak('あ、つやしが焼いたんだ', 'À,原来是 Tsuyoshi nướng đấy.')
    check(cleaned2 == 'À, thì ra là Tsuyoshi nướng đấy.', f"Clean CJK leak 2: {cleaned2!r}")

    # 4d. Kiểm tra vi phạm Glossary
    viol = qa.check_glossary_violations('彼氏と中出しした', 'Anh trai đã làm chuyện đó')
    check(len(viol) == 2, f"Bắt đủ 2 vi phạm glossary (彼氏, 中出し): {viol}")
    viol_ok = qa.check_glossary_violations('彼氏と中出しした', 'Bạn trai đã xuất tinh trong')
    check(len(viol_ok) == 0, 'Không báo lỗi khi đã tuân thủ đủ glossary')

    print('\n=== 5. Test Context Window Token Guard (Group C2) ===')
    short_prompt = 'Dịch câu sau: こんにちは'
    check(guard_prompt_num_ctx(short_prompt, 4096, 1024) == 4096, 'Prompt ngắn giữ nguyên num_ctx=4096')
    long_prompt = '日本語のテキストとベトナム語の翻訳ルール。\n' * 300
    expanded_ctx = guard_prompt_num_ctx(long_prompt, 4096, 1024)
    check(expanded_ctx >= 6144, f"Prompt dài (~{estimate_prompt_tokens(long_prompt)} tok) tự động mở rộng num_ctx={expanded_ctx}")

    print('\n=== 6. Benchmark trên Tập câu Chuẩn (Gold Standard JA->VI) ===')
    gold_benchmark = [
        ('私は絶対に否定しません', 'Tôi tuyệt đối không phủ nhận.'),
        ('この女を性処理の道具にしてやる', 'Tôi sẽ biến người phụ nữ này thành công cụ giải tỏa sinh lý.'),
        ('彼氏のつよしさん', 'Bạn trai là anh Tsuyoshi.'),
        ('ミクお水', 'Miku, nước uống đây.'),
        ('ミクさんには仲出ししたことないのに', 'Dù tôi chưa từng xuất tinh trong Miku lần nào.'),
        ('それに昨日気持ち良かったんじゃないんですか', 'Hơn nữa hôm qua chẳng phải cô cũng sướng lắm sao?'),
        ('あ、つやしが焼いたんだ', 'À, thì ra là Tsuyoshi nướng đấy.'),
    ]
    gold_pass = 0
    for ja_src, vi_gold in gold_benchmark:
        res = qa.inspect_line(ja_src, vi_gold)
        if res['ok']:
            gold_pass += 1
        else:
            print('  [!] Gold item flagged:', ja_src, '->', vi_gold, res)
    check(gold_pass == len(gold_benchmark), f"Gold Standard Benchmark đạt {gold_pass}/{len(gold_benchmark)} (100%)")

    if FAIL:
        print(f'\nFAIL ({len(FAIL)}):', FAIL)
        return 1
    print('\nTAT CA PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
