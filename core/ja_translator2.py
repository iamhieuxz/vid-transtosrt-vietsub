"""Japanese subtitle refiner — chạy SAU main pipeline để trau chuốt dòng đã dịch.

Key optimizations cho model nhỏ (8B) với context giới hạn:
  1. Compact prompt — ít tokens nhất có thể, không verbose explanation
  2. Batch size nhỏ — 5 dòng thay vì 10 cho refine (giảm context ~50%)
  3. Retry với simpler prompt — fallback prompt cực ngắn nếu JSON fail
  4. Chunk nhỏ khi retry — 3 dòng/cluster thay vì 5
  5. Graceful degradation — giữ nguyên nếu refine fail, không crash

Flow:
  Pipeline chạy main (JA→VI) → commit DB
  → ja_trans2: refine completed items (polish, 5 dòng/batch)
    - Fail? → retry với simpler prompt
    - Still fail? → giữ nguyên translated text gốc
"""

from __future__ import annotations

import logging
import re
import json
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Prompt — COMPACT: tối thiểu tokens, tối đa hiệu quả
# -----------------------------------------------------------------------------

# System prompt cực gọn, không verbose
_REFINE_SYSTEM = """Subtitle polisher: JA→VI. Fix only clear errors. Keep correct lines unchanged."""

# Template chính: dễ hiểu hơn, format rõ ràng
_REFINE_TEMPLATE = """You are a subtitle polisher. Fix errors in {n} Japanese-to-Vietnamese subtitle lines.

Common errors to fix:
• Romaji leaked: keep Vietnamese only
• Sino-Vietnamese wrong: 勉強=kiến thức, 汽車=xe lửa, 大丈夫=bình thường
• Missing nuance: ね/よ/ぞ = nhé/nha/mà

Do NOT rewrite correct lines. Keep length similar to input.
Return ONLY valid JSON array with {n} items. Format:
[{{"id":1,"text":"Vietnamese line 1"}},{{"id":2,"text":"Vietnamese line 2"}}]

{pairs}"""

# Fallback template: đơn giản hơn, chỉ dịch lại
_FALLBACK_TEMPLATE = """Translate {n} Japanese lines to Vietnamese. Keep it natural like movie subtitles.
Return ONLY valid JSON array with {n} items:
[{{"id":1,"text":"..."}},{{"id":2,"text":"..."}}]

Lines to translate:
{lines}"""

# Tiny fallback: 3 lines, no rules
_TINY_TEMPLATE = """Translate to Vietnamese (return JSON array):
[{{"id":1,"text":"..."}},{{"id":2,"text":"..."}},{{"id":3,"text":"..."}}]

Lines:
{lines}"""

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

_LEADING_NUM_RE = re.compile(r"^\s*\d+[\.\)]\s*")


def _strip_leading_number(line: str) -> str:
    return _LEADING_NUM_RE.sub("", line).strip()


def _build_pairs(source_lines: List[str], trans_lines: List[str]) -> str:
    """Gộp source + translated thành string compact có label để parse."""
    lines = []
    for i, (s, t) in enumerate(zip(source_lines, trans_lines), 1):
        # Rút gọn: bỏ khoảng trắng thừa, giới hạn độ dài
        s_short = s.strip()[:120] if len(s.strip()) > 120 else s.strip()
        t_short = t.strip()[:120] if len(t.strip()) > 120 else t.strip()
        lines.append(f"{i}. J: {s_short}\n   V: {t_short}")
    return "\n".join(lines)


def _build_lines_only(source_lines: List[str]) -> str:
    """Gộp source lines thành string compact."""
    return "\n".join(
        f"{i}|{s.strip()[:150]}" for i, s in enumerate(source_lines, 1)
    )


def _safe_extract_json(text: str) -> Optional[List[dict]]:
    """Deprecated wrapper — use core.json_utils.extract_json_array directly."""
    from .json_utils import extract_json_array
    return extract_json_array(text)


def _parse_json_response(parsed: List, n: int) -> Tuple[List[Optional[str]], bool]:
    """Deprecated wrapper — use core.json_utils.parse_json_response directly."""
    from .json_utils import parse_json_response
    return parse_json_response(parsed, n)


# -----------------------------------------------------------------------------
# JaTranslator2
# -----------------------------------------------------------------------------

class JaTranslator2:
    """Batch refiner cho JA→VI. Gom N dòng đã dịch, polish lại.

    Parameters
    ----------
    translator : TranslatorService
        Ollama translator.
    batch_size : int
        Số dòng mỗi batch (mặc định 5 — nhỏ hơn main pipeline để giảm context).
    """

    def __init__(self, translator, batch_size: int = 5):
        self.translator = translator
        self.batch_size = batch_size

    # -------------------------------------------------------------------------
    # Refine (main entry)
    # -------------------------------------------------------------------------

    def refine(
        self,
        source_lines: List[str],
        translated_lines: List[str],
        temperature: float = 0.2,
        max_retries: int = 3,
    ) -> List[str]:
        """Refine một batch (≤batch_size dòng). Trả về list đã trau chuốt.

        Strategy:
        1. Thử refine template chính (compact)
        2. Fail? → retry với simpler prompt + reset context
        3. Still fail? → fallback translate từng dòng đơn
        4. All fail? → giữ nguyên translated_lines gốc
        """
        if not translated_lines or len(translated_lines) != len(source_lines):
            return translated_lines

        n = len(translated_lines)
        consecutive_failures = 0

        for attempt in range(max_retries):
            # Strategy 1: Main compact refine
            result = self._refine_main(source_lines, translated_lines, temperature)
            if result is not None:
                if attempt > 0:
                    logger.info(f"JaTranslator2: succeeded on retry {attempt+1} ({n} lines)")
                return result

            consecutive_failures += 1
            logger.warning(f"JaTranslator2: attempt {attempt+1}/{max_retries} failed ({n} lines)")
            
            # Reset context between retries to avoid context overflow
            if hasattr(self.translator, 'reset_context'):
                self.translator.reset_context()

            # Strategy 2: Simpler fallback refine
            result = self._refine_fallback(source_lines, translated_lines, temperature)
            if result is not None:
                logger.info(f"JaTranslator2: fallback succeeded ({n} lines)")
                return result

        # All strategies failed → keep original
        logger.warning(f"JaTranslator2.refine: all strategies failed for {n} lines, keeping original")
        return translated_lines

    def _refine_main(
        self,
        source_lines: List[str],
        translated_lines: List[str],
        temperature: float,
    ) -> Optional[List[str]]:
        """Strategy 1: Main compact refine prompt."""
        n = len(translated_lines)
        pairs = _build_pairs(source_lines, translated_lines)
        prompt = _REFINE_TEMPLATE.format(n=n, pairs=pairs)

        try:
            raw = self.translator.generate(prompt, temperature=temperature)
        except Exception as e:
            logger.warning(f"  main refine generate error: {e}")
            return None

        # Handle empty response
        if not raw or not raw.strip():
            logger.warning(f"  main refine: EMPTY response from model!")
            # Reset context after empty response
            if hasattr(self.translator, 'reset_context'):
                self.translator.reset_context()
            return None

        # Debug: log raw response
        raw_len = len(raw)
        raw_preview = raw[:200].replace('\n', ' ')
        logger.debug(f"  main refine response ({raw_len} chars): {raw_preview}...")

        parsed = _safe_extract_json(raw)
        if not parsed:
            logger.warning(f"  main refine: JSON extraction failed")
            return None

        out, is_valid = _parse_json_response(parsed, n)
        if not is_valid:
            logger.debug(f"  main refine: valid={sum(1 for x in out if x)}/{n}")
            return None

        return [(out[i].strip() if out[i] else translated_lines[i]) for i in range(n)]

    def _refine_fallback(
        self,
        source_lines: List[str],
        translated_lines: List[str],
        temperature: float,
    ) -> Optional[List[str]]:
        """Strategy 2: Simpler prompt — giảm context, yêu cầu output đơn giản hơn."""
        n = len(translated_lines)
        lines = _build_lines_only(source_lines)
        prompt = _FALLBACK_TEMPLATE.format(n=n, lines=lines)

        try:
            raw = self.translator.generate(prompt, temperature=temperature)
        except Exception as e:
            logger.warning(f"  fallback refine error: {e}")
            return None

        # Debug: log raw response
        if raw:
            raw_len = len(raw)
            raw_preview = raw[:200].replace('\n', ' ')
            logger.info(f"  fallback response ({raw_len} chars): {raw_preview}...")
        else:
            logger.warning("  fallback: EMPTY response!")

        # Parse response — có thể là JSON array hoặc plain text
        parsed = _safe_extract_json(raw)
        if parsed:
            out, is_valid = _parse_json_response(parsed, n)
            if is_valid:
                return [(out[i].strip() if out[i] else translated_lines[i]) for i in range(n)]
            else:
                logger.info(f"  fallback: parsed {len(parsed)} items but is_valid=False")

        # Nếu không parse được JSON, thử extract từng dòng bằng regex
        # Response có thể dạng: "1. Dòng 1\n2. Dòng 2\n..."
        lines_out = self._extract_plain_lines(raw, n)
        if lines_out:
            return [
                (lines_out[i].strip() if i < len(lines_out) else translated_lines[i])
                for i in range(n)
            ]

        logger.warning(f"  fallback: all parse methods failed")
        return None

    def _extract_plain_lines(self, text: str, n: int) -> Optional[List[str]]:
        """Extract N lines từ plain text response."""
        if not text:
            return None

        # Thử tách theo dòng, bỏ số thứ tự
        raw_lines = text.strip().split('\n')
        results = []
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            # Bỏ "1. ", "1) ", "1|" prefix
            line = re.sub(r'^[\d]+[\.\)\|]\s*', '', line)
            line = line.strip('"-: ')
            if line:
                results.append(line)
            if len(results) >= n:
                break

        if len(results) == n:
            return results
        return None

    def _refine_tiny_chunks(
        self,
        source_lines: List[str],
        translated_lines: List[str],
        temperature: float,
    ) -> Optional[List[str]]:
        """Strategy 3: Tiny 3-line chunks — context cực nhỏ, ít fail nhất."""
        n = len(source_lines)
        chunk_size = 3
        result = [None] * n

        for i in range(0, n, chunk_size):
            chunk_src = source_lines[i:i + chunk_size]
            chunk_trans = translated_lines[i:i + chunk_size]
            chunk_n = len(chunk_src)

            lines_text = "\n".join(
                f"{j+1}. {s.strip()[:100]}"
                for j, s in enumerate(chunk_src)
            )
            prompt = _TINY_TEMPLATE.format(n=chunk_n, lines=lines_text)

            try:
                raw = self.translator.generate(prompt, temperature=temperature)
            except Exception:
                continue

            parsed = _safe_extract_json(raw)
            if parsed:
                out, is_valid = _parse_json_response(parsed, chunk_n)
                if is_valid:
                    for j in range(chunk_n):
                        result[i + j] = out[j].strip() if out[j] else chunk_trans[j]
                    continue

            # Fallback: extract plain lines
            plain = self._extract_plain_lines(raw, chunk_n)
            if plain:
                for j in range(chunk_n):
                    result[i + j] = plain[j] if j < len(plain) else chunk_trans[j]
                continue

            # All failed for this chunk → keep original
            for j in range(chunk_n):
                result[i + j] = chunk_trans[j]

        # Check if we got enough results
        valid_count = sum(1 for r in result if r is not None and r.strip())
        if valid_count >= n * 0.5:  # Ít nhất 50% thành công
            return [
                (result[i].strip() if result[i] else translated_lines[i])
                for i in range(n)
            ]
        return None

    # -------------------------------------------------------------------------
    # Translate (vòng 2 — dịch những dòng chưa được dịch)
    # -------------------------------------------------------------------------

    def translate_batch(
        self,
        source_lines: List[str],
        temperature: float = 0.2,
    ) -> List[str]:
        """Translate một batch dòng chưa được dịch (JA → Vietnamese).

        Dùng same model/prompt style như main pipeline, nhưng compact hơn.
        """
        if not source_lines:
            return []

        n = len(source_lines)

        # Compact prompt: JA → VI translation
        prompt = f"""Dịch {n} dòng phụ đề Japanese sang Vietnamese tự nhiên.
Giữ ngắn, tự nhiên như phụ đề phim.
{n} lines:
{_build_lines_only(source_lines)}"""

        try:
            raw = self.translator.generate(prompt, temperature=temperature)
        except Exception as e:
            logger.warning(f"JaTranslator2.translate_batch: generate failed: {e}")
            return source_lines

        parsed = _safe_extract_json(raw)
        if parsed:
            out, is_valid = _parse_json_response(parsed, n)
            if is_valid:
                return [(out[i].strip() if out[i] else source_lines[i]) for i in range(n)]

        # Fallback: plain line extraction
        plain = self._extract_plain_lines(raw, n)
        if plain:
            return [(plain[i].strip() if i < len(plain) else source_lines[i]) for i in range(n)]

        logger.warning(f"JaTranslator2.translate_batch: all strategies failed for {n} lines")
        return source_lines

    # -------------------------------------------------------------------------
    # Refine entire project
    # -------------------------------------------------------------------------

    def refine_project(self, db, project_id: int, progress_callback=None) -> dict:
        """Refine toàn bộ project.

        2 bước:
        1. Translate những dòng chưa dịch (NULL translated_text) — vòng 2
        2. Refine những dòng đã dịch — polish
        """
        items = db.get_all_items(project_id)

        # Phân loại: đã dịch vs chưa dịch
        pending_refine = []
        pending_translate = []
        for i, it in enumerate(items):
            trans = it.get('translated_text')
            if trans and trans.strip():
                pending_refine.append((i, it))
            else:
                pending_translate.append((i, it))

        if not pending_refine and not pending_translate:
            logger.info("JaTranslator2: no items to process")
            return {"total": 0, "refined": 0, "translated": 0, "failed": 0}

        total_refine = len(pending_refine)
        total_translate = len(pending_translate)
        total = total_refine + total_translate
        refined = 0
        translated = 0
        failed = 0

        logger.info(
            f"JaTranslator2: {total_translate} items to translate, "
            f"{total_refine} items to refine (batch_size={self.batch_size})"
        )

        # Bước 1: Translate những dòng chưa dịch
        if pending_translate:
            src_translate = [it['original_text'] for _, it in pending_translate]
            for batch_start in range(0, len(src_translate), self.batch_size):
                batch_end = min(batch_start + self.batch_size, len(src_translate))
                batch = src_translate[batch_start:batch_end]

                translated_batch = self.translate_batch(batch)
                for j, (global_idx, _) in enumerate(pending_translate[batch_start:batch_end]):
                    item_id = items[global_idx]['id']
                    db.update_item_translation(item_id, translated_batch[j])

                actually_translated = sum(
                    1 for o, s in zip(translated_batch, batch)
                    if o and o.strip() and o.strip() != s.strip()
                )
                if actually_translated > 0:
                    translated += actually_translated
                else:
                    failed += len(translated_batch)

                if progress_callback:
                    done = min(batch_end, total_translate) + total_refine
                    progress_callback(done, total)

                logger.info(
                    f"JaTranslator2: translate batch {batch_start//self.batch_size + 1} "
                    f"({batch_start+1}-{batch_end}/{total_translate})"
                )

        # Bước 2: Refine những dòng đã dịch
        if pending_refine:
            sources = [it['original_text'] for _, it in pending_refine]
            translated_list = [it['translated_text'] for _, it in pending_refine]

            for batch_start in range(0, total_refine, self.batch_size):
                batch_end = min(batch_start + self.batch_size, total_refine)
                src_batch = sources[batch_start:batch_end]
                trans_batch = translated_list[batch_start:batch_end]

                refined_batch = self.refine(src_batch, trans_batch)

                for j, (global_idx, _) in enumerate(pending_refine[batch_start:batch_end]):
                    item_id = items[global_idx]['id']
                    db.update_item_translation(item_id, refined_batch[j])

                actually_refined = sum(
                    1 for o, t in zip(refined_batch, trans_batch)
                    if o and t and o.strip() and o.strip() != t.strip()
                )
                if actually_refined > 0:
                    refined += actually_refined
                else:
                    failed += len(refined_batch)

                # Reset context between batches to avoid context overflow
                if hasattr(self.translator, 'reset_context'):
                    self.translator.reset_context()

                if progress_callback:
                    done = total_translate + batch_end
                    progress_callback(done, total)

                logger.info(
                    f"JaTranslator2: refine batch {batch_start//self.batch_size + 1} "
                    f"({batch_start+1}-{batch_end}/{total_refine}) — refined {actually_refined}/{len(refined_batch)}"
                )

        logger.info(
            f"JaTranslator2: done — {translated}/{total_translate} translated, "
            f"{refined}/{total_refine} refined"
        )
        return {"total": total, "refined": refined, "translated": translated, "failed": failed}
