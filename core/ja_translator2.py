"""Japanese subtitle refiner — chạy SAU ZH pipeline để trau chuốt 20 dòng/lần.

Khác với ja_translator.py (3-pass cũ fail liên tục):
  - Dịch JA→VI bằng ZH pipeline trước (standard JSON pass)
  - ja_trans2 chỉ nhận output đã dịch, gom 20 dòng rồi polish lại

Flow:
  Pipeline chạy ZH → commit DB (status='completed')
  → ja_trans2: translate NULL items (vòng 2)
  → ja_trans2: refine completed items (polish)
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
        Số dòng mỗi batch (mặc định 20).
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

        result = [
            (out[i].strip() if out[i] else translated_lines[i])
            for i in range(n)
        ]
        return result

    def translate_batch(
        self,
        source_lines: List[str],
        temperature: float = 0.2,
    ) -> List[str]:
        """Translate một batch dòng chưa được dịch (JA → Vietnamese).

        Dùng cùng model/prompt style như pipeline chính.
        """
        if not source_lines:
            return []

        n = len(source_lines)

        prompt = f"""Translate {n} Japanese subtitles to Vietnamese.

TRANSLATION RULES:
- Keep natural, conversational Vietnamese suitable for subtitles
- Use informal/friendly tone suitable for casual conversation
- Keep character names as-is
- Preserve sentence-final particles emotion (ね/よ/ぞ/かな/っす/ etc.)
- Sino-Vietnamese false friends: 勉強 ≠ "cố gắng"; 汽車 ≠ "khí xa"

OUTPUT FORMAT: Return a valid JSON array with exactly {n} elements.
Each element: {{"id": <1-based number>, "text": "<Vietnamese translation>"}}
Only output JSON, nothing else. Do NOT skip any line.

SOURCE LINES:
"""
        for i, src in enumerate(source_lines, 1):
            prompt += f"{i}. {src}\n"

        try:
            raw = self.translator.generate(prompt, temperature=temperature)
        except Exception as e:
            logger.warning(f"JaTranslator2.translate_batch: generate failed: {e}")
            return source_lines

        parsed = _safe_extract_json(raw)
        if not isinstance(parsed, list) or len(parsed) != n:
            logger.warning(f"JaTranslator2.translate_batch: invalid JSON, keeping original")
            return source_lines

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

        return [
            (out[i].strip() if out[i] else source_lines[i])
            for i in range(n)
        ]

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
            f"{total_refine} items to refine"
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

                if translated_batch != batch:
                    translated += len(translated_batch)
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

                if refined_batch != trans_batch:
                    refined += len(refined_batch)
                else:
                    failed += len(refined_batch)

                if progress_callback:
                    done = total_translate + batch_end
                    progress_callback(done, total)

                logger.info(
                    f"JaTranslator2: refine batch {batch_start//self.batch_size + 1} "
                    f"({batch_start+1}-{batch_end}/{total_refine})"
                )

        logger.info(
            f"JaTranslator2: done — {translated}/{total_translate} translated, "
            f"{refined}/{total_refine} refined"
        )
        return {"total": total, "refined": refined, "translated": translated, "failed": failed}
