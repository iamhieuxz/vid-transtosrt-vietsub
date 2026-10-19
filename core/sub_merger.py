"""Sub title merger — merge sub trống/dài bất thường trước khi dịch.

Whisper có thể tạo:
- Sub trống dài 30s+ (nhạc nền, im lặng) — sub_splitter không split được
- Sub có text rất ngắn (1-2 chars) lặp lại nhiều lần, kéo dài 70s+ (Whisper lặp)
- Sub có duration bất thường > 30s (audio ồn, Whisper gộp nhiều câu)

Logic:
- Nếu sub text trống + duration > 10s → merge với sub kế tiếp
- Nếu sub text là 1 romaji/kana đơn (1-2 chars JA) + duration > 15s → merge với sub trước
- Nếu sub duration > 30s + text < 5 chars → merge với sub kế (tránh sub rỗng dài)

Sau khi merge, pipeline main chạy sub_splitter bình thường để chia sub > 6s.
"""

from __future__ import annotations

import re
import logging
from typing import List

logger = logging.getLogger(__name__)


# Pattern: romaji/kana ngắn (1-2 chars kana) — Whisper hay để sót
_SHORT_KANA_RE = re.compile(r'^[\u3040-\u309f\u30a0-\u30ff]{1,3}[!?。]?$')


def _parse_time(t: str) -> float:
    """Parse time string 'HH:MM:SS,mmm' hoặc 'HH:MM:SS.mmm' ra giây."""
    t = t.replace(',', '.')
    parts = t.split(':')
    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2])
    return h * 3600 + m * 60 + s


def _format_time(t: float) -> str:
    """Format giây → 'HH:MM:SS,mmm'."""
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace('.', ',')


def _get_duration(start: str, end: str) -> float:
    return _parse_time(end) - _parse_time(start)


def merge_srt_items(items: list, max_empty_dur_s: float = 10.0,
                    short_kana_min_dur_s: float = 15.0,
                    long_empty_min_dur_s: float = 30.0) -> list:
    """Merge sub bất thường trong danh sách items (in-place logic, return new list).

    items: [{'index': 1, 'start': '00:00:01,000', 'end': '00:00:03,000', 'text': '...'}, ...]

    Trả về list mới (không mutate input) đã merge.
    """
    if not items:
        return items

    # Bước 0: gom các sub trùng text liên tiếp (Whisper lặp cùng 1 cụm từ)
    # VD: 塩? / 塩? / 塩? → 1 sub duy nhất
    deduped = []
    i = 0
    while i < len(items):
        cur = items[i]
        cur_text = (cur.get('text') or '').strip()
        # Nếu text ngắn (1-3 chars) và sub tiếp theo có cùng text → gom
        if 1 <= len(cur_text) <= 3 and _SHORT_KANA_RE.match(cur_text):
            j = i + 1
            while j < len(items):
                next_text = (items[j].get('text') or '').strip()
                if next_text == cur_text:
                    j += 1
                else:
                    break
            if j > i + 1:
                # Có >= 2 sub lặp → gom thành 1
                last = items[j - 1]
                merged_text = cur_text
                merged = {
                    **cur,
                    'end': last['end'],
                }
                # Nếu text ngắn quá và lặp > 1, giữ nguyên 1 dòng (text lặp không có ý nghĩa)
                # Hoặc giữ text gốc nếu chỉ 1 lần
                merged['text'] = cur_text if j == i + 1 else cur_text
                deduped.append(merged)
                logger.debug(
                    f"SubMerger: dedup {j - i} identical short-kana subs "
                    f"({cur['index']}..{last['index']})"
                )
                i = j
                continue
        deduped.append(cur)
        i += 1
    items = deduped

    # Bước 1: gom các sub cần merge (chỉ merge sub rỗng/ngắn với sub kế/trước)
    merged = []
    i = 0
    while i < len(items):
        item = items[i]
        text = (item.get('text') or '').strip()
        dur = _get_duration(item['start'], item['end'])

        # Case 1: sub text trống + duration > 10s → skip, để sub kế merge vào sub trước
        if not text and dur > max_empty_dur_s:
            logger.debug(f"SubMerger: skipping empty sub {item['index']} ({dur:.1f}s)")
            i += 1
            continue

        # Case 2: sub text ngắn (1-3 chars kana) + duration > 15s → merge với sub kế
        if text and _SHORT_KANA_RE.match(text) and dur > short_kana_min_dur_s:
            if i + 1 < len(items):
                next_item = items[i + 1]
                merged.append({
                    **item,
                    'text': (item['text'] + '\n' + next_item['text']).strip(),
                    'end': next_item['end'],
                })
                logger.debug(
                    f"SubMerger: merged sub {item['index']} ({dur:.1f}s, kana) "
                    f"-> sub {next_item['index']}"
                )
                i += 2
                continue

        # Case 3: sub dài > 30s + text ngắn < 5 chars (rất có thể là rỗng/lặp)
        # → merge với sub kế nếu có
        if dur > long_empty_min_dur_s and len(text) < 5:
            if i + 1 < len(items):
                next_item = items[i + 1]
                merged.append({
                    **item,
                    'text': (item['text'] + '\n' + next_item['text']).strip(),
                    'end': next_item['end'],
                })
                logger.debug(
                    f"SubMerger: merged long-empty sub {item['index']} ({dur:.1f}s) "
                    f"-> sub {next_item['index']}"
                )
                i += 2
                continue

        merged.append(item)
        i += 1

    # Re-index
    for new_idx, item in enumerate(merged, 1):
        item['index'] = new_idx

    if len(merged) != len(items):
        logger.info(
            f"SubMerger: {len(items)} -> {len(merged)} subs "
            f"(removed {len(items) - len(merged)} bất thường)"
        )
    return merged
