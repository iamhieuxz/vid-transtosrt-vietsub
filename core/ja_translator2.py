"""Japanese subtitle refiner — chạy SAU ZH pipeline để trau chuốt 20 dòng/lần.

Khác với ja_translator.py (3-pass cũ fail liên tục):
  - Dịch JA→VI bằng ZH pipeline trước (standard JSON pass)
  - ja_trans2 chỉ nhận output đã dịch, gom 20 dòng rồi polish lại

Flow:
  Pipeline chạy ZH → commit DB (status='completed')
  → ja_trans2 chạy batch-refine trên output đã commit
  → ghi đè lại translated_text
"""

from __future__ import annotations

import logging
import re
import json
from typing import List, Optional

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Prompt
# -----------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert subtitle polisher for Japanese-to-Vietnamese translations.
Deep knowledge of 敬語 (keigo), 擬態語/擬声語 (onomatopoeia), common Sino-Vietnamese false friends,
and katakana loanwords.

Your job is to READ a batch of already-translated lines, compare with the original Japanese,
and refine the Vietnamese to sound like professional Vietnamese movie subtitles.
"""


_REFINE_TEMPLATE = """\
You are a subtitle polisher. You receive {n} pairs of (Japanese ORIGINAL → Vietnamese TRANSLATION).
Your task: read all pairs, then rewrite only the Vietnamese translations that need improvement.

PRIORITY FIXES:
1. Inconsistent keigo/formality → make pronouns consistent across all {n} lines
2. Romaji/katakana still present → replace with Vietnamese equivalent
3. Sino-Vietnamese wrong meaning (e.g. 勉強 ≠ "cố gắng"; 汽車 ≠ "khí xa")
4. Machine-translated tone → rewrite to sound natural in Vietnamese
5. Sentence-final particles (ね/よ/ぞ/かな) → convey the emotion in Vietnamese
6. Too short → expand naturally
7. Too long → trim to subtitle length

KEEP UNCHANGED:
- Character names, place names (or Vietnamese-subtitle style adaptations)
- Common katakana already popular in Vietnam (キャンセル, セットアップ, etc.)
- Lines that are already good — do NOT fix for the sake of fixing

OUTPUT FORMAT:
- Return a valid JSON array with exactly {n} elements.
- Each element: {{"id": <1-based number>, "text": "<refined Vietnamese>"}}
- Only output JSON, nothing else.
- Do NOT skip any line.
"""


_REFINE_SHORT_TEMPLATE = """\
You are a subtitle polisher. Same task as above, but only {n} lines instead of {batch} lines.

OUTPUT FORMAT:
- Return a valid JSON array with exactly {n} elements.
- Each element: {{"id": <1-based number>, "text": "<refined Vietnamese>"}}
- Only output JSON, nothing else.
"""


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

_LEADING_NUM_RE = re.compile(r"^\s*\d+[\.\)]\s*")


def _strip_leading_number(line: str) -> str:
    return _LEADING_NUM_RE.sub("", line).strip()


def _safe_extract_json(text: str):
    try:
        from json_repair import repair_json
        return json.loads(repair_json(text))
    except Exception:
        pass
    try:
        matches = re.findall(r"\[.*\]", text, re.DOTALL)
        for match in reversed(matches):
            try:
                parsed = json.loads(match)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
    except Exception:
        pass
    return None


# -----------------------------------------------------------------------------
# JaTranslator2
# -----------------------------------------------------------------------------

class JaTranslator2:
    """Batch refiner cho JA→VI. Gom 20 dòng đã dịch, polish lại.

    Parameters
    ----------
    translator : TranslatorService
        Ollama translator (dùng model ja hoặc default).
    batch_size : int
        Số dòng mỗi batch refine (mặc định 20).
    """

    def __init__(self, translator, batch_size: int = 20):
        self.translator = translator
        self.batch_size = batch_size

    def refine(
        self,
        source_lines: List[str],
        translated_lines: List[str],
        temperature: float = 0.2,
    ) -> List[str]:
        """Refine một batch (≤20 dòng). Trả về list đã trau chuốt.

        Falls back sang translated_lines gốc nếu refine fail.
        """
        if not translated_lines or len(translated_lines) != len(source_lines):
            return translated_lines

        n = len(translated_lines)

        # Build pairs block
        pairs = "\n".join(
            f"{i+1}. ORIGINAL: {s}\n   TRANSLATED: {t}"
            for i, (s, t) in enumerate(zip(source_lines, translated_lines))
        )

        if n == self.batch_size:
            prompt = _REFINE_TEMPLATE.format(n=n, batch=self.batch_size)
        else:
            prompt = _REFINE_SHORT_TEMPLATE.format(n=n, batch=self.batch_size)

        prompt += f"\nDANH SÁCH:\n{pairs}\n"

        try:
            raw = self.translator.generate(prompt, temperature=temperature)
        except Exception as e:
            logger.warning(f"JaTranslator2.refine: generate failed: {e}, keeping original")
            return translated_lines

        parsed = _safe_extract_json(raw)
        if not isinstance(parsed, list) or len(parsed) != n:
            logger.warning(f"JaTranslator2.refine: invalid JSON ({type(parsed).__name__}), keeping original")
            return translated_lines

        out: List[str] = [None] * n
        for item in parsed:
            if not isinstance(item, dict):
                continue
            idx = item.get("id")
            if isinstance(idx, (int, str)):
                try:
                    j = int(idx) - 1
                    if 0 <= j < n:
                        out[j] = item.get("text") or item.get("translation")
                except (ValueError, TypeError):
                    pass

        # Fill missing with original
        result = [
            (out[i].strip() if out[i] else translated_lines[i])
            for i in range(n)
        ]
        return result

    def refine_project(self, db, project_id: int, progress_callback=None) -> dict:
        """Refine toàn bộ project đã translate xong.

        Lấy tất cả items đã completed, gom thành batch {batch_size},
        refine mỗi batch, cập nhật lại translated_text.

        Parameters
        ----------
        db : Database
        project_id : int
        progress_callback : callable, optional
            Callback(total_done, total) cho progress bar.

        Returns
        -------
        dict
            {{"total": N, "refined": M, "failed": F}}
        """
        items = db.get_all_items(project_id)
        # Chỉ refine những dòng đã có translated_text
        pending = [
            (i, it)
            for i, it in enumerate(items)
            if it.get('translated_text') and it.get('translated_text').strip()
        ]
        if not pending:
            logger.info("JaTranslator2: no completed items to refine")
            return {"total": 0, "refined": 0, "failed": 0}

        total = len(pending)
        refined = 0
        failed = 0

        logger.info(f"JaTranslator2: refining {total} items in batches of {self.batch_size}")

        # Gom theo thứ tự index
        sources = [it['original_text'] for _, it in pending]
        translated = [it['translated_text'] for _, it in pending]

        for batch_start in range(0, total, self.batch_size):
            batch_end = min(batch_start + self.batch_size, total)
            src_batch = sources[batch_start:batch_end]
            trans_batch = translated[batch_start:batch_end]

            refined_batch = self.refine(src_batch, trans_batch)

            # Cập nhật DB
            for j, (global_idx, _) in enumerate(pending[batch_start:batch_end]):
                item_id = items[global_idx]['id']
                db.update_item_translation(item_id, refined_batch[j])

            if refined_batch != trans_batch:
                refined += len(refined_batch)
            else:
                failed += len(refined_batch)

            if progress_callback:
                progress_callback(batch_end, total)

            logger.debug(
                f"JaTranslator2: batch {batch_start//self.batch_size + 1} "
                f"({batch_start+1}-{batch_end}/{total}) refined"
            )

        logger.info(f"JaTranslator2: done — {refined}/{total} lines refined")
        return {"total": total, "refined": refined, "failed": total - refined}
