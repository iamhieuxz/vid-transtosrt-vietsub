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

import difflib
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
  Vai lịch sự hoặc người trưởng thành → "tôi - anh/chị/em/cô". NHẤT QUÁN xuyên suốt phim.
  TUYỆT ĐỐI KHÔNG tự ý dùng "tớ/cậu" cho người trưởng thành, phụ huynh (お母さん/母/娘), cấp trên/cấp dưới hoặc cảnh 18+.
- Xưng hô PHẢI chọn ngôi cụ thể (anh, chị, em, cô, tôi, mình, mẹ, con...).
  TUYỆT ĐỐI KHÔNG dùng "Anh/chị" / "anh/chị" dạng gộp — đó là lỗi.
  + 先輩 (senpai) → "anh" (vai trên, nam)
  + 先輩 nữ + nạn nhân nữ → "chị"
  + 君 (kimi) → "em" / "cậu" (khi người lớn tuổi/mẹ bạn gái gọi bạn trai của con gái)
  + あなた (anata) → "anh" mặc định
  + 不明確 (không rõ) → "anh" mặc định cho nam, "chị"/"cô" cho nữ theo giọng tự xưng của nhân vật.
  + お母さん/母さん/ママ → "mẹ" (khi con gọi mẹ) hoặc "cô/mẹ" (khi bạn trai của con gọi); người mẹ xưng "mẹ" với con, xưng "tôi/cô" với bạn trai của con (TUYỆT ĐỐI KHÔNG xưng "tớ").
  + 娘 (musume) → "con gái" (VD: 娘を身ごもった = "tôi mang thai con gái", 一人で娘を育てた = "một mình nuôi con gái", 娘には笑っててほしい = "tôi muốn con gái mình luôn mỉm cười"; KHÔNG dịch 娘 thành "cô ấy").
  + 兄さん/お兄さん/お兄ちゃん → "anh" (KHÔNG "cậu"); 姉さん/お姉さん/お姉ちゃん → "chị";
    弟/妹 gọi bằng tên hoặc "em". 旦那/あなた (vợ gọi chồng) → "anh"; 奥さん → "chị"/"vợ" tùy người nói.
    お義母さん/お義父さん → "mẹ"/"bố" (hoặc "mẹ chồng"/"bố chồng" khi người nói là con dâu). Giữ nguyên cách xưng đã chọn.
  + 俺/僕 (ore/boku) → "tôi" / "anh" / "em" (giọng nam, KHÔNG dịch "tớ" trong cảnh người lớn/18+)
  + あたし/私 (atashi/watashi) → "tôi" / "em" / "mẹ" (giọng nữ, KHÔNG dịch "tớ" khi là mẹ/người trưởng thành)
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
- TẨY MÙI DỊCH MÁY (DE-TRANSLATIONESE — BẮT BUỘC ĐỂ CÂU THOẠI TỰ NHIÊN):
  + Khử bị động gượng gạo: Tiếng Nhật rất chuộng thể bị động (〜られる, 〜てしまう, 〜ていただく).
    Trong tiếng Việt giao tiếp hàng ngày, KHÔNG dịch thành "bị/được" cứng nhắc mà diễn đạt chủ động theo ý nhân vật:
    * やられたかった → "muốn anh làm thô bạo / muốn anh làm mạnh tay" (KHÔNG dịch máy "muốn bị cưỡng ép").
    * 言われても困る → "anh nói thế tôi biết làm sao" (KHÔNG dịch "dù bị nói thế cũng khó xử").
    * 助けてもらった → "nhờ anh cứu giúp" (KHÔNG dịch "được giúp đỡ").
  + Khử danh hóa và từ đệm hành chính thừa thãi: Tránh các từ ("việc", "sự", "chuyện là", "thực tế rằng") trong câu thoại:
    * 現実を受け入れる → "chấp nhận sự thật" (tự nhiên hơn "chấp nhận thực tế").
    * 〜ということ → dịch thẳng hành động, không đệm "việc...".
  + Khẩu ngữ đối thoại tự nhiên: Sử dụng linh hoạt trợ từ tình thái và thán từ tiếng Việt (nhé, nha, đấy, mà, cơ, à, hả...) phù hợp tâm lý và bối cảnh phim.
  + ĐỘC LẬP TỪNG DÒNG: Mỗi dòng phụ đề là độc lập. TUYỆT ĐỐI KHÔNG lặp lại bản dịch của câu trước cho câu sau khi câu tiếng Nhật khác nhau. Không để thông tin câu dưới rò rỉ lên câu trên.
"""

# Uncen system prompt — thêm vào base cho mode uncen
# Bắt buộc dịch trung thực 100% từ ngữ tình dục theo khẩu ngữ đời thực (Mức 2)
_UNCEN_ADDON = """

UNCEN MODE (BẮT BUỘC — dịch phim người lớn/JAV theo khẩu ngữ tự nhiên, chân thực):
- Dịch TRUNG THỰC 100% mọi từ ngữ tình dục bằng KHẨU NGỮ ĐỜI THỰC, KHÔNG làm sạch, KHÔNG né tránh, KHÔNG dùng từ Hán Việt lịch sự hay y khoa cứng nhắc.
- "気持ちいい" → "Sướng quá" / "Phê quá" (KHÔNG "Dễ chịu" / "Thoải mái").
- "いく" / "逝く" → "ra" / "sắp ra" (KHÔNG "lên đỉnh" mềm mại).
- "セックス" → "làm tình" / "chịch" (KHÔNG dùng từ y khoa "quan hệ tình dục", KHÔNG "gần gũi" / "yêu").
- "中出し" → "bắn vào trong" / "xuất vào trong" (KHÔNG "xuất tinh trong" cứng nhắc).
- "ちんぽ" / "ちんちん" / "ちんこ" / "肉棒" → "con cặc" / "cặc" / "cu" (KHÔNG "cậu nhỏ", tránh dùng từ y khoa "dương vật" trong câu thoại giao tiếp).
- "まんこ" / "おまんこ" → "bướm" / "lồn" (KHÔNG dịch y khoa "âm đạo", KHÔNG "cô bé").
- "フェラ" / "フェラチオ" → "bú cu" / "bú cặc" (KHÔNG dịch y khoa "hút dương vật" hay "ngậm").
- "パイズリ" → "kẹp ngực" / "kẹp vú" (KHÔNG "kẹp dương vật bằng ngực").
- "手コキ" → "sóc lọ" / "dùng tay sục cặc" (KHÔNG "kích thích dương vật bằng tay").
- "クンニ" → "liếm bướm" (KHÔNG "liếm âm đạo").
- "孕む" / "妊娠" → "mang thai" / "có thai" (KHÔNG "có em bé" né tránh).
- "犯す" → "cưỡng ép" / "hiếp" tùy ngữ cảnh (KHÔNG che đậy).
- "やめて" trong ngữ cảnh tình dục → "dừng lại" / "thôi mà" / "đừng mà" tùy giọng (KHÔNG bỏ).
- Particles rên rỉ (あっ/んっ/はぁ/きゃっ) → giữ nguyên "anh"/"ừ"/"hừm"/"a...".
- GLOSSARY (glossary tình dục) là LUẬT, KHÔNG được thay bằng từ Hán Việt lịch sự.
- Tiếng rên/rên rỉ → giữ nguyên tiếng Việt tương đương, KHÔNG bỏ.
- 喘ぐ/喘ぎ → "rên rỉ"/"rên" (KHÔNG "thở").
- 射精 → "bắn tinh" / "xuất tinh" (KHÔNG "phóng thích").
- 勃起 → "cương cứng" (KHÔNG "hưng phấn").
- NEO GIỚI TÍNH SINH HỌC (TUYỆT ĐỐI KHÔNG ĐẢO NGƯỢC NAM/NỮ):
  + "まんこ" / "おまんこ" (bướm/lồn), "おっぱい" (ngực/vú), "子宮" (tử cung) là cơ thể NỮ → luôn đi với đại từ nữ ("của em/cô/chị/mẹ", TUYỆT ĐỐI KHÔNG dịch là "bướm của anh" hay "ngực anh").
  + "ちんぽ" / "ちんちん" / "肉棒" (con cặc/cu), "中出しした/しちゃいました" (đã bắn vào trong) là cơ thể/hành động của NAM → khi nam nói với nữ ("ずっとちんぽ見てますね"), phải dịch là "cô/em cứ nhìn con cặc của tôi suốt nhỉ" (KHÔNG dịch thành "tớ nhìn con cặc của anh").
- ĐỪNG giảm sắc thái ngay cả khi câu nghe thô — đó là phong cách phim.
"""

# Hỗ trợ cả 2 tên gọi cũ (_SYSTEM_PROMPT) cho tương thích ngược
# (các module khác có thể đã import _SYSTEM_PROMPT)
_SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT


_LITERAL_TEMPLATE = """\
{system}

Bản dịch nguyên văn từng câu. CHỈ DỊCH SANG TIẾNG VIỆT, KHÔNG trau chuốt.

OUTPUT FORMAT (BẮT BUỘC):
1. <bản dịch tiếng Việt>
2. <bản dịch tiếng Việt>
3. <bản dịch tiếng Việt>

Return numbered list, đúng {n} dòng. Không thêm text ngoài danh sách.
Không giữ nguyên câu tiếng Nhật. Không thêm comment.

{glossary_block}
{character_block}
Lines:
{lines}
"""


_POLISH_TEMPLATE = """\
{system}

Bản dịch "thô" (ROUGH) cần được trau chuốt cho tự nhiên, nhất quán, mượt như phụ đề phim Việt.

ƯU TIÊN TRAU CHUỐT:
1. Nhất quán xưng hô xuyên suốt (dựa trên danh sách các dạng keigo đã thấy ở bước literal).
2. Câu thoại nghe như người Việt nói, KHÔNG "dịch máy" (De-translationese):
   - Chuyển câu bị động gượng sang chủ động tự nhiên (vd: "muốn anh làm thô bạo", KHÔNG "muốn bị cưỡng ép").
   - Bỏ các từ đệm hành chính ("việc", "sự", "thực tế rằng").
   - Thêm trợ từ khẩu ngữ tự nhiên (nhé, nha, đấy, mà, cơ, à, hả...).
3. Giữ đúng sắc thái cảm xúc (ngạc nhiên, giận, mỉa mai...).
4. Nếu ROUGH còn sót romaji/kana onomatopoeia, dịch lại sang tiếng Việt.
5. Khi gặp tên riêng, dùng canonical trong TEN RIENG (không tự sáng tạo).
6. ĐỘC LẬP TỪNG DÒNG: Mỗi dòng phụ đề chỉ dịch đúng phần nghĩa của câu gốc đó.
{context_block}
QUY TẮC ĐẦU RA:
- Trả về JSON array hợp lệ, không kèm text ngoài JSON.
- Số phần tử đúng bằng số dòng đầu vào.
- Mỗi phần tử: {{"id": <số thứ tự 1-based>, "text": "<bản dịch>"}}.

TUYỆT ĐỐI KHÔNG:
- Bỏ sót hoặc gộp nhiều dòng thành một.
- Trả về câu gốc khi chưa dịch.
- Tự sáng tạo tên riêng (vd: 匠 KHÔNG phải "Craft", あゆみ KHÔNG phải "Aya").
- Sao chép lặp lại bản dịch của dòng trước cho dòng sau khi hai câu gốc tiếng Nhật khác nhau.
- Mang ý tứ hay từ ngữ của dòng dưới lên dòng trên (chống lây nhiễm ngữ cảnh).
{glossary_block}
{character_block}
Dữ liệu (ORIGINAL là câu gốc, ROUGH là bản dịch thô):
{pairs}
"""


# Fallback khi model trả JSON thiếu phần tử (thường với model 8B): dùng
# numbered list thay vì JSON — cùng pattern literal_translate đã hoạt động ổn.
_POLISH_FALLBACK_TEMPLATE = """\
{system}

Trau chuốt {n} câu dịch sẵn (ROUGH) cho tự nhiên như phụ đề phim Việt.
Vẫn phải giữ đúng NGHĨA gốc, chỉ sửa lỗi diễn đạt/sắc thái — KHÔNG đổi nội dung.
{context_block}
QUY TẮC:
- Dịch lại câu ORIGINAL cho tự nhiên, dùng ROUGH làm gợi ý.
- Khử mùi dịch máy: câu bị động chuyển sang chủ động tự nhiên, thêm thán từ/trợ từ khẩu ngữ (nhé, đấy, mà, hả...).
- Giữ đúng xưng hô và cách gọi nhau nhất quán trong cả danh sách.
- Giữ nguyên nghĩa tình dục/thô như gốc (không làm sạch, không né tránh).
- Độc lập từng dòng: tuyệt đối không lặp lại bản dịch của dòng trước cho dòng sau khi câu gốc khác nhau.
{glossary_block}
{character_block}
OUTPUT FORMAT (BẮT BUỘC):
1. <câu đã trau chuốt>
2. <câu đã trau chuốt>

Trả về đúng {n} dòng, đánh số từ 1. Không thêm text ngoài danh sách.

Dữ liệu:
{pairs}
"""


_QA_TEMPLATE = """\
{system}

Bạn là reviewer. So sánh từng cặp (ORIGINAL gốc tiếng Nhật, TRANSLATION bản dịch tiếng Việt).
Nhiệm vụ: phát hiện "translation drift" — bản dịch lệch nghĩa, bỏ sót thông tin, hoặc còn sót romaji/kana.
{context_block}
QUY TẮC:
- Trả về JSON array. CHỈ liệt kê những dòng CÓ LỖI (ok=false), mỗi phần tử:
  {{"id": <1-based>, "ok": false, "issue": "<tối đa 8 từ>", "suggestion": "<bản dịch đề xuất>"}}
- Nếu tất cả các dòng đều đúng nghĩa, tự nhiên, đúng xưng hô và không sót chữ Nhật/romaji, trả về mảng rỗng: []
- ok=false nếu lệch nghĩa, bỏ sót particle, còn romaji/kana onomatopoeia, hoặc quá máy móc.
- Nếu xưng hô trong TRANSLATION không khớp với bản đồ pronouns_map ở context, đánh ok=false.
- Nếu tên riêng trong TRANSLATION không khớp TEN RIENG (vd: 匠 = Takumi, あゆみ = Ayumi), đánh ok=false.

TUYỆT ĐỐI KHÔNG:
- Liệt kê các dòng đã đúng (ok=true).
- Viết giải thích dài dòng hoặc trả về text ngoài JSON.
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
    """Render context summary (từ JaContextAnalyzer + history liên-window) thành block inject vào prompt polish.

    Nếu context_summary None / rỗng → trả chuỗi rỗng (baseline 3-pass).
    """
    if not context_summary:
        return ""
    from .film_profile import render_profile_block
    profile_block = render_profile_block(context_summary.get("film_profile"))
    history_text = (context_summary.get("history") or "").strip()
    history_block = ""
    if history_text:
        history_block = (
            "CÁC CÂU ĐÃ DỊCH Ở WINDOW TRƯỚC (CHỈ ĐỌC để giữ mạch xưng hô và ngữ cảnh, TUYỆT ĐỐI KHÔNG dịch lại):\n"
            f"{history_text}\n\n"
        )
    tm_examples = context_summary.get("tm_examples") or []
    tm_block = ""
    if tm_examples:
        ex_lines = [
            f"  + {ex['source_text']} -> {ex['target_text']}"
            for ex in tm_examples
            if isinstance(ex, dict) and ex.get("source_text") and ex.get("target_text")
        ]
        if ex_lines:
            tm_block = (
                "VÍ DỤ CÂU TƯƠNG TỰ TỪ BỘ NHỚ DỊCH (tham khảo văn phong và từ vựng):\n"
                + "\n".join(ex_lines)
                + "\n\n"
            )
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
        return profile_block + history_block + tm_block
    return (
        profile_block
        + history_block
        + tm_block
        + "NGỮ CẢNH WINDOW (đã phân tích trước, dùng để chuẩn hóa xưng hô):\n"
        + "\n".join(parts)
        + "\n\n"
    )


def _number_lines(lines: List[str]) -> str:
    return "\n".join(f"{i+1}. {l}" for i, l in enumerate(lines))


# Compact system prompt cho LITERAL pass (chỉ giữ keigo + uncen essentials).
# Full _BASE_SYSTEM_PROMPT + _UNCEN_ADDON quá dài (~2677 chars) → LLM 8B hay
# non-deterministic, đôi khi echo raw Japanese thay vì dịch. Literal pass chỉ cần
# giữ ý chính (keigo + uncen), chi tiết cho POLISH pass xử lý.
_LITERAL_SYSTEM_COMPACT = """\
You are a professional Japanese-to-Vietnamese subtitle translator.

ABSOLUTE RULES (never break):
- Output ONLY Vietnamese text, never Japanese.
- Never return the original Japanese text.
- Never output Japanese characters (あ-ん ア-ン 一-龯).

Output format: numbered list, one translation per line:
1. Vietnamese translation
2. Vietnamese translation

Examples:
Input: こんにちは
Output: 1. Xin chào

Input: ありがとうございます
Output: 1. Cảm ơn rất nhiều

Pronouns: Default "tôi - anh/chị/em/cô". NEVER use "tớ/cậu" for adults or parents (お母さん/娘).
Family: お母さん=mẹ/cô, 娘=con gái (not "cô ấy"), 兄さん/お兄さん=anh, 姉さん/お姉さん=chị (KHÔNG dịch thành "cậu").
Particles: ね=nhỉ, よ=nhấn mạnh, わ=sự ngạc nhiên, ぞ=cảnh báo.
Onomatopoeia: きらきら→lấp lánh, わくわく→hào hứng, ドキドキ→đập thình thịch.
Common mistakes to avoid: 勉強=học (not "cố gắng"), 汽車=xe lửa, 階段=cầu thang."""

# Compact uncen variant cho LITERAL pass.
# Vì sao cần riêng: PROMPTS['vi'] trong pipeline có đầy rule uncen, nhưng source JA
# đi qua ja_translator nên KHÔNG dùng PROMPTS['vi'] → rule uncen đó là code chết.
# Với uncen mode + literal_mode (1-pass), _LITERAL_SYSTEM_COMPACT là prompt DUY NHẤT
# model thấy, mà nó không có rule uncen nào → model tự dịch "bạn tình/lịch sự".
_LITERAL_SYSTEM_COMPACT_UNCEN = """\
You are a professional Japanese-to-Vietnamese subtitle translator for adult (18+) films.

ABSOLUTE RULES (never break):
- Output ONLY Vietnamese text, never Japanese.
- Never output Japanese characters (あ-ん ア-ン 一-龯).
- Output format: numbered list, one translation per line:
1. Vietnamese translation
2. Vietnamese translation

UNCEN MODE (adult content — translate literally with natural explicit spoken tone, do NOT sanitize):
- Use explicit Vietnamese words for sexual content. NEVER substitute polite/soft
  euphemisms: no "bạn tình" (use "người tình"), no "quan hệ" alone for sex
  (use "làm tình"), no "gần gũi" (use "làm tình"),
  no "dễ chịu"/"thoải mái" (use "sướng"), no "lên đỉnh" (use "ra"),
  no "phóng thích" (use "bắn tinh"/"xuất tinh"), no "cương" alone (use "cương cứng"),
  no "mở miệng" for oral (use "bú cu"/"bú cặc"), no "cậu nhỏ" (use "con cặc"/"cu").
- 気持ちいい→sướng quá, いく→ra, セックス→làm tình/chịch, 中出し→bắn vào trong,
  孕む/妊娠→mang thai, 犯す→cưỡng ép/hiếp, 喘ぐ→rên rỉ, 射精→bắn tinh/xuất tinh,
  勃起→cương cứng, 挿入→đút vào/cho vào, フェラ→bú cu/bú cặc, まんこ→bướm/lồn, ちんぽ→con cặc/cu.
- Keep rough/直接的 tone of original dialogue. Do not add politeness or euphemism.
- MANDATORY GLOSSARY entries below are ABSOLUTE RULES — use the exact target word.

Pronouns & Gender Lock: Default "tôi - anh/chị/em/cô". NEVER use "tớ/cậu" for adults, parents (お母さん/娘), or 18+ scenes.
Anatomical Gender: まんこ/おっぱい = female body ("của em/cô/mẹ", NEVER "của anh"); ちんぽ/中出し = male body/action.
Family: お母さん=mẹ/cô, 娘=con gái (not "cô ấy"), 兄さん/お兄さん=anh, 姉さん/お姉さん=chị (KHÔNG dịch thành "cậu").
Particles: ね=nhỉ, よ=nhấn mạnh, わ=sự ngạc nhiên, ぞ=cảnh báo.
Onomatopoeia: きらきら→lấp lánh, わくわく→hào hứng, ドキドキ→đập thình thịch."""


def _get_system_prompt(mode: str = "default") -> str:
    """Trả về system prompt theo mode.

    - "default" / "cen" : chỉ base (cho phim thường, anime, drama)
    - "uncen" : base + addon uncen (cho JAV, phim 18+)
    """
    if (mode or "").lower() == "uncen":
        return _BASE_SYSTEM_PROMPT + _UNCEN_ADDON
    return _BASE_SYSTEM_PROMPT


def _get_literal_system_prompt(mode: str = "default") -> str:
    """Compact system prompt cho LITERAL pass — giảm prompt length ~85% để LLM 8B
    ổn định hơn. Giữ đủ keigo + uncen essentials, chi tiết cho polish pass.

    Với mode uncen, dùng bản có rule uncen tường minh — nếu không, model 8B
    tự dịch "bạn tình" thay vì "người tình" (đây là prompt DUY NHẤT model
    thấy khi literal_mode bật, vì PROMPTS['vi'] không được dùng cho source JA).
    """
    if (mode or "").lower() == "uncen":
        return _LITERAL_SYSTEM_COMPACT_UNCEN
    return _LITERAL_SYSTEM_COMPACT


def _format_pairs(source_lines: List[str], translations: List[str]) -> str:
    return "\n".join(
        f"ORIGINAL: {s}\nTRANSLATION: {t}"
        for s, t in zip(source_lines, translations)
    )


# CHỈ strip dạng numbered-list thật ("1. ", "2) ", "1、"):
# - 1-3 chữ số + [.)] + BẮT BUỘC khoảng trắng, hoặc "N、"
# - KHÔNG đưa ':' và '-' vào class vì sẽ cắt số giờ ("3:00") / khoảng ("20-30 tuổi",
#   "1-2 tiếng") trong câu tiếng Việt hợp lệ (bug review 2026-11-01).
_LEADING_NUM_RE = re.compile(r"^(?:\d{1,3}\s*[\.\)]\s+|\d{1,3}、\s*)+")
_LEADING_ORPHAN_PUNCT_RE = re.compile(r"^[,;:\s]+")


def _is_likely_japanese_translation(text: str) -> bool:
    """True nếu text gần như chắc chắn là bản dịch thất bại (Japanese echo hoặc
    chỉ chứa rất ít tiếng Việt). Dùng để detect literal pass fail thay vì
    dùng heuristic JA-char ratio đơn giản (vì kết quả có thể chứa romaji).
    """
    if not text:
        return True
    # Đếm từ tiếng Việt có dấu hoặc common VN patterns
    vi_chars = sum(1 for c in text if '\u00c0' <= c <= '\u1ef3')
    # Từ tiếng Việt common trong sub
    vi_words = len(re.findall(r'\b(tôi|anh|chị|em|bạn|mình|họ|tao|mày|cậu|thì|là|có|không|và|đã|được|rồi|này|kia|với|cho)\b', text))
    # Nếu có dấu tiếng Việt nhiều → không phải JA echo
    if vi_chars >= 3:
        return False
    # Nếu có ≥2 từ VN common → không phải JA echo
    if vi_words >= 2:
        return False
    # Nếu ≥60% chars là Japanese → echo
    total_chars = sum(1 for c in text if c.isalpha() or '\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff')
    if total_chars == 0:
        return True
    ja_chars = sum(1 for c in text if '\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff')
    return ja_chars / total_chars >= 0.6

# shortcut: strip markdown bold + leading arrows khi LLM 8B leak format
_MARKDOWN_BOLD_RE = re.compile(r"\*{1,3}([^*]+)\*{1,3}")
_LEADING_ARROW_RE = re.compile(r"^[→\-\*\u2022]\s*")

# shortcut: ký tự lạ model 8B hay lỡ tay đổi khi dịch từ Nhật — model đổi sang
# bảng mã lân cận (Thái/Han tự) thay vì giữ nguyên ký tự Nhật, nên regex "có ký
# tự Nhật" không bắt được. Đo ở JUL-409: "giống nhauเลย", "xấu ý too",
# "từ giờ开始", "Đúng rồi呢" (JUL-837) — 4/454 dòng.
# Thái: bất kỳ vị trí (tiếng Việt không bao giờ chứa chữ Thái).
# Han: chỉ đuôi câu — giữ phần đầu vì tên riêng Hán (龍介, 陳) là hợp lệ.
_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
_JUNK_TAIL_RE = re.compile(r"(?:[\u4e00-\u9fff]|too|도|に){1,4}$", re.IGNORECASE)


def _strip_leading_number(line: str) -> str:
    res = _LEADING_NUM_RE.sub("", line or "").strip()
    if _LEADING_ORPHAN_PUNCT_RE.match(res):
        res = _LEADING_ORPHAN_PUNCT_RE.sub("", res).strip()
        if res and res[0].islower():
            res = res[0].upper() + res[1:]
    return res


# Model hay trả kèm chú thích tự nói với mình ("(Giữ nguyên nếu ngữ cảnh phù hợp...)",
# ', hoặc "..."') thay vì bản dịch thuần — đo ở ADN-627 cue 223-228.
_LLM_NOTE_RE = re.compile(
    r"\((?:Giữ|Nếu|Cần|Lưu ý|Có thể|Hoặc|Note)\b|,\s*hoặc\s*[\"“']|xác định xưng hô|ngữ cảnh phù hợp",
    re.IGNORECASE,
)
_OPEN_QUOTES = "\"'“‘"


def looks_like_llm_note(text: str) -> bool:
    """True nếu `text` chứa chú thích model tự nói với mình. Dùng cho TM: thoại mở bằng nháy vẫn hợp lệ."""
    return bool(_LLM_NOTE_RE.search((text or "").strip()))


def looks_like_llm_junk(text: str, reference: str = "") -> bool:
    """True nếu `text` là chú thích của model hoặc bị bọc/mở bằng dấu nháy mà `reference` thì không.

    Dùng để chặn suggestion của QA và chặn ghi TM; không dùng cho bản dịch chính
    (thoại thật có thể mở bằng dấu nháy).
    """
    t = (text or "").strip()
    if not t:
        return False
    if looks_like_llm_note(t):
        return True
    return t[0] in _OPEN_QUOTES and not (reference or "").strip().startswith(tuple(_OPEN_QUOTES))


def _looks_shifted(sug: str, lines: List[str], idx: int,
                   threshold: float = 0.78) -> bool:
    """True nếu `sug` thực chất là bản dịch của DÒNG KỀ BÊN, không phải dòng `idx`.

    Lỗi thực đo được trên NSFS-504 và NSFS-301: QA trả danh sách suggestion có `id` đúng
    nhưng nội dung lệch 1 dòng; áp vào thì cả window bị đẩy lệch và dòng đầu
    bị nhân bản (đúng dấu hiệu `DUP_TRANSLATION line k->k+1`).

    Vì `lines[idx-1]` hoặc `lines[idx+1]` đã mang nội dung của câu đó, suggestion lệch sẽ
    gần trùng với nó (ngưỡng 0.78 bắt được cả trường hợp đổi từ ở đuôi câu).
    """
    if not sug:
        return False
    for nb in (idx - 1, idx + 1):
        if not (0 <= nb < len(lines)):
            continue
        other = (lines[nb] or "").strip()
        if not other:
            continue
        # Trùng khít với dòng kề bên là tín hiệu MẠNH NHẤT của lệch index
        if other == sug:
            return True
        ratio = difflib.SequenceMatcher(None, sug, other).ratio()
        if ratio >= threshold:
            return True
        # Nếu có tiền tố chung dài >= 12 ký tự và ratio >= 0.72 -> cùng 1 ý
        prefix_len = 0
        for c1, c2 in zip(sug, other):
            if c1 != c2:
                break
            prefix_len += 1
        if prefix_len >= 12 and ratio >= 0.72:
            return True
    return False


def _clean_markdown(line: str) -> str:
    r"""Strip markdown bold, leading arrows, giữ text thuần. Áp dụng cho mọi
    LLM output trước khi xuất ra SRT — tránh bug **xxx**, → xxx leak.

    Thêm: dọn 3 dạng leak model 8B hay trả (đo ở JUL-409: 8/255 dòng bẩn):
    - "JA -> VN": LLM lặp cả cặp so sánh → chỉ giữ phần sau mũi tên.
    - Ký tự lạ lõm vào đuôi (เลย/too/开始): model đổi ký tự cuối sang
      bảng mã Thái/Han. Regex \w không bắt vì chúng không thuộc \w.
    """
    if not line:
        return line
    cleaned = _MARKDOWN_BOLD_RE.sub(r"\1", line)
    cleaned = _LEADING_ARROW_RE.sub("", cleaned)
    # "JA -> VN": giữ phần sau mũi tên cuối (bản dịch luôn nằm sau)
    for sep in (' → ', ' -> ', '⇒', '=>'):
        if sep in cleaned:
            cleaned = cleaned.rsplit(sep, 1)[1].strip()
    cleaned = _THAI_RE.sub("", cleaned)
    cleaned = _JUNK_TAIL_RE.sub("", cleaned)
    cleaned = _strip_leading_number(cleaned)
    return cleaned.strip()


_NUMBERED_RE = re.compile(r"^\s*(\d+)\s*[\.\)、]\s*(.*)$")


def _map_numbered(out_lines: List[str], n: int) -> List[Optional[str]]:
    """Ghép output LLM về n slot theo SỐ THỨ TỰ ("3. ..." → slot 3).

    Số nào thiếu/ngoài 1..n → slot None (caller dịch lại riêng); số trùng giữ lần đầu.
    Preamble/dòng không đánh số bị bỏ qua. Nếu không dòng nào có số (prompt cũ) thì
    rơi về ghép theo vị trí sau khi bỏ preamble trước "1.".
    """
    slots: List[Optional[str]] = [None] * n
    found = False
    for line in out_lines:
        m = _NUMBERED_RE.match(line)
        if not m:
            continue
        found = True
        k = int(m.group(1)) - 1
        text = _strip_leading_number(m.group(2).strip())
        if 0 <= k < n and slots[k] is None and text:
            slots[k] = text
    if not found:
        lines = list(out_lines)
        while lines and not lines[0].lstrip().startswith(("1.", "1)", "1、", "1 ")):
            lines.pop(0)
        for k, text in enumerate(lines[:n]):
            slots[k] = text or None
    return slots


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
        # Compact system prompt cho LITERAL pass (~700 chars vs 2677).
        # LLM 8B với prompt ngắn ổn định hơn nhiều so với full system prompt
        # (đã test — full prompt hay echo raw JA non-deterministic).
        self.literal_system_prompt = _get_literal_system_prompt(self.mode)

    # --- Pass 1 ---
    def literal_translate(
        self,
        source_lines: List[str],
        glossary_terms=None,
        history: Optional[str] = None,
        tm_examples: Optional[List[dict]] = None,
    ) -> Optional[List[str]]:
        """Pass 1: dịch literal, giữ keigo/particles/onomatopoeia."""
        glossary_block = _render_glossary(glossary_terms)
        character_block = _render_character_block(self.character_names)
        history_clean = (history or "").strip()
        history_block = (
            f"PREVIOUS TRANSLATED LINES (for pronoun/context reference ONLY, do NOT include in output):\n{history_clean}\n\n"
            if history_clean else ""
        )
        tm_block = ""
        if tm_examples:
            ex_lines = [
                f"- {ex['source_text']} -> {ex['target_text']}"
                for ex in tm_examples
                if isinstance(ex, dict) and ex.get("source_text") and ex.get("target_text")
            ]
            if ex_lines:
                tm_block = "SIMILAR TRANSLATED EXAMPLES (reference style):\n" + "\n".join(ex_lines) + "\n\n"
        translations: List[str] = []

        for chunk_idx, chunk in enumerate(_chunked(source_lines, self.chunk_size)):
            numbered = _number_lines(chunk)
            eff_char_block = (character_block + history_block + tm_block) if chunk_idx == 0 else character_block
            prompt = _LITERAL_TEMPLATE.format(
                system=self.literal_system_prompt,
                n=len(chunk),
                glossary_block=glossary_block,
                character_block=eff_char_block,
                lines=numbered,
            )
            try:
                raw = self.translator.generate(prompt, temperature=0.1)
            except Exception as e:
                logger.warning(f"JaTranslator.literal_translate: generate failed: {e}")
                return None

            # P1-fix: nếu LLM trả JSON array thay vì plain numbered lines,
            # hãy parse JSON. Trước đây split('\n') trên '[{...},{...}]' trả về
            # 1 dòng JSON dài → regex numbered không match → fallback append
            # raw JSON string vào translations → commit "raw JSON" ra SRT.
            if raw and raw.lstrip().startswith(("[", "{")):
                parsed_json = self.translator.extract_json(raw)
                if isinstance(parsed_json, list) and parsed_json:
                    # Map ID → text như output mong đợi (numbered plain text).
                    # Nếu items không có "id", dùng thứ tự.
                    items = parsed_json
                    out_lines = []
                    for idx, item in enumerate(items):
                        if isinstance(item, dict):
                            text = item.get("text") or item.get("translation") or item.get("vietnamese") or ""
                            out_lines.append(f"{idx + 1}. {_strip_leading_number(str(text))}")
                        else:
                            out_lines.append(f"{idx + 1}. {_strip_leading_number(str(item))}")
                    raw = "\n".join(out_lines)
                else:
                    logger.warning("literal_translate: JSON parse failed, falling back to plain text")

            # Detect raw JA echo (LLM non-deterministic với prompt phức tạp).
            # Retry với temperature 0 (deterministic) nếu ≥50% lines có JA chars.
            out_lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
            ja_count = sum(
                1 for l in out_lines
                if any('\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff' for c in l)
            )
            if ja_count >= max(1, len(out_lines) * 0.5):
                logger.warning(
                    f"JaTranslator.literal_translate: {ja_count}/{len(out_lines)} raw-JA lines, "
                    f"retrying with temperature=0 (deterministic)"
                )
                try:
                    raw = self.translator.generate(prompt, temperature=0.0)
                    out_lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
                except Exception as e:
                    logger.debug(f"Deterministic retry failed: {e}")
            # Ghép theo SỐ THỨ TỰ model trả, không theo vị trí: model gộp/bỏ 1 dòng thì
            # chỉ dòng đó thiếu (dịch lại riêng), không làm lệch cả phần còn lại của chunk.
            parsed = _map_numbered(out_lines, len(chunk))
            chunk_out: List[Optional[str]] = []
            for j, original in enumerate(chunk):
                if parsed[j]:
                    chunk_out.append(_clean_markdown(parsed[j]))
                else:
                    # retry từng dòng bằng prompt literal ngắn
                    single = self._translate_single_literal(original, glossary_terms)
                    # shortcut: nếu retry fail thì return None để pipeline biết retry,
                    # KHÔNG fallback về raw Nhật (gây bug "giữ nguyên bản gốc chưa dịch")
                    chunk_out.append(single)

            # Final validation: nếu hơn 50% kết quả của CHUNK NÀY là echo tiếng Nhật → retry cả chunk
            # shortcut: chỉ retry khi chunk đủ lớn (≥4 dòng), tránh retry vô tận với single line
            if len(chunk) >= 4:
                ja_echo_count = sum(
                    1 for t in chunk_out
                    if t and any('\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff' for c in t)
                    and _is_likely_japanese_translation(t)
                )
                if ja_echo_count >= len(chunk) * 0.5:
                    logger.warning(
                        f"JaTranslator.likely JA-echo in literal chunk ({ja_echo_count}/{len(chunk)} lines), "
                        f"retrying entire chunk with temperature=0"
                    )
                    retry_prompt = _LITERAL_TEMPLATE.format(
                        system=self.literal_system_prompt,
                        n=len(chunk),
                        glossary_block=glossary_block,
                        character_block=character_block,
                        lines=_number_lines(chunk),
                    )
                    try:
                        raw_retry = self.translator.generate(retry_prompt, temperature=0.0)
                        retry_out = [l.strip() for l in raw_retry.strip().split("\n") if l.strip()]
                        retry_parsed = _map_numbered(retry_out, len(chunk))
                        # chỉ thay dòng retry có nội dung; dòng thiếu giữ kết quả cũ
                        for j in range(len(chunk)):
                            if retry_parsed[j]:
                                chunk_out[j] = _clean_markdown(retry_parsed[j])
                    except Exception as e:
                        logger.debug(f"Literal chunk retry failed: {e}")

            translations.extend(chunk_out)

        return translations if translations else None

    def _translate_single_literal(self, text: str, glossary_terms=None) -> Optional[str]:
        """Retry 1 dòng literal khi chunk bị thiếu."""
        glossary_block = _render_glossary(glossary_terms)
        prompt = (
            f"{self.literal_system_prompt}\n\n"
            f"Dịch câu tiếng Nhật sau sang tiếng Việt.\n"
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
            except Exception as e:
                # Audit R-1: giữ nguyên fallback nhưng không nuốt im lặng.
                logger.debug(
                    f"JaTranslator.polish_translate: retry generate failed "
                    f"({type(e).__name__}: {e})"
                )
        if not isinstance(parsed, list):
            logger.warning("JaTranslator.polish_translate: no JSON list, keeping rough")
            return self._polish_fallback(source_lines, rough_translations,
                                         glossary_terms, context_block)

        # Fail-fast nếu JSON trả về số phần tử sai — tránh silent mapping sai.
        # Model 8B hay trả thiếu phần tử khi đầu vào >3 dòng, nên thử numbered
        # list trước khi bỏ cuộc (cùng cách literal_translate đã làm).
        if len(parsed) != len(source_lines):
            logger.warning(
                f"JaTranslator.polish_translate: JSON length {len(parsed)} "
                f"!= source {len(source_lines)}, trying numbered-list fallback"
            )
            return self._polish_fallback(source_lines, rough_translations,
                                         glossary_terms, context_block)

        # Map JSON items back to source lines by id
        parsed_by_id: dict = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            idx = item.get("id")
            try:
                idx_int = int(idx) if idx is not None else None
            except (ValueError, TypeError):
                idx_int = None
            if idx_int is not None:
                text = item.get("text") or item.get("translation") or ""
                parsed_by_id[idx_int] = _clean_markdown(text).strip()

        # Final check: nếu kết quả polish vẫn là Japanese → fallback về literal translation
        out_vn: List[str] = []
        for j, original in enumerate(source_lines):
            result = parsed_by_id.get(j + 1, rough_translations[j] if j < len(rough_translations) else "")
            # Nếu result gần như chắc là Japanese echo → retry bằng literal
            if _is_likely_japanese_translation(result):
                logger.debug(f"JaTranslator.polish_translate: line {j} is JA-echo, retry literal")
                single = self._translate_single_literal(original, glossary_terms)
                result = single if single else result
            out_vn.append(result)
        return out_vn

    def _polish_fallback(self, source_lines, rough_translations,
                         glossary_terms=None, context_block=""):
        """Retry polish bằng numbered list khi JSON fail (model 8B hay thiếu item).

        Numbered list dễ hơn JSON cho model nhỏ: chỉ cần N dòng "1. text" thay vì
        phải cân bằng dấu ngoặc + key "id". Trả về rough nếu cả 2 cách đều fail.
        """
        n = len(source_lines)
        pairs_text = _format_pairs(source_lines, rough_translations)
        prompt = _POLISH_FALLBACK_TEMPLATE.format(
            system=self.system_prompt,
            n=n,
            glossary_block=_render_glossary(glossary_terms),
            character_block=_render_character_block(self.character_names),
            context_block=context_block,
            pairs=pairs_text,
        )
        numbered_re = re.compile(r"^\s*(\d+)[.)]\s*(.+)$")
        try:
            raw = self.translator.generate(prompt, temperature=0.1)
        except Exception as e:
            logger.warning(f"JaTranslator.polish fallback generate failed: {e}, keeping rough")
            return rough_translations

        raw_lines = [line.strip() for line in raw.strip().split("\n") if line.strip()]
        numbered_lines = [line for line in raw_lines if numbered_re.match(line)]
        if len(numbered_lines) < max(2, n - 1) and len(numbered_lines) < n:
            logger.warning(
                f"JaTranslator.polish fallback: got {len(numbered_lines)}/{n} lines, keeping rough"
            )
            return rough_translations

        slots = _map_numbered(raw_lines, n)

        # Giữ rough cho dòng bị thiếu (None) hoặc dòng mà fallback trả rỗng/echo tiếng Nhật
        out_vn: List[str] = []
        polished_count = 0
        for j in range(n):
            cand = _clean_markdown(slots[j].strip()) if slots[j] else ""
            if not cand or _is_likely_japanese_translation(cand):
                out_vn.append(rough_translations[j])
            else:
                out_vn.append(cand)
                polished_count += 1
        logger.info(f"JaTranslator.polish fallback OK ({polished_count}/{n} lines via numbered list)")
        return out_vn

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
            # _QA_TEMPLATE yêu cầu key "id"; "index" là schema cũ
            if not isinstance(item, dict) or ("index" not in item and "id" not in item):
                continue
            try:
                idx_int = int(item.get("index", item.get("id", 0)))
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
        """Áp suggestion từ QA pass vào list translations (return list mới).

        Mỗi dòng được quyết định ĐỘC LẬP. Trước đây hàm bỏ qua toàn bộ QA khi
        >50% dòng bị flag, khiến các suggestion hợp lệ của thiểu số cũng bị vứt
        (audit C-1: 500/758 = 66% tổng số flag bị mất trên log production).

        Schema đầu vào: `qa_review()` phát key "index"; một số output LLM cũ dùng
        "id". Đọc cả hai để tương thích.
        """
        if not qa_results:
            return translations

        out = list(translations)
        flagged = sum(1 for it in qa_results if not it.get("ok", True))

        # Tỉ lệ flag cao là tín hiệu ĐỂ SỬA, không phải để bỏ qua — chỉ cảnh báo.
        if len(out) >= 4 and flagged > len(out) / 2:
            logger.warning(
                f"JaTranslator.apply_qa_results: high flag ratio {flagged}/{len(out)} "
                f"— applying per-line fixes anyway"
            )

        applied = 0
        dropped_junk = 0
        dropped_shift = 0
        for item in qa_results:
            if item.get("ok", True):
                continue
            raw_id = item.get("id")
            if raw_id is None:
                raw_id = item.get("index", 0)
            try:
                idx = int(raw_id) - 1
            except (TypeError, ValueError):
                continue
            if not (0 <= idx < len(out)):
                continue
            sug = _clean_markdown(str(item.get("suggestion", "")).strip())
            if not sug:
                continue
            if looks_like_llm_junk(sug, out[idx]):
                logger.warning(
                    f"JaTranslator.apply_qa_results: dropped junk suggestion "
                    f"for line {idx + 1}: {sug[:50]!r}"
                )
                dropped_junk += 1
                continue
            # Chống lệch index: LLM đôi khi trả suggestion của DÒNG BÊN CẠNH
            # (id đúng nhưng nội dung lệch 1). Áp nguyên xi sẽ đẩy cả window lệch
            # một dòng và nhân bản dòng đầu — đúng lỗi DUP_TRANSLATION mà
            # pipeline cảnh báo. Nếu suggestion gần trùng bản dịch của dòng
            # kề bên thì đó là dấu hiệu lệch -> bỏ, giữ bản polish.
            if _looks_shifted(sug, out, idx):
                logger.warning(
                    f"JaTranslator.apply_qa_results: dropped SHIFTED suggestion "
                    f"for line {idx + 1} (matches neighbour): {sug[:50]!r}"
                )
                dropped_shift += 1
                continue
            if out[idx] != sug:
                out[idx] = sug
                applied += 1

        logger.info(
            f"JaTranslator.apply_qa_results: applied={applied} junk={dropped_junk} "
            f"shifted={dropped_shift} flagged={flagged}/{len(out)}"
        )
        return out

    # --- helpers ---
    def _safe_extract_json(self, text: str):
        """Use shared JSON extraction (P1-fix: unify 3 copies with subtle drift)."""
        from .json_utils import extract_json_array
        try:
            return extract_json_array(text)
        except Exception:
            return None
