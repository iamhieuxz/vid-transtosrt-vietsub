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

_BASE_SYSTEM_PROMPT = """\
Bạn là chuyên gia dịch phụ đề phim/show từ tiếng Nhật sang tiếng Việt.
Phong cách tự nhiên, dễ hiểu, đúng giọng nhân vật.

ĐẶC THÙ TIẾNG NHẬT (BẮT BUỘC NẮM):
- 敬語 (keigo): phân biệt rõ 敬体 (です/ます) ↔ 常体 (だ/する) ↔ 謙譲語 ↔ 尊敬語.
  Vai lịch sự → "anh/chị"; vai thân mật → "tớ/cậu/tao-mày". NHẤT QUÁN xuyên suốt phim.
- Xưng hô PHẢI chọn ngôi cụ thể (anh, chị, em, bạn, tôi, mình...).
  TUYỆT ĐỐI KHÔNG dùng "Anh/chị" / "anh/chị" dạng gộp — đó là lỗi.
  + 先輩 (senpai) → "anh" (vai trên, nam)
  + 先輩 nữ + nạn nhân nữ → "chị"
  + 君 (kimi) → "em" (vai dưới)
  + あなた (anata) → "anh" mặc định
  + 不明確 (không rõ) → "anh" mặc định cho nam, "chị" cho nữ theo giọng tự xưng của nhân vật.
  + 俺/僕 (ore/boku) → "tôi" (giọng nam)
  + あたし/私 (atashi/watashi) → "tôi" (giọng nữ)
  + 私 (watashi) trung tính → "tôi"
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

# Uncen system prompt — thêm vào base cho mode uncen
# Bắt buộc dịch trung thực 100% từ ngữ tình dục, KHÔNG làm sạch/làm mượt
_UNCEN_ADDON = """

UNCEN MODE (BẮT BUỘC — dịch phim người lớn/JAV):
- Dịch TRUNG THỰC 100% mọi từ ngữ tình dục, KHÔNG làm sạch, KHÔNG né tránh, KHÔNG dùng từ Hán Việt lịch sự.
- "気持ちいい" → "Sướng quá" (KHÔNG "Dễ chịu" / "Thoải mái").
- "いく" / "逝く" → "ra" / "sắp ra" (KHÔNG "lên đỉnh" mềm mại).
- "セックス" → "quan hệ tình dục" (KHÔNG "gần gũi" / "yêu").
- "中出し" → "xuất tinh trong" (KHÔNG giảm nhẹ).
- "孕む" / "妊娠" → "mang thai" / "có thai" (KHÔNG "có em bé" né tránh).
- "犯す" → "cưỡng ép" / "hiếp" tùy ngữ cảnh (KHÔNG che đậy).
- "やめて" trong ngữ cảnh tình dục → "dừng lại" / "thôi" tùy giọng (KHÔNG bỏ).
- Particles rên rỉ (あっ/んっ/はぁ/きゃっ) → giữ nguyên "anh"/"ừ"/"hừm" theo âm thanh gốc.
- GLOSSARY (glossary tình dục) là LUẬT, KHÔNG được thay bằng từ Hán Việt lịch sự.
- Tiếng rên/rên rỉ → giữ nguyên tiếng Việt tương đương, KHÔNG bỏ.
- 喘ぐ/喘ぎ → "rên rỉ"/"rên" (KHÔNG "thở").
- 射精 → "xuất tinh" (KHÔNG "phóng thích").
- 勃起 → "cương cứng" (KHÔNG "hưng phấn").
- ĐỪNG giảm sắc thái ngay cả khi câu nghe thô — đó là phong cách phim.
"""

# Hỗ trợ cả 2 tên gọi cũ (_SYSTEM_PROMPT) cho tương thích ngược
# (các module khác có thể đã import _SYSTEM_PROMPT)
_SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT


_LITERAL_TEMPLATE = """\
{system}

Bản dịch "nguyên văn" từng câu, ưu tiên giữ đúng cấu trúc & cảm xúc gốc.

CHỈ DỊCH, KHÔNG trau chuốt.
- Xưng hô: ghi chú rõ dạng keigo gặp phải (trong đầu) để bước sau chọn từ xưng hô VN nhất quán.
- Onomatopoeia: dịch sát nghĩa ra tiếng Việt (xem system).
- Particles cuối câu: truyền tải cảm xúc, không bỏ.

QUY TẮC ĐẦU RA (NGHIÊM):
- Trả về CHÍNH XÁc số dòng bằng số dòng đầu vào (không thừa, không thiếu).
- Mỗi dòng chỉ chứa bản dịch tương ứng, đánh số theo mẫu: "1. <dịch>"
- KHÔNG viết lời dẫn, KHÔNG preamble "Dưới đây là...", KHÔNG chú thích ngoặc đơn.
- KHÔNG bọc markdown (no **bold**, no →, no bullet).
- Khi gặp tên riêng, PHẢI dùng canonical trong TEN RIENG (không tự sáng tạo).
- Nếu câu đầu vào đã là tiếng Việt, in lại nguyên văn không đổi.

TUYỆT ĐỐI KHÔNG:
- Bỏ sót hoặc gộp nhiều dòng thành một.
- Trả về câu gốc (tiếng Nhật) thay cho bản dịch.
- Dùng ký tự lạ (emoji, ký tự control, romaji trong ngoặc) trong bản dịch.
- Tự sáng tạo tên riêng (vd: 匠 KHÔNG phải "Craft", あゆみ KHÔNG phải "Aya").
{glossary_block}
{character_block}
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
5. Khi gặp tên riêng, dùng canonical trong TEN RIENG (không tự sáng tạo).
{context_block}
QUY TẮC ĐẦU RA:
- Trả về JSON array hợp lệ, không kèm text ngoài JSON.
- Số phần tử đúng bằng số dòng đầu vào.
- Mỗi phần tử: {{"id": <số thứ tự 1-based>, "text": "<bản dịch>"}}.

TUYỆT ĐỐI KHÔNG:
- Bỏ sót hoặc gộp nhiều dòng thành một.
- Trả về câu gốc khi chưa dịch.
- Tự sáng tạo tên riêng (vd: 匠 KHÔNG phải "Craft", あゆみ KHÔNG phải "Aya").
{glossary_block}
{character_block}
Dữ liệu (ORIGINAL là câu gốc, ROUGH là bản dịch thô):
{pairs}
"""


_QA_TEMPLATE = """\
{system}

Bạn là reviewer. So sánh từng cặp (ORIGINAL gốc tiếng Nhật, TRANSLATION bản dịch tiếng Việt).
Nhiệm vụ: phát hiện "translation drift" — bản dịch lệch nghĩa, bỏ sót thông tin, hoặc còn sót romaji/kana.
{context_block}
QUY TẮC:
- Trả về JSON array, mỗi phần tử:
  {{"id": <1-based>, "ok": true|false, "issue": "<mô tả ngắn nếu ok=false>", "suggestion": "<bản dịch đề xuất nếu ok=false>"}}
- ok=true nếu bản dịch đúng nghĩa, tự nhiên, không có romaji/kana sót.
- ok=false nếu lệch nghĩa, bỏ sót particle, còn romaji/kana onomatopoeia, hoặc quá máy móc.
- Nếu xưng hô trong TRANSLATION không khớp với bản đồ pronouns_map ở context, đánh ok=false.
- Nếu tên riêng trong TRANSLATION không khớp TEN RIENG (vd: 匠 = Takumi, あゆみ = Ayumi), đánh ok=false.

TUYỆT ĐỐI KHÔNG:
- Bỏ sót dòng nào trong danh sách.
- Trả về text ngoài JSON.
{glossary_block}
{character_block}
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


def _render_character_block(character_names) -> str:
    """Render character_names từ config thành block inject vào prompt.

    Mục đích: LLM biết dùng canonical cho tên riêng (VD: 匠 -> Takumi,
    KHÔNG tự sáng tạo "Craft"; あゆみ -> Ayumi, KHÔNG "Aya").
    """
    if not character_names:
        return ""
    lines = []
    for entry in character_names:
        canonical = entry.get('canonical', '').strip()
        aliases = entry.get('aliases', []) or []
        if not canonical or not aliases:
            continue
        # Dùng alias JA đầu tiên làm source_term
        ja_aliases = [a for a in aliases
                      if re.match(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]', a)]
        source = ja_aliases[0] if ja_aliases else canonical
        line = f"- {source} -> {canonical}"
        lines.append(line)
    if not lines:
        return ""
    return "TEN RIENG BAT BUOC (giu nguyen, KHONG tu y dich/sang tao):\n" + "\n".join(lines) + "\n\n"


def _render_context(context_summary) -> str:
    """Render context summary (từ JaContextAnalyzer) thành block inject vào prompt polish.

    Nếu context_summary None / rỗng → trả chuỗi rỗng (baseline 3-pass).
    """
    if not context_summary:
        return ""
    parts = []
    speakers = context_summary.get("speakers") or []
    if speakers:
        parts.append(f"- Nhân vật xuất hiện: {', '.join(speakers)}")
    pronouns = context_summary.get("pronouns_map") or {}
    if pronouns:
        # Format: "Taro->Hanako: Taro dùng 'tớ', Hanako dùng 'cậu'"
        pairs_lines = []
        for pair, mapping in pronouns.items():
            if not isinstance(mapping, dict):
                continue
            self_pronoun = mapping.get("self") or "?"
            other_pronoun = mapping.get("other") or "?"
            pairs_lines.append(f"  + {pair}: nói 'tôi' là '{self_pronoun}', gọi đối phương là '{other_pronoun}'")
        if pairs_lines:
            parts.append("- Xưng hô chuẩn hóa (DÙNG XUYÊN SUỐT):\n" + "\n".join(pairs_lines))
    tone = context_summary.get("tone")
    if tone and tone != "unknown":
        parts.append(f"- Tone: {tone}")
    setting = context_summary.get("setting")
    if setting and setting != "unknown":
        parts.append(f"- Bối cảnh: {setting}")
    summary = context_summary.get("summary")
    if summary:
        parts.append(f"- Tóm tắt tình huống: {summary}")
    if not parts:
        return ""
    return "NGỮ CẢNH WINDOW (đã phân tích trước, dùng để chuẩn hóa xưng hô):\n" + "\n".join(parts) + "\n\n"


def _number_lines(lines: List[str]) -> str:
    return "\n".join(f"{i+1}. {l}" for i, l in enumerate(lines))


def _get_system_prompt(mode: str = "default") -> str:
    """Trả về system prompt theo mode.

    - "default" / "cen" : chỉ base (cho phim thường, anime, drama)
    - "uncen" : base + addon uncen (cho JAV, phim 18+)
    """
    if (mode or "").lower() == "uncen":
        return _BASE_SYSTEM_PROMPT + _UNCEN_ADDON
    return _BASE_SYSTEM_PROMPT


def _format_pairs(source_lines: List[str], translations: List[str]) -> str:
    return "\n".join(
        f"ORIGINAL: {s}\nTRANSLATION: {t}"
        for s, t in zip(source_lines, translations)
    )


_LEADING_NUM_RE = re.compile(r"^\s*\d+[\.\)]\s*")

# shortcut: strip markdown bold + leading arrows khi LLM 8B leak format
_MARKDOWN_BOLD_RE = re.compile(r"\*{1,3}([^*]+)\*{1,3}")
_LEADING_ARROW_RE = re.compile(r"^[→\-\*\u2022]\s*")


def _strip_leading_number(line: str) -> str:
    return _LEADING_NUM_RE.sub("", line).strip()


def _clean_markdown(line: str) -> str:
    """Strip markdown bold, leading arrows, giữ text thuần. Áp dụng cho mọi
    LLM output trước khi xuất ra SRT — tránh bug **xxx**, → xxx leak.
    """
    if not line:
        return line
    cleaned = _MARKDOWN_BOLD_RE.sub(r"\1", line)
    cleaned = _LEADING_ARROW_RE.sub("", cleaned)
    return cleaned.strip()


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

    def __init__(self, translator, chunk_size: int = 8, character_names=None, mode: str = "default"):
        self.translator = translator
        self.chunk_size = chunk_size
        # Inject character_names từ config (tên riêng bắt buộc dùng canonical)
        # LLM hay tự sáng tạo tên nếu không có block này (vd: 匠 -> Craft)
        self.character_names = character_names or []
        # Mode: "default" / "cen" / "uncen" — quyết định system prompt
        self.mode = mode or "default"
        self.system_prompt = _get_system_prompt(self.mode)

    # --- Pass 1 ---
    def literal_translate(
        self,
        source_lines: List[str],
        glossary_terms=None,
    ) -> Optional[List[str]]:
        """Pass 1: dịch literal, giữ keigo/particles/onomatopoeia."""
        glossary_block = _render_glossary(glossary_terms)
        character_block = _render_character_block(self.character_names)
        translations: List[str] = []

        for chunk in _chunked(source_lines, self.chunk_size):
            numbered = _number_lines(chunk)
            prompt = _LITERAL_TEMPLATE.format(
                system=self.system_prompt,
                glossary_block=glossary_block,
                character_block=character_block,
                lines=numbered,
            )
            try:
                raw = self.translator.generate(prompt, temperature=0.1)
            except Exception as e:
                logger.warning(f"JaTranslator.literal_translate: generate failed: {e}")
                return None

            out_lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
            # shortcut: lọc chỉ giữ các dòng BẮT ĐẦU BẰNG SỐ thứ tự hợp lệ (1., 2., ...).
            # LLM 8B hay chèn preamble "Dưới đây là..." hoặc trộn Nhật ngoài numbered,
            # cách này robust hơn drop-preamble đơn lẻ; tránh bug **彼とのセックスが** leak.
            numbered_re = re.compile(r"^\s*(\d+)\s*[\.\)、]\s*(.*)$")
            parsed_lines = []
            for line in out_lines:
                m = numbered_re.match(line)
                if m:
                    idx_str = m.group(1)
                    text = m.group(2).strip()
                    parsed_lines.append(text)
            if not parsed_lines:
                # LLM không numbered → fallback drop-preamble cũ (cho prompt cũ)
                while out_lines and not out_lines[0].lstrip().startswith(("1.", "1)", "1、", "1 ")):
                    out_lines.pop(0)
                    if not out_lines:
                        break
                parsed_lines = out_lines
            for j, original in enumerate(chunk):
                if j < len(parsed_lines):
                    translations.append(_clean_markdown(parsed_lines[j]))
                else:
                    # retry từng dòng bằng prompt literal ngắn
                    single = self._translate_single_literal(original, glossary_terms)
                    # shortcut: nếu retry fail thì return None để pipeline biết retry,
                    # KHÔNG fallback về raw Nhật (gây bug "giữ nguyên bản gốc chưa dịch")
                    translations.append(single)
        return translations if translations else None

    def _translate_single_literal(self, text: str, glossary_terms=None) -> Optional[str]:
        """Retry 1 dòng literal khi chunk bị thiếu."""
        glossary_block = _render_glossary(glossary_terms)
        prompt = (
            f"{self.system_prompt}\n\n"
            f"Dịch câu tiếng Nhật sau sang tiếng Việt.\n"
            f"Giữ keigo/particles/onomatopoeia đúng, KHÔNG trau chuốt.\n"
            f"Trả về CHỈ bản dịch, một dòng duy nhất, không ghi chú.\n"
            f"{glossary_block}"
            f"Câu gốc: {text}"
        )
        try:
            raw = self.translator.generate(prompt, temperature=0.1)
            line = raw.strip().split("\n")[0].strip()
            cleaned = _clean_markdown(_strip_leading_number(line))
            # shortcut: nếu trả về chính câu gốc / rỗng thì coi như fail,
            # <3s overhead, ép pipeline retry thay vì để raw Nhật vào output
            if not cleaned or cleaned == text.strip():
                logger.warning(f"JaTranslator._translate_single_literal: empty/echo for {text[:40]!r}")
                return None
            return cleaned
        except Exception as e:
            logger.warning(f"JaTranslator._translate_single_literal failed: {e}")
            return None

    # --- Pass 2 ---
    def polish_translate(
        self,
        source_lines: List[str],
        rough_translations: List[str],
        glossary_terms=None,
        context_summary: Optional[dict] = None,
    ) -> List[str]:
        """Pass 2: trau chuốt. Trả về list (giữ rough nếu thất bại hoàn toàn).

        Parameters
        ----------
        context_summary : dict, optional
            Output từ JaContextAnalyzer: {speakers, pronouns_map, tone, setting, summary}.
            Nếu None thì polish theo baseline 3-pass (không có xưng hô mapping).
        """
        if not rough_translations or len(rough_translations) != len(source_lines):
            return rough_translations

        glossary_block = _render_glossary(glossary_terms)
        character_block = _render_character_block(self.character_names)
        context_block = _render_context(context_summary)
        pairs_text = _format_pairs(source_lines, rough_translations)
        prompt = _POLISH_TEMPLATE.format(
            system=self.system_prompt,
            glossary_block=glossary_block,
            character_block=character_block,
            context_block=context_block,
            pairs=pairs_text,
        )

        try:
            raw = self.translator.generate(prompt, temperature=0.2)
        except Exception as e:
            logger.warning(f"JaTranslator.polish_translate: generate failed: {e}, keeping rough")
            return rough_translations

        parsed = self._safe_extract_json(raw)
        if not isinstance(parsed, list):
            # shortcut: retry 1 lần với temperature=0 (deterministic JSON), <5s overhead
            logger.debug("JaTranslator.polish_translate: retry with temperature=0")
            try:
                raw2 = self.translator.generate(prompt, temperature=0.0)
                parsed = self._safe_extract_json(raw2)
            except Exception:
                pass
        if not isinstance(parsed, list):
            logger.warning("JaTranslator.polish_translate: no JSON list, keeping rough")
            return rough_translations

        # Fail-fast nếu JSON trả về số phần tử sai — tránh silent mapping sai
        if len(parsed) != len(source_lines):
            logger.warning(
                f"JaTranslator.polish_translate: JSON length {len(parsed)} "
                f"!= source {len(source_lines)}, keeping rough"
            )
            return rough_translations

        out: List[str] = []
        for j, original in enumerate(source_lines):
            match = None
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                idx = item.get("id")
                # id có thể là int hoặc string số; chuẩn hoá trước khi so
                try:
                    idx_int = int(idx) if idx is not None else None
                except (ValueError, TypeError):
                    idx_int = None
                if idx_int == j + 1:
                    match = item.get("text") or item.get("translation")
                    break
            out.append(_clean_markdown(match).strip() if match else rough_translations[j])
        return out

    # --- Pass 3 ---
    def qa_review(
        self,
        source_lines: List[str],
        translations: List[str],
        glossary_terms=None,
        context_summary: Optional[dict] = None,
    ) -> List[dict]:
        """Pass 3: QA review. Trả về list {index, ok, issue, suggestion}.

        Dòng nào ok=false sẽ được thay bằng ``suggestion`` (nếu có).

        Parameters
        ----------
        context_summary : dict, optional
            Context từ Worker A; nếu có, QA check cả xưng hô có khớp pronouns_map không.
        """
        if not translations or len(translations) != len(source_lines):
            return []

        glossary_block = _render_glossary(glossary_terms)
        character_block = _render_character_block(self.character_names)
        context_block = _render_context(context_summary)
        pairs_text = _format_pairs(source_lines, translations)
        prompt = _QA_TEMPLATE.format(
            system=self.system_prompt,
            glossary_block=glossary_block,
            character_block=character_block,
            context_block=context_block,
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

        # Chuẩn hoá: nếu JSON có len khác source, chỉ lấy phần overlap
        results: List[dict] = []
        for item in parsed:
            if not isinstance(item, dict) or "index" not in item:
                continue
            try:
                idx_int = int(item.get("index", 0))
            except (ValueError, TypeError):
                continue
            if idx_int < 1 or idx_int > len(source_lines):
                continue
            results.append({
                "index": idx_int,
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
        """Áp suggestion từ QA pass vào list translations (in place, return new list).

        P1-2 fix: QA prompt schema now uses 'id' (consistent with polish schema) instead
        of 'index'. Read 'id' to align with polish_translate. Fall back to 'index' for
        backwards compatibility with older LLM outputs.
        """
        if not qa_results:
            return translations
        out = list(translations)
        for item in qa_results:
            if item.get("ok", True):
                continue
            # P1-2: prefer 'id' (consistent with polish), fall back to 'index' for old data
            raw_id = item.get("id")
            if raw_id is None:
                raw_id = item.get("index", 0)
            idx = raw_id - 1
            sug = _clean_markdown(item.get("suggestion", "").strip())
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
