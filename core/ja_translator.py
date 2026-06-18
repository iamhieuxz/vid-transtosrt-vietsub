"""Japanese → Vietnamese subtitle translator (keigo-aware, 3-pass).

Module riêng cho tiếng Nhật — KHÔNG động vào pipeline zh-vi hiện tại.
Được gọi tùy chọn bởi ``TranslationPipeline`` khi ``project.source_lang``
là ``Japanese`` (hoặc ``tiếng Nhật`` / ``ja``).

3 pass:
  1. ``literal_translate``  — dịch thô, giữ keigo + onomatopoeia + particles
  2. ``polish_translate``   — trau chuốt, nhất quán xưng hô, mượt như phim VN
  3. ``qa_review``          — phát hiện drift (lệch nghĩa, sót romaji/kana)

Mỗi hàm nhận một instance ``TranslatorService`` (đã được khởi tạo với model
phù hợp cho tiếng Nhật) để gọi ``self.translator.generate(prompt, ...)``.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Prompt templates
# -----------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Bạn là chuyên gia dịch phụ đề phim/show từ tiếng Nhật sang tiếng Việt.
Phong cách tự nhiên, dễ hiểu, đúng giọng nhân vật.

ĐẶC THÙ TIẾNG NHẬT (BẮT BUỘC NẮM):
- 敬語 (keigo): phân biệt rõ 敬体 (です/ます) ↔ 常体 (だ/する) ↔ 謙譲語 ↔ 尊敬語.
  Vai lịch sự → "anh/chị/quý vị"; vai thân mật → "tớ/cậu/tao-mày". NHẤT QUÁN xuyên suốt phim.
- 終助詞 (sentence-final particles ね/よ/わ/ぞ/かな/さ): phải truyền tải cảm xúc sang tiếng Việt,
  không bỏ sót. VD: だろうね → "chắc là vậy nhỉ", よ → nhấn mạnh/khẳng định.
- 擬態語 / 擬声語 (きらきら, わくわく, ドキドキ, ガタガタ): PHẢI dịch nghĩa sang tiếng Việt
  tương ứng (lấp lánh, hồi hộp, thình thình, lạch cạch...). KHÔNG giữ nguyên romaji/kana.
- 同形異義漢字 vs Hán Việt: VD 勉強 = "học" (KHÔNG phải "cố gắng" theo nghĩa Hán Việt gốc),
  汽車 = "xe lửa" (KHÔNG phải "khí xxa"), 階段 = "cầu thang".
- Kana-only tên nhân vật hư cấu (Tenten, Kokoro...): giữ nguyên hoặc phiên âm sang tiếng Việt
  theo phong cách anime-sub VN phổ biến.
- Đếm từ 個/本/人/匹... thường được lược bỏ hoặc Việt hóa (VD: 三人 → "ba người").
- Câu tin nhắn/chat lược (省略): thêm từ cho đủ nghĩa khi dịch sang tiếng Việt.
- Tiếng Anh lẫn trong câu (カタカナ外来語, VD キャンセル, セットアップ): giữ nguyên nếu phổ biến ở VN
  hoặc dịch theo nghĩa khi cần tự nhiên hơn.
"""


_LITERAL_TEMPLATE = """\
{system}

Bản dịch "nguyên văn" từng câu, ưu tiên giữ đúng cấu trúc & cảm xúc gốc.

CHỈ DỊCH, KHÔNG trau chuốt.
- Xưng hô: ghi chú rõ dạng keigo gặp phải (trong đầu) để bước sau chọn từ xưng hô VN nhất quán.
- Onomatopoeia: dịch sát nghĩa ra tiếng Việt (xem system).
- Particles cuối câu: truyền tải cảm xúc, không bỏ.

QUY TẮC ĐẦU RA:
- Trả về đúng số dòng bằng số dòng đầu vào, KHÔNG thêm dòng thừa.
- Mỗi dòng chỉ chứa bản dịch tương ứng, không đánh số, không ghi chú.
- Giữ nguyên xuống dòng giữa các dòng đầu vào.
- KHÔNG viết lại nguyên văn câu gốc, KHÔNG thêm giải thích.

TUYỆT ĐỐI KHÔNG:
- Bỏ sót hoặc gộp nhiều dòng thành một.
- Trả về câu gốc khi chưa dịch.
- Dùng ký tự lạ (emoji, ký tự control) trong bản dịch.
{glossary_block}
Các dòng cần dịch (đã đánh số):
{lines}
"""


_POLISH_TEMPLATE = """\
{system}

Bản dịch "thô" (ROUGH) cần được trau chuốt cho tự nhiên, nhất quán, mượt như phụ đề phim Việt.

ƯU TIÊN TRAU CHUỐT:
1. Nhất quán xưng hô xuyên suốt (dựa trên danh sách các dạng keigo đã thấy ở bước literal).
2. Câu thoại nghe như người Việt nói, không "dịch máy".
3. Giữ đúng sắc thái cảm xúc (ngạc nhiên, giận, mỉa mai...).
4. Nếu ROUGH còn sót romaji/kana onomatopoeia, dịch lại sang tiếng Việt.

QUY TẮC ĐẦU RA:
- Trả về JSON array hợp lệ, không kèm text ngoài JSON.
- Số phần tử đúng bằng số dòng đầu vào.
- Mỗi phần tử: {{"index": <số thứ tự 1-based>, "translation": "<bản dịch>"}}.

TUYỆT ĐỐI KHÔNG:
- Bỏ sót hoặc gộp nhiều dòng thành một.
- Trả về câu gốc khi chưa dịch.
{glossary_block}
Dữ liệu (ORIGINAL là câu gốc, ROUGH là bản dịch thô):
{pairs}
"""


_QA_TEMPLATE = """\
{system}

Bạn là reviewer. So sánh từng cặp (ORIGINAL gốc tiếng Nhật, TRANSLATION bản dịch tiếng Việt).
Nhiệm vụ: phát hiện "translation drift" — bản dịch lệch nghĩa, bỏ sót thông tin, hoặc còn sót romaji/kana.

QUY TẮC:
- Trả về JSON array, mỗi phần tử:
  {{"index": <1-based>, "ok": true|false, "issue": "<mô tả ngắn nếu ok=false>", "suggestion": "<bản dịch đề xuất nếu ok=false>"}}
- ok=true nếu bản dịch đúng nghĩa, tự nhiên, không có romaji/kana sót.
- ok=false nếu lệch nghĩa, bỏ sót particle, còn romaji/kana onomatopoeia, hoặc quá máy móc.

TUYỆT ĐỐI KHÔNG:
- Bỏ sót dòng nào trong danh sách.
- Trả về text ngoài JSON.
{glossary_block}
Danh sách cần review:
{pairs}
"""


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _render_glossary(glossary_terms) -> str:
    if not glossary_terms:
        return ""
    lines = []
    for t in glossary_terms:
        line = f"- {t['source_term']} -> {t['target_term']}"
        if t.get('context_hint'):
            line += f" (context: {t['context_hint']})"
        lines.append(line)
    return "MANDATORY GLOSSARY:\n" + "\n".join(lines) + "\n\n"


def _number_lines(lines: List[str]) -> str:
    return "\n".join(f"{i+1}. {l}" for i, l in enumerate(lines))


def _format_pairs(source_lines: List[str], translations: List[str]) -> str:
    return "\n".join(
        f"ORIGINAL: {s}\nTRANSLATION: {t}"
        for s, t in zip(source_lines, translations)
    )


_LEADING_NUM_RE = re.compile(r"^\s*\d+[\.\)]\s*")


def _strip_leading_number(line: str) -> str:
    return _LEADING_NUM_RE.sub("", line).strip()


def _chunked(seq: List[str], size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

class JaTranslator:
    """Wrapper dịch ja→vi theo 3-pass. Gọi từ ``TranslationPipeline``.

    Parameters
    ----------
    translator : TranslatorService
        Đã được khởi tạo với model phù hợp cho tiếng Nhật
        (mặc định config ``models.ja`` nếu có, fallback ``models.default``).
    chunk_size : int
        Số dòng mỗi chunk trong pass literal (mặc định 8).
    """

    def __init__(self, translator, chunk_size: int = 8):
        self.translator = translator
        self.chunk_size = chunk_size

    # --- Pass 1 ---
    def literal_translate(
        self,
        source_lines: List[str],
        glossary_terms=None,
    ) -> Optional[List[str]]:
        """Pass 1: dịch literal, giữ keigo/particles/onomatopoeia."""
        glossary_block = _render_glossary(glossary_terms)
        translations: List[str] = []

        for chunk in _chunked(source_lines, self.chunk_size):
            numbered = _number_lines(chunk)
            prompt = _LITERAL_TEMPLATE.format(
                system=_SYSTEM_PROMPT,
                glossary_block=glossary_block,
                lines=numbered,
            )
            try:
                raw = self.translator.generate(prompt, temperature=0.1)
            except Exception as e:
                logger.warning(f"JaTranslator.literal_translate: generate failed: {e}")
                return None

            out_lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
            for j, original in enumerate(chunk):
                if j < len(out_lines):
                    translations.append(_strip_leading_number(out_lines[j]))
                else:
                    # retry từng dòng bằng prompt literal ngắn
                    single = self._translate_single_literal(original, glossary_terms)
                    translations.append(single if single else original)
        return translations if translations else None

    def _translate_single_literal(self, text: str, glossary_terms=None) -> Optional[str]:
        """Retry 1 dòng literal khi chunk bị thiếu."""
        glossary_block = _render_glossary(glossary_terms)
        prompt = (
            f"{_SYSTEM_PROMPT}\n\n"
            f"Dịch câu tiếng Nhật sau sang tiếng Việt.\n"
            f"Giữ keigo/particles/onomatopoeia đúng, KHÔNG trau chuốt.\n"
            f"Trả về CHỈ bản dịch, một dòng duy nhất.\n"
            f"{glossary_block}"
            f"Câu gốc: {text}"
        )
        try:
            raw = self.translator.generate(prompt, temperature=0.1)
            line = raw.strip().split("\n")[0].strip()
            return _strip_leading_number(line) or None
        except Exception as e:
            logger.warning(f"JaTranslator._translate_single_literal failed: {e}")
            return None

    # --- Pass 2 ---
    def polish_translate(
        self,
        source_lines: List[str],
        rough_translations: List[str],
        glossary_terms=None,
    ) -> List[str]:
        """Pass 2: trau chuốt. Trả về list (giữ rough nếu thất bại)."""
        if not rough_translations or len(rough_translations) != len(source_lines):
            return rough_translations

        glossary_block = _render_glossary(glossary_terms)
        pairs_text = _format_pairs(source_lines, rough_translations)
        prompt = _POLISH_TEMPLATE.format(
            system=_SYSTEM_PROMPT,
            glossary_block=glossary_block,
            pairs=pairs_text,
        )

        try:
            raw = self.translator.generate(prompt, temperature=0.2)
        except Exception as e:
            logger.warning(f"JaTranslator.polish_translate: generate failed: {e}, keeping rough")
            return rough_translations

        parsed = self._safe_extract_json(raw)
        if not isinstance(parsed, list):
            logger.warning("JaTranslator.polish_translate: no JSON list, keeping rough")
            return rough_translations

        out: List[str] = []
        for j, original in enumerate(source_lines):
            match = None
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                idx = item.get("index")
                if idx == j + 1:
                    match = item.get("translation") or item.get("text")
                    break
            out.append(match.strip() if match else rough_translations[j])
        return out

    # --- Pass 3 ---
    def qa_review(
        self,
        source_lines: List[str],
        translations: List[str],
        glossary_terms=None,
    ) -> List[dict]:
        """Pass 3: QA review. Trả về list {index, ok, issue, suggestion}.

        Dòng nào ok=false sẽ được thay bằng ``suggestion`` (nếu có).
        """
        if not translations or len(translations) != len(source_lines):
            return []

        glossary_block = _render_glossary(glossary_terms)
        pairs_text = _format_pairs(source_lines, translations)
        prompt = _QA_TEMPLATE.format(
            system=_SYSTEM_PROMPT,
            glossary_block=glossary_block,
            pairs=pairs_text,
        )

        try:
            raw = self.translator.generate(prompt, temperature=0.0)
        except Exception as e:
            logger.warning(f"JaTranslator.qa_review: generate failed: {e}")
            return []

        parsed = self._safe_extract_json(raw)
        if not isinstance(parsed, list):
            return []

        results: List[dict] = []
        for item in parsed:
            if not isinstance(item, dict) or "index" not in item:
                continue
            results.append({
                "index": int(item.get("index", 0)),
                "ok": bool(item.get("ok", False)),
                "issue": str(item.get("issue", "")),
                "suggestion": str(item.get("suggestion", "")),
            })
        return results

    def apply_qa_results(
        self,
        translations: List[str],
        qa_results: List[dict],
    ) -> List[str]:
        """Áp suggestion từ QA pass vào list translations (in place, return new list)."""
        if not qa_results:
            return translations
        out = list(translations)
        for item in qa_results:
            if item.get("ok", True):
                continue
            idx = item.get("index", 0) - 1
            sug = item.get("suggestion", "").strip()
            if 0 <= idx < len(out) and sug:
                out[idx] = sug
        return out

    # --- helpers ---
    def _safe_extract_json(self, text: str):
        """Dùng json_repair (giống TranslatorService.extract_json) nhưng trả về
        None khi thất bại thay vì raise.
        """
        try:
            from json_repair import repair_json
            import json
            return json.loads(repair_json(text))
        except Exception:
            pass
        # fallback: tìm JSON array lớn nhất
        try:
            import json
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
