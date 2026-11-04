"""Whisper artifact normalizer — chuẩn hóa output Whisper trước khi dịch.

Whisper hay để lại romaji/kana đơn lẻ (うん, ありがとう, 塩, やめて) khi
nghe rõ audio. Nếu để nguyên sẽ lọt vào bản dịch gây "chưa dịch" như JUR-859.

Pre-processor này thay thế bằng tiếng Việt tương đương TRƯỚC khi LLM dịch.
- Tiết kiệm LLM cost (không phải dịch các từ 1-2 chars)
- Bám sát nghĩa gốc (literal translation)
- Tránh LLM tự dịch sai ngữ cảnh (vd: 塩 = "muối" hay "nghề"?)

Tham khảo lỗi JUR-859: 4 dòng JA có うん/ありがとう/塩 bị dịch trễ/lệch.
Sau khi fix: 塩? → "Muối à?" thẳng vào database, không qua LLM.
"""

from __future__ import annotations

import re
import logging
from typing import List

logger = logging.getLogger(__name__)


# Bảng map romaji/kana → tiếng Việt literal
# Chỉ chứa các từ Whisper hay để nguyên (1-3 chars kana, thường gặp)
# Ưu tiên: từ thường gặp, có nghĩa rõ ràng
_WHISPER_KANA_FIXES = [
    # Đồng ý / lịch sự
    (r'うん+', 'Ừm'),                # うん うんうん → "Ừm" (giữ lặp vì JA đôi khi lặp cho tự nhiên)
    (r'ううん', 'Không không'),       # ううん (lắc đầu, khác うん)
    (r'はい+', 'Vâng'),
    (r'いいえ', 'Không'),
    (r'ありがとう+', 'Cảm ơn'),
    (r'ありがとうございます', 'Cảm ơn rất nhiều'),
    (r'すみません', 'Xin lỗi'),
    (r'すいません', 'Xin lỗi'),       # Whisper hay viết sai thành すいません
    (r'ごめんなさい', 'Xin lỗi'),
    (r'ごめん', 'Xin lỗi'),

    # Cảm xúc / trạng thái
    (r'すごい+', 'Tuyệt vời'),
    (r'やばい+', 'Nguy hiểm quá'),    # hay dùng = "tuyệt" trong slang nhưng mặc định literal
    (r'だめ+', 'Không được'),
    (r'やめて+', 'Dừng lại'),
    (r'やだ+', 'Không muốn'),
    (r'いや+', 'Không'),
    (r'きもい+', 'Ghê quá'),          # 気持ち悪い slang
    (r'気持ち悪い+', 'Ghê quá'),
    (r'気持ちいい+', 'Sướng quá'),
    (r'気持ちいい', 'Sướng quá'),
    (r'気持ちよく', 'Sướng'),
    (r'うそ+', 'Thật à'),             # 嘘 (nói dối) → "Thật à?" (phản ứng nghe)
    (r'まじで', 'Thật à'),            # まじ (slang)
    (r'まじか', 'Thật à'),

    # Đồ vật / đồ ăn
    (r'塩+', 'Muối'),
    (r'お塩', 'Muối'),

    # Đau / sướng (JAV context)
    (r'痛い+', 'Đau quá'),
    (r'いたい+', 'Đau quá'),
    (r'いく+', 'Ra đi'),              # orgasm context
    (r'イク+', 'Ra đi'),
    (r'逝く+', 'Ra đi'),
    (r'いくい', 'Sắp ra'),
    (r'あっ+', 'Anh'),
    (r'んっ+', 'Ừ'),
    (r'えっ+', 'Hả'),
    (r'はぁ+', 'Hừm'),
    (r'きゃっ+', 'Anh ơi'),
    (r'もう+', 'Thôi mà'),
    (r'もっと+', 'Mạnh hơn'),
]

# Compile thành 1 regex (ưu tiên match dài hơn trước)
_WHISPER_KANA_PATTERN = re.compile(
    '|'.join(f'({p})' for p, _ in _WHISPER_KANA_FIXES)
)


# Sửa lỗi đồng âm tiếng Nhật (JA -> JA) mà Whisper hay ghi sai chữ Hán trong câu dài
_WHISPER_HOMOPHONE_FIXES = [
    ('教授に仕上げ', '今日中に仕上げ'),
    ('教授に終わらせ', '今日中に終わらせ'),
    ('激無', '激務'),
    ('家庭を借りみなかった', '家庭を顧みなかった'),
    ('仲出し', '中出し'),
    ('中だし', '中出し'),
    ('寝引き', '値引き'),
    ('おなでたん', 'オナネタ'),
    ('おなねた', 'オナネタ'),
    ('閲覧車', '閲覧者'),
    # ROE-459: 媚薬 (biyaku - thuốc kích dục) bị ghi nhầm thành 美薬 (thuốc đẹp) / 美役 (vai đẹp)
    ('美薬', '媚薬'),
    ('美役', '媚薬'),
    # ROE-459: tên nhân vật しんぼう bị ghi thành 辛抱 (nhẫn nại) khi đi kèm lời cảm ơn/gọi tên
    ('辛抱ありがとう', 'しんぼう、ありがとう'),
    # ROE-459: しんぼう bị Whisper tách thành 彼氏 陰謀 (đồng âm kareshi inbō) -> "bạn trai anh ấy"
    ('彼氏 陰謀', 'しんぼう'),
    ('彼氏陰謀', 'しんぼう'),
    # Các fix riêng của từng phim (ADN-780, ...) KHÔNG nằm ở đây nữa —
    # xem film_fixes_<PROJECT>.yaml + core/film_fixes.py (review 2026-11-01).
]

# Khôi phục âm tiết đầu câu bị Whisper VAD cắt cụt (Initial Mora Clipping Recovery)
# Chỉ giữ các tiền tố GENERIC; entry gắn với nội dung 1 phim đã chuyển sang film_fixes_*.yaml.
_INITIAL_MORA_CLIP_FIXES = [
    (re.compile(r'^\s*しぶりに'), '久しぶりに'),
    (re.compile(r'^\s*きい声'), '大きい声'),
    (re.compile(r'^\s*持ちよくして'), '気持ちよくして'),
    (re.compile(r'^\s*けてください'), '開けてください'),
    (re.compile(r'^\s*じめまして'), 'はじめまして'),
    (re.compile(r'^\s*まれた時のこと'), '産まれた時のこと'),
    (re.compile(r'^\s*っち\s*確認しましょう'), 'こっち確認しましょう'),
]


def _normalize_whisper_text(text: str, film_fixes: dict | None = None) -> str:
    """Chuẩn hóa 1 dòng text từ Whisper.

    - Sửa các từ đồng âm chữ Hán bị Whisper ghi nhầm (VD: 教授に仕上げ -> 今日中に仕上げ, 激無 -> 激務)
    - Áp thêm film_fixes (homophone + mora_clip) riêng của phim nếu có
    - CHỈ thay kana->VI khi text ngắn (<=10 chars kana/romaji) — Whisper để sót nguyên
    - KHÔNG thay kana->VI giữa câu dài (sẽ gây JA + VI lẫn lộn)
    - Trim space thừa
    """
    if not text or not text.strip():
        return text

    if film_fixes:
        for clip_re, clip_rep in film_fixes.get('mora_clip') or []:
            if clip_re.search(text):
                text = clip_re.sub(clip_rep, text, count=1)

    for clip_re, clip_rep in _INITIAL_MORA_CLIP_FIXES:
        if clip_re.search(text):
            text = clip_re.sub(clip_rep, text, count=1)

    for wrong_ja, right_ja in _WHISPER_HOMOPHONE_FIXES:
        if wrong_ja in text:
            text = text.replace(wrong_ja, right_ja)

    if film_fixes:
        for wrong_ja, right_ja in film_fixes.get('homophone') or []:
            if wrong_ja in text:
                text = text.replace(wrong_ja, right_ja)

    # Check: text có chứa kanji không? Nếu có → câu dài có cấu trúc, KHÔNG normalize
    # (chỉ những câu toàn hiragana/katakana ngắn Whisper mới để sót)
    has_kanji = any('\u4e00' <= ch <= '\u9fff' for ch in text)

    # Nếu text có dấu câu giữa (, 。 ! ? 、) → câu có cấu trúc, KHÔNG normalize
    # (vd: "ああ、はい。" có 2 phần, "すごいですね。" có 1 phần + 2 từ → câu)
    if any(p in text for p in ['、', '。', '!', '?', '！', '？']):
        # Trừ khi text rất ngắn (1-3 chars) và CHỈ có 1 dấu câu cuối
        stripped = text.rstrip('。.,!?！？')
        if len(stripped) > 3:
            return text.strip()
        if len(stripped) == 0:
            return text.strip()
        # stripped 1-3 chars + dấu câu cuối → OK normalize (vd: "うん。" → "Ừm。")

    # Đếm số chars: nếu > 6 chars kana/romaji → có thể là câu có cấu trúc
    text_clean = text.rstrip('。.,!?！？')
    if has_kanji or len(text_clean) > 6:
        return text.strip()

    result = text.strip()
    for pattern, replacement in _WHISPER_KANA_FIXES:
        m = re.match(r'^(?:' + pattern + r')([。.,!?！？…~～ー\s]*)$', result)
        if m:
            result = replacement + (m.group(1) or '')
            break

    # Collapse multiple spaces
    result = re.sub(r'\s+', ' ', result).strip()
    return result


def normalize_srt_lines(text: str) -> str:
    """Chuẩn hóa toàn bộ SRT text (giữ nguyên numbering, timestamps).

    Input: toàn bộ nội dung file SRT
    Output: SRT đã normalize phần text
    """
    if not text:
        return text
    lines = text.split('\n')
    out = []
    in_text = False
    for line in lines:
        # Phát hiện block: index line (số), time line, text line
        stripped = line.strip()
        if re.match(r'^\d+$', stripped):
            in_text = False
            out.append(line)
        elif re.match(r'^\d{2}:\d{2}:\d{2}', stripped):
            in_text = True
            out.append(line)
        elif in_text and stripped:
            out.append(_normalize_whisper_text(line))
        else:
            out.append(line)
    return '\n'.join(out)


def normalize_items(items: list, film_fixes: dict | None = None) -> list:
    """Chuẩn hóa text trong list of dict items (từ parser).

    items: [{'index': 1, 'start': ..., 'end': ..., 'text': 'うん。'}, ...]
    film_fixes: dict từ core.film_fixes.load_film_fixes(project_name) — tùy chọn,
                chỉ áp fix riêng của phim (homophone/mora_clip), không ảnh hưởng phim khác.
    """
    fixed = 0
    for it in items:
        old = it.get('text', '')
        new = _normalize_whisper_text(old, film_fixes=film_fixes)
        if new != old:
            it['text'] = new
            fixed += 1
    if fixed:
        logger.info(f"WhisperNormalizer: fixed {fixed} items (romaji/kana -> VI)")
    return items
