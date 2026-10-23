"""Smoke test for WhisperTranscriber._is_noise_segment after JAV-aware tuning."""
import os
os.environ['HF_HUB_OFFLINE'] = '1'

import sys
sys.path.insert(0, '.')

from core.transcriber import WhisperTranscriber


class FakeSelf:
    pass


fn = WhisperTranscriber._is_noise_segment

# Cases should be KEPT (return False)
# JAV moans + Whisper artifacts that look like moan
# Note: 'ん' và 'ア' đơn lẻ vẫn bị drop (giữ behavior cũ) vì:
# - 'ん' đơn = Whisper hallucination phổ biến, không có ý nghĩa
# - 'ア' đơn = tiếng "a" đơn lẻ, JAV thật sự dùng "アッ"/"アー"/"アア"
keep_cases = [
    'あっ',        # 1-char + small tsu (JAV moan start)
    'んっ',
    'はぁ',
    'イク',        # 2 chars - orgasm
    'はぁはぁ',    # repeated breathing
    'あっあっ',
    'いくいく',
    'mm',          # breathing sound
    'ah',
    'uh',
    'Ah',
    'あああ',      # 3 lặp - kept (threshold now 4+)
    'ンッ',
    'ハァ',
    'ああ',        # 2 lặp - kept
    'えっ',
]

# Cases should be DROPPED (return True)
# Pure noise / artifacts
drop_cases = [
    'music',
    '♪',
    'アアアアア',      # 5 chars = 4 lặp - dropped
    'eeeeeeee',         # 8 lặp Latin
    'applause',
    'ウホッ',
    'silence',
    '💕',
    '...',
    '???',
    '123',
    'eeeee',            # 5 Latin = 4 lặp - dropped
    'あああああ',       # 5 katakana = 4 lặp - dropped
    'h',                # single Latin char
    'ん',               # single hiragana (Whisper hallucination)
    'ア',               # single katakana (single vowel noise)
    '',                 # empty
    '   ',              # whitespace only
]


print('=== Should be KEPT (False) ===')
fail_keep = 0
for c in keep_cases:
    r = fn(FakeSelf(), c)
    ok = not r
    if not ok:
        fail_keep += 1
    mark = 'OK' if ok else 'FAIL'
    print(f'  [{mark}] {c!r}')

print()
print('=== Should be DROPPED (True) ===')
fail_drop = 0
for c in drop_cases:
    r = fn(FakeSelf(), c)
    ok = bool(r)
    if not ok:
        fail_drop += 1
    mark = 'OK' if ok else 'FAIL'
    print(f'  [{mark}] {c!r}')

print()
total = fail_keep + fail_drop
print(f'Result: {fail_keep} keep failures, {fail_drop} drop failures (total {total})')
sys.exit(0 if total == 0 else 1)