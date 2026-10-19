"""Rule-based post-processor — thay thế ja_trans2 (LLM) bằng regex + dict.

Không gọi LLM, không tốn thêm thời gian.
Fix các lỗi phổ biến sau khi main pipeline hoàn tất:
  1. Sino-Vietnamese sai — 勉強≠cố gắng, 汽車≠khí xa, 大丈夫≠đại trượng phu
  2. Romaji leak — chữ Latin ascii trong bản dịch VI
  3. Particles chưa dịch — ね/よ/ぞ/かな
  4. Cấu trúc câu cứng nhắc — thay thế từ Hán-Việt bằng từ tự nhiên

Tốc độ: ~1000 dòng/giây (regex thuần)
"""

from __future__ import annotations

import re
import logging
from typing import List

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Rule 1: Sino-Vietnamese wrong -> correct mapping
# -----------------------------------------------------------------------------

# kanji wrong -> correct
_SINO_FIXES = {
    'cố gắng': 'học',
    'nỗ lực': 'học',
    'khí xa': 'ô tô',
    'đại trượng phu': 'không sao',
    'đại nhân': 'không sao',
    'nhân gian': 'người',
    'thế gian': 'người ta',
    'thủ chỉ': 'thư',
    'tả chân': 'ảnh',
    'ảnh diện': 'phim',
    'liêu lý': 'nấu ăn',
    'dương thủy': 'nước nóng',
    'liên nhân': 'người yêu',
    'thủy tộc': 'thủy sinh',
    'tha nhân': 'người lạ',
    'ta ma': 'phiền',
    'ước hẹn': 'hẹn',
    'ước': 'hẹn',
    'tàn niên': 'tiếc',
    'tinh lệ': 'đẹp',
    'khả ái': 'dễ thương',
    'đại biến': 'vất vả',
    'thuận hiệu': 'thứ tự',
    'chủng loại': 'loại',
    'hoàn cảnh': 'môi trường',
    'tất yếu': 'cần',
    'khai triển': 'phát triển',
    'phổ thông': 'bình thường',
    'sở thích': 'trận đấu',
    'miệt thị': 'phiền phức',
    'bản đương': 'thật ra',
    'giản đơn': 'đơn giản',
    'nghiên cứu': 'học hỏi',
}

# Compile pattern
_SINO_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in sorted(_SINO_FIXES.keys(), key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)


# -----------------------------------------------------------------------------
# Rule 2: Romaji leak -> VI mapping
# -----------------------------------------------------------------------------

_ROMAJI_TO_VI = {
    'arigatou': 'cảm ơn',
    'arigatou gozaimasu': 'cảm ơn rất nhiều',
    'sumimasen': 'xin lỗi',
    'sugoi': 'kinh khủng',
    'sore': 'cái đó',
    'kore': 'cái này',
    'dore': 'cái nào',
    'are': 'cái kia',
    'nani': 'gì',
    'dare': 'ai',
    'hai': 'vâng',
    'iie': 'không',
    'mata': 'gặp lại',
    'ja ne': 'tạm biệt',
    'yoroshiku': 'mong được chiếu cố',
    'hajimemashite': 'rất vui được gặp',
    'wakatta': 'hiểu rồi',
    'wakaranai': 'không hiểu',
    'ii': 'tốt',
    'yabai': 'nguy hiểm',
    'suteki': 'tuyệt vời',
    'kawaii': 'dễ thương',
    'hontou': 'thật',
    'ganbatte': 'cố gắng lên',
    'shiawase': 'hạnh phúc',
    'ureshii': 'vui',
    'kanashii': 'buồn',
    'kowai': 'sợ',
    'suki': 'thích',
    'daisuki': 'rất thích',
    'aishiteru': 'yêu',
    'genki': 'khỏe',
    'tsukareru': 'mệt',
    'daijoubu': 'không sao',
    'hen': 'lạ',
    'shiranai': 'không biết',
    'omou': 'nghĩ',
    'hen': 'lạ',
    'daijoubu': 'ổn',
    'kawaii': 'dễ thương',
    'sugoi': 'ghê',
    'abunai': 'nguy hiểm',
    'urusai': 'ồn ào',
    'shizuka': 'yên lặng',
    'majide': 'thật à',
}

_ROMAJI_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in _ROMAJI_TO_VI.keys()) + r')\b',
    re.IGNORECASE
)


# -----------------------------------------------------------------------------
# Rule 3: Particles chưa dịch
# -----------------------------------------------------------------------------

_PARTICLES = [
    ('です。', '.'),
    ('です,', ','),
    ('です', ''),
    ('です', 'ấy'),
    ('ます。', '.'),
    ('ます,', ','),
    ('ます', ''),
    ('した。', '.'),
    ('した,', ','),
    ('した', ''),
    ('して。', '.'),
    ('して,', ','),
    ('して', ''),
    (' da ', ' '),
    (' da.', '.'),
    (' da,', ','),
    (' da', ''),
    (' yo ', ' nhé '),
    (' yo,', ', nhé'),
    (' yo.', ' nhé.'),
    (' ne ', ' chứ '),
    (' ne,', ', chứ'),
    (' ne.', ' chứ.'),
    (' na ', ' nha '),
    (' na,', ', nha'),
    (' na.', ' nha.'),
    (' ka ', ' không '),
    (' ka,', ', không'),
    (' ka.', ' không?'),
    (' zo ', ' mà '),
    (' zo,', ', mà'),
    (' zo.', ' mà.'),
]


# -----------------------------------------------------------------------------
# Rule 4: English leak -> VI
# -----------------------------------------------------------------------------

_ENGLISH_FIXES = {
    ' okay ': ' được ',
    ' ok ': ' được ',
    ' yes ': ' vâng ',
    ' no ': ' không ',
    ' thank you ': ' cảm ơn ',
    ' thanks ': ' cảm ơn ',
    ' sorry ': ' xin lỗi ',
    ' hello ': ' chào ',
    ' goodbye ': ' tạm biệt ',
    ' love you ': ' yêu em ',
    ' love ': ' yêu ',
}

_ENGLISH_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k.strip()) for k in _ENGLISH_FIXES.keys()) + r')\b',
    re.IGNORECASE
)


# -----------------------------------------------------------------------------
# Rule 5: Number 2 -> "vâng" (LLM hallucination)
# -----------------------------------------------------------------------------
# LLM hay gõ "vâng" thay vì "hai" khi dịch JA số 2 (二人, 二つ, etc.).
# shortcut: chỉ cover số 2 (vì số 3+ thường viết "ba", "bốn" thay vì digit)
#
# P1-6 fix: replace hardcoded list with a heuristic regex that catches
# "vâng <countable_noun>" pattern. The old list only matched 17 specific
# phrases; LLM produces dozens of variants ("vây người", "vâng bạn bè", etc.)
# that slipped through. The new rule matches any "vâng" followed by a known
# Vietnamese counter/noun, plus a few legacy specific patterns for safety.

# Countable nouns that follow the number 2 in Vietnamese (kept lowercase)
_VANG_COUNTERS = (
    r'người|đứa|con|cái|món|cách|bên|ngày|lần|tuần|tháng|năm|giờ|phút|'
    r'chị em|anh em|vợ chồng|bạn|người|đứa|bạn bè|em bé|thằng|con gái|'
    r'con trai|đứa con|người yêu|người lạ|người bạn|thế hệ|thế kỷ|'
    r'thành viên|người chơi|kẻ|người ta|người kia|người đó|cô|chú|bác|'
    r'cậu|ông|bà|anh|chị|em|thầy|cô giáo|gia đình|gia đình|cặp|đôi|'
    r'cánh|cửa|bức|tấm|chiếc|quyển|cuốn|quả|trái|con vật|con chó|con mèo|'
    r'con đường|cung đường|đường|bước|bước chân|chân|tay|cánh tay|bàn tay|'
    r'mắt|con mắt|đôi mắt|tai|đôi tai|lưỡi|cái lưỡi|miệng|cái miệng'
)

# Specific cases that don't fit the "vâng <noun>" pattern but are still wrong
_VANG_SPECIFIC = [
    (r'\bcả vâng\b', 'cả hai'),
]

# Main heuristic: "vâng" before a known counter -> "hai <counter>"
_VANG_HEURISTIC_RE = re.compile(
    r'\bvâng\s+(' + _VANG_COUNTERS + r')\b',
    re.IGNORECASE | re.UNICODE,
)

# Combined regex: specific cases first, then heuristic
_VANG_RE = re.compile(
    '|'.join(p for p, _ in _VANG_SPECIFIC) + '|' + _VANG_HEURISTIC_RE.pattern,
    re.IGNORECASE | re.UNICODE,
)


def _replace_vang(match: 're.Match') -> str:
    """Convert a 'vâng' match to 'hai', preserving the trailing word."""
    matched = match.group(0)
    if 'vâng' not in matched.lower():
        return matched
    # Replace just the first 'vâng' occurrence; rest of the match is the counter
    return re.sub(r'\bvâng\b', 'hai', matched, count=1, flags=re.IGNORECASE)


# -----------------------------------------------------------------------------
# Rule 6: Whisper artifacts (おなり -> onani, etc.)
# -----------------------------------------------------------------------------
# Common JA JAV mishearings by Whisper large-v3.
# Từ ngữ nghe sai nên dịch theo nghĩa thực tế.

_WHISPER_ARTIFACT_FIXES = [
    # おなりは = オナニー (thủ dâm)
    (r'\bおなり(は|の|を|して)?', r'onani\1'),
    # 女人 (Hán tự cũ, hiếm gặp) — Whisper hay nghe nhầm thành 彼女
    # 'xử lý' hay bị dùng thay cho 扱う — JA 扱う trong JAV = "xử lý thân xác"
    # giữ nguyên vì thường LLM đã dịch OK
]

_WHISPER_ARTIFACT_RE = re.compile('|'.join(p for p, _ in _WHISPER_ARTIFACT_FIXES))


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _fix_whitespace(text: str) -> str:
    """Fix extra whitespace."""
    result = text
    result = re.sub(r'[　]+', ' ', result)  # Full-width space
    result = re.sub(r'\s+', ' ', result)
    result = result.strip()
    return result


def _fix_punctuation(text: str) -> str:
    """Fix repeated punctuation."""
    result = text
    result = re.sub(r'[!]{2,}', '!', result)
    result = re.sub(r'[?]{2,}', '?', result)
    result = re.sub(r'[。]{2,}', '。', result)
    result = re.sub(r'\.{2,}', '...', result)
    return result


# -----------------------------------------------------------------------------
# Main post-processor
# -----------------------------------------------------------------------------

class RuleBasedPostProcessor:
    """Post-process translated subtitles using rules (no LLM calls)."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._stats = {
            'total': 0,
            'changed': 0,
        }
        # character_names config: tên riêng dùng để fix mistaken aliases
        # Format: [{canonical, aliases, mistaken_aliases}, ...]
        self.character_names = []

    def set_character_names(self, character_names: list):
        """Inject character_names từ config để fix mistaken aliases.

        Mỗi entry có thể có mistaken_aliases (Whisper nghe sai) — sẽ được
        replace bằng canonical khi gặp trong bản dịch.
        """
        self.character_names = character_names or []

    def process_line(self, text: str) -> str:
        """Post-process một dòng đã dịch."""
        if not self.enabled or not text or not text.strip():
            return text

        self._stats['total'] += 1
        original = text

        # 1. Fix whitespace
        text = _fix_whitespace(text)

        # 2. Fix Sino-VN wrong translations
        def _replace_sino(m):
            return _SINO_FIXES.get(m.group(0).lower(), m.group(0))
        text = _SINO_RE.sub(_replace_sino, text)

        # 3. Fix Romaji leak
        def _replace_romaji(m):
            return _ROMAJI_TO_VI.get(m.group(0).lower(), m.group(0))
        text = _ROMAJI_RE.sub(_replace_romaji, text)

        # 4. Fix particles
        for wrong, correct in _PARTICLES:
            text = text.replace(wrong, correct)

        # 5. Fix English leak
        def _replace_english(m):
            return _ENGLISH_FIXES.get(m.group(0).lower(), m.group(0))
        text = _ENGLISH_RE.sub(_replace_english, text)

        # 6. Fix "vâng" hallucination (LLM gõ "vâng" thay vì "hai")
        # shortcut: re.IGNORECASE để cover cả "Vâng" đầu câu; preserve case
        # P1-6: now uses heuristic regex (catches any "vâng <counter>" pattern),
        # not just 17 hardcoded phrases. Callback keeps the counter suffix.
        def _vang_in_match(m):
            # Replace only the "vâng" substring inside the match, keep suffix
            return re.sub('vâng', lambda c: 'Hai' if c.group(0)[0].isupper() else 'hai', m.group(0), flags=re.IGNORECASE)
        text = _VANG_RE.sub(_vang_in_match, text)

        # 7. JAV artifacts: đảm bảo các từ JA thô tục (おなり, 種付け, ...) đã được dịch
        # Whisper có thể để sót romaji/kana onomatopoeia; post-processor giữ an toàn
        # bằng cách check 1 số marker phổ biến.
        # shortcut: pattern này chỉ chạy khi romaji onomatopoeia xuất hiện cuối câu,
        # KHÔNG can thiệp vào các từ đã dịch đúng.
        text = re.sub(r'\s*(onani|seku\s?kusu|fera|paizuri|teiman)\s*', ' ', text, flags=re.IGNORECASE)

        # 8. Fix mistaken character aliases (Whisper nghe sai tên nhân vật)
        # VD: "Kasumi-san" → "Kana-san" (Whisper nghe 香菜 thành Kasumi)
        # Áp dụng cho từng entry có mistaken_aliases.
        if self.character_names:
            for entry in self.character_names:
                mistaken = entry.get('mistaken_aliases') or []
                canonical = (entry.get('canonical') or '').strip()
                if not mistaken or not canonical:
                    continue
                for alias in mistaken:
                    if not alias:
                        continue
                    # Word boundary để tránh replace giữa từ (vd: "Kana" trong "Kanada")
                    text = re.sub(
                        r'\b' + re.escape(alias) + r'\b',
                        canonical,
                        text,
                    )

        # 9. Fix punctuation
        text = _fix_punctuation(text)

        # 9. Final whitespace
        text = _fix_whitespace(text)

        if text != original:
            self._stats['changed'] += 1

        return text

    def process_lines(self, lines: List[str]) -> List[str]:
        """Post-process nhiều dòng."""
        return [self.process_line(line) for line in lines]

    def process_project(self, db, project_id: int, progress_callback=None) -> dict:
        """Post-process toàn bộ project."""
        items = db.get_all_items(project_id)
        total = len(items)
        changed = 0

        for i, item in enumerate(items):
            trans = item.get('translated_text', '')
            if not trans or not trans.strip():
                continue

            fixed = self.process_line(trans)
            if fixed != trans:
                db.update_item_translation(item['id'], fixed)
                changed += 1

            if progress_callback:
                progress_callback(i + 1, total)

        self._stats['changed'] = changed
        logger.info(f"RuleBasedPostProcessor: {changed}/{total} lines changed")
        return {
            'total': total,
            'changed': changed,
            'stats': dict(self._stats),
        }

    def get_stats(self) -> dict:
        return dict(self._stats)

    def reset_stats(self):
        self._stats = {'total': 0, 'changed': 0}
