"""Shared JSON extraction helpers used across the translation pipeline.

Three callers previously had their own copy of the same logic with subtle
differences (greedy match order, error reporting). Unifying here so behavior
is consistent and the regex / fallback strategy lives in one place.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

from json_repair import repair_json


def extract_json_array(text: str) -> Optional[list]:
    """Extract a JSON array from model output.

    Strategy:
      1. json_repair on the whole text (handles truncated, malformed JSON).
      2. Greedy regex to find the LARGEST bracketed JSON array.

    Returns:
        Parsed list (non-empty) on success, None if no valid array found or
        the parsed array is empty (empty arrays are usually false positives
        from `[model artifact] [response body]` patterns).
    """
    if not text or not text.strip():
        return None

    def _ok(parsed):
        return isinstance(parsed, list) and len(parsed) > 0

    # Method 1: json_repair
    try:
        parsed = json.loads(repair_json(text))
        if _ok(parsed):
            return parsed
    except Exception:
        pass

    # Method 2: Greedy — try longest array first (avoid matching empty `[]`)
    try:
        # Use re.DOTALL so JSON spans newlines. Non-greedy `\[.*?\]` would
        # match the FIRST `[]` (often empty), so we instead find all candidates
        # and try from longest to shortest.
        matches = re.findall(r"\[.*?\]", text, re.DOTALL)
        # Filter out empty arrays and trivial `[]` (often false positives).
        candidates = [m for m in matches if m.strip() not in ("[]", "[ ]")]
        for match in sorted(candidates, key=len, reverse=True):
            try:
                parsed = json.loads(match)
                if _ok(parsed):
                    return parsed
            except Exception:
                # Try with json_repair as a last resort for this segment
                try:
                    parsed = json.loads(repair_json(match))
                    if _ok(parsed):
                        return parsed
                except Exception:
                    continue
    except Exception:
        pass

    return None


def parse_json_response(parsed: List, n: int) -> tuple[List[Optional[str]], bool]:
    """Parse JSON response into a list aligned with source positions.

    Each item in `parsed` should have an `id` (1-based) and a text/translation
    field. Out-of-range ids are ignored.

    Returns:
        (list of texts or None, is_valid=True iff n items extracted successfully)
    """
    out: List[Optional[str]] = [None] * n
    valid_count = 0

    for item in parsed:
        if not isinstance(item, dict):
            continue
        idx = item.get("id")
        text = item.get("text") or item.get("translation") or item.get("vietnamese")
        if text is None:
            continue
        try:
            j = int(idx) - 1 if isinstance(idx, (int, str)) else None
        except (ValueError, TypeError):
            j = None
        if j is not None and 0 <= j < n:
            out[j] = text.strip()
            valid_count += 1

    is_valid = valid_count == n
    return out, is_valid