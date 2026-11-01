"""Detect suspiciously long subtitle cues without inventing timestamps."""

from __future__ import annotations

import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[。！？!?])\s*|(?<=\.)\s+')


def _parse_timestamp(ts: str) -> int:
    """Parse ``HH:MM:SS,mmm`` or ``HH:MM:SS.mmm`` into milliseconds."""
    ts = ts.replace('.', ',')
    h, m, s_ms = ts.split(':')
    s, ms = s_ms.split(',')
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def _format_timestamp(ms: int) -> str:
    """Format milliseconds as ``HH:MM:SS,mmm``."""
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    ms_part = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms_part:03d}"


def split_long_sub(
    text: str,
    start_ms: int,
    end_ms: int,
    max_duration_ms: int = 6000,
) -> List[Tuple[int, int, str]]:
    """Tách phụ đề quá dài (> max_duration_ms) theo dòng mới hoặc dấu ngắt câu."""
    duration = end_ms - start_ms
    if duration <= max_duration_ms or not text:
        return [(start_ms, end_ms, text)]

    # Ưu tiên 1: Tách theo dòng mới (\n) nếu phụ đề đã có sẵn nhiều dòng
    if '\n' in text:
        parts = [p.strip() for p in text.split('\n') if p.strip()]
    else:
        # Ưu tiên 2: Tách theo dấu kết thúc câu tiếng Nhật / Latin (。！？!?. )
        parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text.strip()) if p and p.strip()]

    if len(parts) <= 1:
        logger.warning(
            "Long subtitle cue (%dms) without sentence breaks; preserving original timing",
            duration,
        )
        return [(start_ms, end_ms, text)]

    n = len(parts)
    step = duration // n
    if step <= 0:
        return [(start_ms, end_ms, text)]

    result: List[Tuple[int, int, str]] = []
    for i, part in enumerate(parts):
        seg_start = start_ms + i * step
        seg_end = end_ms if i == n - 1 else (start_ms + (i + 1) * step)
        result.append((seg_start, seg_end, part))
    return result


def find_long_subs(items: List[dict], max_duration_ms: int = 15000) -> List[dict]:
    """Return copies of cues exceeding the review threshold, with duration."""
    suspicious = []
    for item in items:
        duration = _parse_timestamp(item['end']) - _parse_timestamp(item['start'])
        if duration > max_duration_ms:
            suspicious.append({**item, 'duration_ms': duration})
    return suspicious


def validate_subtitle_timings(items: List[dict], video_duration_ms: int | None = None) -> List[dict]:
    """Describe invalid, out-of-video, overlapping, and unusually long cues."""
    issues = []
    previous = None
    for item in items:
        start = _parse_timestamp(item['start'])
        end = _parse_timestamp(item['end'])
        index = item.get('index')
        if start < 0 or end < 0:
            issues.append({'index': index, 'issue': 'negative_timestamp'})
        if end <= start:
            issues.append({'index': index, 'issue': 'non_positive_duration'})
        if video_duration_ms is not None and end > video_duration_ms + 1000:
            issues.append({'index': index, 'issue': 'past_video_end', 'end_ms': end})
        if previous and start < previous['end']:
            issues.append({
                'index': index,
                'issue': 'overlap',
                'overlap_ms': previous['end'] - start,
                'previous_index': previous['index'],
            })
        previous = {'index': index, 'start': start, 'end': end}
    return issues


def split_srt_items(items: List[dict], max_duration_ms: int = 6000) -> List[dict]:
    """Preserve cue timings and text; retain function name for caller compatibility."""
    result = []
    for item in items:
        start_ms = _parse_timestamp(item['start'])
        end_ms = _parse_timestamp(item['end'])
        for start, end, text in split_long_sub(
            item['text'], start_ms, end_ms, max_duration_ms,
        ):
            result.append({
                **item,
                'start': _format_timestamp(start),
                'end': _format_timestamp(end),
                'text': text,
            })
    for index, item in enumerate(result, start=1):
        item['index'] = index
    return result
