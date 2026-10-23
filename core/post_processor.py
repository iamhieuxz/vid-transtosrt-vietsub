"""Rule-based post-processor — thay thế ja_trans2 (LLM) bằng regex + dict.

Không gọi LLM, không tốn thêm thời gian.
Fix các lỗi phổ biến sau khi main pipeline hoàn tất:
  1. Sino-Vietnamese calques sai (khí xa, thủ chỉ, tả chân...) + sửa theo ngữ cảnh JA
  2. Romaji leak — từ Nhật viết Latin còn sót trong bản dịch VI
  3. Kana particles chưa dịch — です/ます/した/して
  4. Thực thi luật cấm (forbidden euphemisms) từ glossary_uncen khi câu gốc JA có từ khoá

Tốc độ: ~1000 dòng/giây (regex thuần)
"""

from __future__ import annotations

import re
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Rule 1: Sino-Vietnamese wrong -> correct mapping
# Chỉ giữ các cụm Hán-Việt dịch ghép từng chữ KHÔNG tồn tại tự nhiên trong tiếng Việt.
# Các từ tiếng Việt bình thường ("cố gắng", "sở thích", "ước", "hoàn cảnh"...)
# TUYỆT ĐỐI không thay mù quáng, chỉ thay khi kiểm tra câu gốc JA ở _SOURCE_CONDITIONED_FIXES.
# -----------------------------------------------------------------------------

_SINO_FIXES = {
    'khí xa': 'ô tô',
    'đại trượng phu': 'không sao',
    'thủ chỉ': 'thư',
    'tả chân': 'ảnh',
    'liêu lý': 'nấu ăn',
    'dương thủy': 'nước nóng',
    'tàn niên': 'đáng tiếc',
    'tinh lệ': 'đẹp',
    'thuận hiệu': 'thứ tự',
    'bản đương': 'thật ra',
}

_SINO_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in sorted(_SINO_FIXES.keys(), key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)

# Sửa lỗi dịch Hán-Việt & từ né tránh (uncen) KHI VÀ CHỈ KHI câu gốc JA có chứa từ khoá tương ứng
_SOURCE_CONDITIONED_FIXES = [
    (re.compile(r'勉強'), re.compile(r'\b(cố gắng|nỗ lực|nghiên cứu)\b', re.IGNORECASE), 'học'),
    (re.compile(r'大丈夫'), re.compile(r'\b(đại trượng phu|đại nhân)\b', re.IGNORECASE), 'không sao'),
    (re.compile(r'手紙'), re.compile(r'\bthủ chỉ\b', re.IGNORECASE), 'thư'),
    (re.compile(r'写真'), re.compile(r'\btả chân\b', re.IGNORECASE), 'ảnh'),
    (re.compile(r'料理'), re.compile(r'\bliêu lý\b', re.IGNORECASE), 'nấu ăn'),
    (re.compile(r'精液'), re.compile(r'\btinh trùng\b', re.IGNORECASE), 'tinh dịch'),
    (re.compile(r'射精'), re.compile(r'\bphóng thích\b', re.IGNORECASE), 'xuất tinh'),
    (re.compile(r'(セフレ|セックスフレンド|愛人)'), re.compile(r'\bbạn tình\b', re.IGNORECASE), 'người tình'),
    (re.compile(r'(浮気|不倫)'), re.compile(r'\blừa đảo\b', re.IGNORECASE), 'ngoại tình'),
    (re.compile(r'(気持ちいい|気持ち良|気持ちよ)'), re.compile(r'\b(dễ chịu|thoải mái|cảm giác tốt|cảm xúc cũng tốt|cảm giác tuyệt vời)\b', re.IGNORECASE), 'sướng'),
    (re.compile(r'勃起'), re.compile(r'\bhưng phấn\b', re.IGNORECASE), 'cương cứng'),
    (re.compile(r'喘'), re.compile(r'\bthở dài\b', re.IGNORECASE), 'rên rỉ'),
    (re.compile(r'(孕|妊娠)'), re.compile(r'\bcó em bé\b', re.IGNORECASE), 'mang thai'),
    (re.compile(r'オナニー'), re.compile(r'\btự sướng\b', re.IGNORECASE), 'thủ dâm'),
    (re.compile(r'バック'), re.compile(r'\bhậu môn\b', re.IGNORECASE), 'tư thế từ phía sau'),
    (re.compile(r'彼氏'), re.compile(r'\banh trai\b', re.IGNORECASE), 'bạn trai'),
    (re.compile(r'お水'), re.compile(r'\bnước tiểu\b', re.IGNORECASE), 'nước uống'),
]


# -----------------------------------------------------------------------------
# Rule 2: Romaji leak -> VI mapping
# Loại bỏ các từ ngắn trùng tiếng Việt / tiếng Anh ("hai", "ii", "are", "hen", "suki"...)
# Chỉ giữ các từ romaji đa âm tiết đặc trưng của tiếng Nhật.
# -----------------------------------------------------------------------------

_ROMAJI_TO_VI = {
    'arigatou gozaimasu': 'cảm ơn rất nhiều',
    'arigatou': 'cảm ơn',
    'sumimasen': 'xin lỗi',
    'yoroshiku': 'mong được chiếu cố',
    'hajimemashite': 'rất vui được gặp',
    'wakatta': 'hiểu rồi',
    'wakaranai': 'không hiểu',
    'yabai': 'nguy hiểm',
    'suteki': 'tuyệt vời',
    'kawaii': 'dễ thương',
    'hontou': 'thật',
    'ganbatte': 'cố gắng lên',
    'shiawase': 'hạnh phúc',
    'ureshii': 'vui',
    'kanashii': 'buồn',
    'kowai': 'sợ',
    'daisuki': 'rất thích',
    'aishiteru': 'yêu',
    'tsukareru': 'mệt',
    'daijoubu': 'không sao',
    'shiranai': 'không biết',
    'sugoi': 'ghê',
    'abunai': 'nguy hiểm',
    'urusai': 'ồn ào',
    'shizuka': 'yên lặng',
    'majide': 'thật à',
}

_ROMAJI_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in sorted(_ROMAJI_TO_VI.keys(), key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)


# -----------------------------------------------------------------------------
# Rule 3: Particles chưa dịch (chỉ xoá đuôi kana Nhật bị sót, KHÔNG xoá chữ Latin
# như "da", "na", "ka" vì sẽ làm hỏng từ tiếng Việt như "làn da", "da thịt").
# -----------------------------------------------------------------------------

_PARTICLES = [
    ('です。', '.'),
    ('です,', ','),
    ('です', ''),
    ('ます。', '.'),
    ('ます,', ','),
    ('ます', ''),
    ('した。', '.'),
    ('した,', ','),
    ('した', ''),
    ('して。', '.'),
    ('して,', ','),
    ('して', ''),
]

# Rule 3b: Chinese/Japanese CJK phrase leaks từ model Qwen/Hunyuan trong câu tiếng Việt
_CJK_LEAK_FIXES = [
    ('绝对不会', ' tuyệt đối không '),
    ('絶対不会', ' tuyệt đối không '),
    ('絶対に', ' tuyệt đối '),
    ('绝对', ' tuyệt đối '),
    ('原来是', ' thì ra là '),
    ('原来', ' thì ra '),
    ('其实', ' thật ra '),
    ('因为', ' vì '),
    ('所以', ' nên '),
    ('但是', ' nhưng '),
    ('不过', ' tuy nhiên '),
    ('如果', ' nếu '),
    ('虽然', ' mặc dù '),
    ('已经', ' đã '),
    ('一直', ' luôn '),
    ('馬上', ' ngay lập tức '),
    ('马上', ' ngay lập tức '),
    ('真的', ' thật sự '),
    ('非常', ' rất '),
    ('完全', ' hoàn toàn '),
    ('肯定', ' chắc chắn '),
    ('到底', ' rốt cuộc '),
    ('难道', ' chẳng lẽ '),
    ('居然', ' lại có thể '),
    ('竟然', ' lại có thể '),
]
_CJK_RE = re.compile(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+')
_VI_CHARS_RE = re.compile(r'[a-zA-ZàáảãạăâêôơưđÀÁẢÃẠĂÂÊÔƠƯĐ]')


# -----------------------------------------------------------------------------
# Rule 4: English leak -> VI (chỉ giữ các từ không trùng tiếng Việt)
# -----------------------------------------------------------------------------

_ENGLISH_FIXES = {
    'okay': 'được',
    'thank you': 'cảm ơn',
    'sorry': 'xin lỗi',
    'goodbye': 'tạm biệt',
    'love you': 'yêu em',
}

_ENGLISH_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in sorted(_ENGLISH_FIXES.keys(), key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)


# -----------------------------------------------------------------------------
# Rule 5: Number 2 -> "vâng" (fix các dòng bị lỗi "vâng <danh từ đếm>" -> "hai <danh từ đếm>")
# -----------------------------------------------------------------------------

_VANG_COUNTERS = (
    r'mươi|trăm|nghìn|ngàn|triệu|tỷ|chục|thứ|phần|đôi|cặp|lượng|'
    r'lớp|tầng|phòng|nhà|hộp|gói|chai|ly|cốc|bát|đĩa|tờ|'
    r'người|đứa|con|cái|món|cách|bên|ngày|lần|tuần|tháng|năm|giờ|phút|'
    r'chị em|anh em|vợ chồng|bạn|bạn bè|em bé|thằng|con gái|'
    r'con trai|đứa con|người yêu|người lạ|người bạn|thế hệ|thế kỷ|'
    r'thành viên|người chơi|kẻ|người ta|người kia|người đó|cô|chú|bác|'
    r'cậu|ông|bà|anh|chị|em|thầy|cô giáo|gia đình|'
    r'cánh|cửa|bức|tấm|chiếc|quyển|cuốn|quả|trái|con vật|con chó|con mèo|'
    r'con đường|cung đường|đường|bước|bước chân|chân|tay|cánh tay|bàn tay|'
    r'mắt|con mắt|đôi mắt|tai|đôi tai|lưỡi|cái lưỡi|miệng|cái miệng'
)

_VANG_SPECIFIC = [
    (r'\bcả vâng\b', 'cả hai'),
]

_VANG_HEURISTIC_RE = re.compile(
    r'\bvâng\s+(' + _VANG_COUNTERS + r')\b',
    re.IGNORECASE | re.UNICODE,
)

_VANG_RE = re.compile(
    '|'.join(p for p, _ in _VANG_SPECIFIC) + '|' + _VANG_HEURISTIC_RE.pattern,
    re.IGNORECASE | re.UNICODE,
)


def _replace_vang(match: 're.Match') -> str:
    """Convert a 'vâng' match to 'hai', preserving the trailing word."""
    matched = match.group(0)
    if 'vâng' not in matched.lower():
        return matched
    return re.sub(r'\bvâng\b', 'hai', matched, count=1, flags=re.IGNORECASE)


# -----------------------------------------------------------------------------
# Rule 6: Whisper artifacts (おなり -> onani, etc.)
# -----------------------------------------------------------------------------

_WHISPER_ARTIFACT_FIXES = [
    (r'\bおなり(は|の|を|して)?', r'onani\1'),
]

_WHISPER_ARTIFACT_RE = re.compile('|'.join(p for p, _ in _WHISPER_ARTIFACT_FIXES))

# Trích xuất các từ bị cấm dạng: KHÔNG dịch "A" / "B" trong context_hint của glossary
_FORBIDDEN_HINT_RE = re.compile(
    r'KHÔNG\s+(?:BAO\s+GIỜ\s+)?(?:dịch|dùng|phải)?\s*([^.;\n]+)',
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(r'"([^"]+)"')


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


# Rule 10: Force-fix "chủ nhân" -> "chồng" (model bias for 主人 in JAV)
_JAV_MASTER_RE = re.compile(r'chủ nhân', re.IGNORECASE)


def _replace_chu_nhan(m, full_text: str) -> str:
    start = m.start()
    prefix = full_text[:start].rstrip()
    if prefix.lower().endswith('của'):
        return m.group(0)
    orig = m.group(0)
    return 'Chồng' if orig[0].isupper() else 'chồng'


def _preserve_case_replace(match_text: str, replacement: str) -> str:
    if not match_text or not replacement:
        return replacement
    if match_text[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


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
        self.character_names = []
        self._glossary_forbidden_rules = []

    def set_character_names(self, character_names: list):
        """Inject character_names từ config để fix mistaken aliases."""
        self.character_names = character_names or []

    def set_glossary_terms(self, glossary_terms: list):
        """Tự động trích các cặp (source_term, forbidden_vi -> target_term) từ context_hint."""
        rules = []
        for entry in glossary_terms or []:
            if not isinstance(entry, dict):
                continue
            src = (entry.get('source_term') or entry.get('source') or '').strip()
            tgt = (entry.get('target_term') or entry.get('target') or '').strip()
            hint = (entry.get('context_hint') or entry.get('context') or '').strip()
            if not src or not tgt or not hint or 'KHÔNG' not in hint.upper():
                continue
            forbidden = []
            for m in _FORBIDDEN_HINT_RE.finditer(hint):
                for q in _QUOTED_RE.findall(m.group(1)):
                    q_clean = q.strip()
                    if (
                        len(q_clean) >= 4
                        and len(q_clean.split()) >= 2
                        and q_clean.lower() not in tgt.lower()
                    ):
                        forbidden.append(q_clean)
            if forbidden:
                pat = re.compile(
                    r'\b(' + '|'.join(re.escape(w) for w in sorted(set(forbidden), key=len, reverse=True)) + r')\b',
                    re.IGNORECASE,
                )
                rules.append((src, pat, tgt))
        self._glossary_forbidden_rules = rules

    def process_line(self, text: str, source_text: Optional[str] = None) -> str:
        """Post-process một dòng đã dịch (kèm source_text tiếng Nhật nếu có)."""
        if not self.enabled or not text or not text.strip():
            return text

        self._stats['total'] += 1
        original = text

        # 1. Fix whitespace
        text = _fix_whitespace(text)

        # 2. Fix Sino-VN literal calques
        def _replace_sino(m):
            rep = _SINO_FIXES.get(m.group(0).lower(), m.group(0))
            return _preserve_case_replace(m.group(0), rep)
        text = _SINO_RE.sub(_replace_sino, text)

        # 2b. Source-conditioned fixes (chỉ chạy khi biết câu gốc JA)
        if source_text:
            for ja_re, vi_re, rep in _SOURCE_CONDITIONED_FIXES:
                if ja_re.search(source_text):
                    text = vi_re.sub(lambda m, r=rep: _preserve_case_replace(m.group(0), r), text)
            for src_term, vi_forbid_re, tgt_term in self._glossary_forbidden_rules:
                if src_term in source_text:
                    text = vi_forbid_re.sub(
                        lambda m, r=tgt_term: _preserve_case_replace(m.group(0), r),
                        text,
                    )

        # 3. Fix Romaji leak
        def _replace_romaji(m):
            rep = _ROMAJI_TO_VI.get(m.group(0).lower(), m.group(0))
            return _preserve_case_replace(m.group(0), rep)
        text = _ROMAJI_RE.sub(_replace_romaji, text)

        # 4. Fix kana particles bị sót + CJK phrase leaks từ Qwen/Hunyuan
        for wrong, correct in _PARTICLES:
            text = text.replace(wrong, correct)
        for cjk_phrase, vi_rep in _CJK_LEAK_FIXES:
            if cjk_phrase in text:
                text = text.replace(cjk_phrase, vi_rep)
        if _CJK_RE.search(text) and _VI_CHARS_RE.search(text):
            text = _CJK_RE.sub(' ', text)

        # 5. Fix English leak
        def _replace_english(m):
            rep = _ENGLISH_FIXES.get(m.group(0).lower(), m.group(0))
            return _preserve_case_replace(m.group(0), rep)
        text = _ENGLISH_RE.sub(_replace_english, text)

        # 6. Fix "vâng" hallucination ("vâng <counter>" -> "hai <counter>")
        def _vang_in_match(m):
            return re.sub(
                'vâng',
                lambda c: 'Hai' if c.group(0)[0].isupper() else 'hai',
                m.group(0),
                flags=re.IGNORECASE,
            )
        text = _VANG_RE.sub(_vang_in_match, text)

        # 7. JAV romaji artifacts
        text = re.sub(r'\s*(onani|seku\s?kusu|fera|paizuri|teiman)\s*', ' ', text, flags=re.IGNORECASE)

        # 8. Fix mistaken character aliases (Whisper nghe sai tên nhân vật)
        if self.character_names:
            for entry in self.character_names:
                mistaken = entry.get('mistaken_aliases') or []
                canonical = (entry.get('canonical') or '').strip()
                if not mistaken or not canonical:
                    continue
                for alias in mistaken:
                    if not alias:
                        continue
                    text = re.sub(
                        r'\b' + re.escape(alias) + r'\b',
                        canonical,
                        text,
                    )

        # 9. Fix punctuation
        text = _fix_punctuation(text)

        # 10. Force-fix model bias: "chủ nhân" -> "chồng"
        text = _JAV_MASTER_RE.sub(lambda m: _replace_chu_nhan(m, text), text)

        # 11. Final whitespace
        text = _fix_whitespace(text)

        if text != original:
            self._stats['changed'] += 1

        return text

    def process_lines(self, lines: List[str], source_lines: Optional[List[str]] = None) -> List[str]:
        """Post-process nhiều dòng."""
        if source_lines and len(source_lines) == len(lines):
            return [self.process_line(line, src) for line, src in zip(lines, source_lines)]
        return [self.process_line(line) for line in lines]

    def process_project(self, db, project_id: int, progress_callback=None) -> dict:
        """Post-process toàn bộ project (có đối chiếu original_text tiếng Nhật)."""
        items = db.get_all_items(project_id)
        total = len(items)
        changed = 0

        for i, item in enumerate(items):
            trans = item.get('translated_text', '')
            if not trans or not trans.strip():
                continue

            orig = item.get('original_text', '') or ''
            fixed = self.process_line(trans, source_text=orig)
            if fixed != trans:
                db.update_item_translation(item['id'], fixed)
                if hasattr(db, 'log_translation_error'):
                    try:
                        db.log_translation_error(
                            project_id=project_id,
                            sub_index=item.get('sub_index', 0),
                            source_text=orig,
                            wrong_text=trans,
                            fixed_text=fixed,
                            error_type='post_processor',
                        )
                    except Exception:
                        pass
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
