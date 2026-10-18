"""Subtitle duration splitter — chia sub dài thành nhiều sub ngắn theo dấu câu.

Vấn đề (từ JUR-708.srt dòng 61): Whisper output 1 sub dài 9 phút
(00:05:10 --> 00:14:09) chứa 3-4 câu hội thoại. Pipeline dịch cả block
→ mất ngữ cảnh, LLM gộp thành 1 câu dài → sub hiển thị không tự nhiên.

Fix: trước khi lưu vào DB, split sub > max_duration theo dấu câu
(。 ！？.!?\n). Mỗi sub con có timestamp chia đều trong khoảng gốc.

Không gom sub — chỉ tách sub dài ra nhiều sub ngắn.
shortcut: max_duration=6s phù hợp cho sub thông thường. Whisper VAD
  thường chunk tự nhiên 3-5s, nên 6s là ngưỡng thận trọng. Khi cần
  sub ngắn hơn cho JAV, set 4s.
"""

from __future__ import annotations

import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)


# Dấu câu dùng để split (JA + VI + EN)
# Bao gồm cả 、 (JA comma) vì JA thường dùng nó như dấu phân cách câu
_SPLIT_PUNCT = re.compile(r'(?<=[。！？.!?、，\n])\s*')


def _parse_timestamp(ts: str) -> int:
    """Parse 'HH:MM:SS,mmm' hoặc 'HH:MM:SS.mmm' -> milliseconds."""
    ts = ts.replace('.', ',')
    h, m, s_ms = ts.split(':')
    s, ms = s_ms.split(',')
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def _format_timestamp(ms: int) -> str:
    """Format milliseconds -> 'HH:MM:SS,mmm'."""
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    ms_part = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms_part:03d}"


def split_long_sub(text: str, start_ms: int, end_ms: int, max_duration_ms: int = 6000) -> List[Tuple[int, int, str]]:
    """Chia sub dài thành nhiều sub ngắn theo dấu câu.

    Args:
        text: nội dung sub (có thể nhiều dòng với \\n)
        start_ms, end_ms: timestamp gốc (ms)
        max_duration_ms: ngưỡng thời lượng tối đa (mặc định 6000 = 6s)

    Returns:
        List các tuple (start_ms, end_ms, text) đã chia.
        Nếu sub <= max_duration: trả về [(start, end, text)] nguyên.
        Nếu sub > max_duration: chia theo dấu câu, mỗi sub con có timestamp
        chia đều trong khoảng gốc.

    Không làm gì nếu:
        - Sub <= max_duration (return 1 element)
        - Text không có dấu câu để split (return 1 element, warning log)
        - Text chỉ có 1 segment sau khi split
    """
    duration = end_ms - start_ms
    if duration <= max_duration_ms:
        return [(start_ms, end_ms, text)]

    # Split theo dấu câu, giữ nguyên dấu câu
    parts = _SPLIT_PUNCT.split(text.strip())
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) <= 1:
        # Không thể split, giữ nguyên
        logger.debug(
            f"Sub splitter: sub {start_ms}-{end_ms} ({duration}ms) "
            f"no split points, keeping as-is"
        )
        return [(start_ms, end_ms, text)]

    # Chia timestamp đều
    n = len(parts)
    step = duration // n
    result = []
    for i, part in enumerate(parts):
        seg_start = start_ms + i * step
        seg_end = start_ms + (i + 1) * step if i < n - 1 else end_ms
        result.append((seg_start, seg_end, part))

    logger.debug(
        f"Sub splitter: sub {start_ms}-{end_ms} ({duration}ms) "
        f"split into {n} parts"
    )
    return result


def split_srt_items(items: List[dict], max_duration_ms: int = 6000) -> List[dict]:
    """Apply split cho 1 list SRT items. Trả về list mới (không mutate input).

    Args:
        items: list các dict có keys 'index', 'start', 'end', 'text'
        max_duration_ms: ngưỡng (mặc định 6000)

    Returns:
        List mới với các sub đã được split. Index được re-number từ 1.
    """
    result = []
    for item in items:
        start_ms = _parse_timestamp(item['start'])
        end_ms = _parse_timestamp(item['end'])
        segments = split_long_sub(
            item['text'], start_ms, end_ms, max_duration_ms,
        )
        for seg_start, seg_end, seg_text in segments:
            result.append({
                'start': _format_timestamp(seg_start),
                'end': _format_timestamp(seg_end),
                'text': seg_text,
            })
    # Re-number index
    for i, item in enumerate(result, start=1):
        item['index'] = i
    return result
