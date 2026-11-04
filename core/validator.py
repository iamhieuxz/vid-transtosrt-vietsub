import re
import logging
from typing import List, Dict, Set

logger = logging.getLogger(__name__)

# Audit R-2: đặt tên cho các ngưỡng thay vì magic number rải trong code.
# Phụ đề hiển thị tối đa 2 dòng x ~42 ký tự; 150 là giới hạn cứng để chặn
# trường hợp LLM trả về cả đoạn văn.
MAX_SUBTITLE_CHARS = 150
MAX_TRANSLATION_RATIO = 8.0

# Ranh giới câu/vế để cắt an toàn (không chặt giữa từ)
_CLAUSE_BOUNDARY_RE = re.compile(r'[.!?…;:,。！？、]+')


def _truncate_at_boundary(text: str, limit: int) -> str:
    """Cắt `text` về <= `limit` ký tự tại ranh giới câu/vế gần nhất.

    Ưu tiên: dấu câu -> khoảng trắng -> cắt cứng.
    Tránh trường hợp `text[:150]` chặt đôi một từ và làm câu cụt nghĩa.
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    # 1. Ranh giới câu/vế muộn nhất, nhưng phải giữ được ít nhất nửa giới hạn
    #    (cắt ở dấu chấm quá sớm thì mất gần hết nội dung).
    min_keep = limit * 0.5
    last = None
    for m in _CLAUSE_BOUNDARY_RE.finditer(head):
        if m.end() >= min_keep:
            last = m.end()
    if last:
        return head[:last].rstrip()
    # 2. Khoảng trắng muộn nhất
    sp = head.rfind(" ")
    if sp >= min_keep:
        return head[:sp].rstrip()
    # 3. Cắt cứng (không có ranh giới nào đủ muộn)
    return head.rstrip()


class Validator:
    _VI_COMMON_WORDS_LOWER = frozenset({
        'anh', 'em', 'con', 'co', 'cô', 'me', 'mẹ', 'ba', 'bà', 'bo', 'bố',
        'an', 'ăn', 'ai', 'mi', 'mì', 'my', 'ta', 'to', 'tớ', 'ca', 'cả',
        'la', 'là', 'ma', 'mà', 'na', 'sa', 'ha', 'da', 'ra', 'va', 'và',
        'oi', 'ơi', 'uh', 'ư', 'ừ', 'a', 'á', 'ạ', 'o', 'ơ', 'ờ', 'u', 'ừm',
        'nha', 'nhé', 'nhe', 'nhi', 'nhỉ', 'di', 'đi', 'do', 'đó', 'du', 'đủ',
    })

    def validate_json_translation(self, json_data: List[Dict], expected_ids: List[int]) -> bool:
        """Validate JSON structure and ID ORDER."""
        if not isinstance(json_data, list):
            logger.error("JSON is not a list")
            return False
        if len(json_data) != len(expected_ids):
            logger.error(f"JSON length {len(json_data)} != expected {len(expected_ids)}")
            return False

        # Check both IDs and ORDER
        for idx, (item, expected_id) in enumerate(zip(json_data, expected_ids)):
            if not isinstance(item, dict) or 'id' not in item or 'text' not in item:
                logger.error("Item missing 'id' or 'text'")
                return False
            if not isinstance(item['id'], int) or not isinstance(item['text'], str):
                logger.error(f"Invalid type in item: {item}")
                return False
            if item['id'] != expected_id:
                logger.error(f"ID order mismatch at position {idx}: got {item['id']}, expected {expected_id}")
                return False

        return True

    def validate_window_content(self, originals: List[str], translations: List[str]) -> bool:
        if len(originals) != len(translations):
            logger.error(f"Line count mismatch: {len(originals)} vs {len(translations)}")
            return False

        for i, (orig, trans) in enumerate(zip(originals, translations)):
            placeholders = re.findall(r'%[sd]|%[0-9]*[diouxXeEfFgGcrsab%]|\{[^}]+\}', orig)
            for ph in placeholders:
                if ph not in trans:
                    logger.error(f"Missing placeholder '{ph}' in: {trans}")
                    return False
            if not orig.strip() and not trans.strip():
                logger.error(f"Both empty for subtitle index")
                return False
            if orig.strip() and not trans.strip():
                logger.error(f"Empty translation for: {orig}")
                return False

            # Truncate dòng quá dài (subtitles có giới hạn ~42 chars mỗi dòng)
            if len(orig) > 0 and len(trans) / len(orig) > MAX_TRANSLATION_RATIO:
                # P3: cắt tại ranh giới câu/từ, KHÔNG cắt giữa từ.
                # `trans[:150]` thô có thể chặt đôi một từ và làm câu cụt nghĩa.
                cut = _truncate_at_boundary(trans, MAX_SUBTITLE_CHARS)
                logger.warning(
                    f"Translation too long ({len(trans)} chars), truncated to "
                    f"{len(cut)} at boundary: {cut[-40:]!r}"
                )
                translations[i] = cut

            # Cảnh báo chính tả tiếng Việt (không reject, chỉ warn)
            if trans:
                self._check_vietnamese_typos(orig, trans)
                # Ký tự Nhật/Cyrillic lọt vào bản dịch = LLM không dịch được
                # (VD: "Nó cứ lừ đừ不出来 mà", "Vậy thì chỉ一杯 thôi").
                # Reject để retry bằng prompt khác. Chuẩn phụ đề Việt chỉ dùng
                # Latin, giữ ký tự Nhật kèm nghĩa ("Miho (美穂)") cũng là lỗi.
                leaked = self._detect_script_leak(trans)
                if leaked:
                    logger.error(
                        "Translation contains untranslated %s characters: %r",
                        leaked, trans[:60],
                    )
                    return False

        # Check tính nhất quán của tên nhân vật
        if not self._check_character_name_consistency(translations):
            return False

        if self._detect_repetition(translations):
            logger.error("Translation contains repetitive patterns")
            return False

        return True

    @staticmethod
    def _detect_script_leak(trans: str) -> str:
        """Phát hiện ký tự nước ngoài còn sót trong bản dịch Việt.

        Chuẩn phụ đề Việt chỉ dùng Latin + dấu, nên bất kỳ Kana/CJK/Hangul/
        Cyrillic nào còn lại đều là LLM bỏ sót (VD "Nó cứ lừ đừ不出来 mà").
        Trả về tên script bị lọt, hoặc chuỗi rỗng nếu sạch.
        """
        for name, pattern in (
            ('Kana', r'[\u3040-\u30ff]'),
            ('CJK', r'[\u4e00-\u9fff]'),
            ('Hangul', r'[\uac00-\ud7af]'),
            ('Cyrillic', r'[\u0400-\u04ff]'),
        ):
            if re.search(pattern, trans):
                return name
        return ''

    def _check_vietnamese_typos(self, orig: str, trans: str):
        """Cảnh báo các lỗi chính tả tiếng Việt phổ biến khi dịch Nhật-Việt."""
        # Lỗi "vâng" thay vì "hai" (model hay viết sai khi gặp số 2).
        # Dùng chung regex với post_processor: chỉ match "vâng + <counter>".
        # Substring check warn nhầm mỗi lần gặp "Vâng" (lời lạm) — false positive.
        from .post_processor import _VANG_RE
        if _VANG_RE.search(trans):
            logger.warning(f"VN typo: 'vâng' (should be 'hai') in: {trans[:60]}")
        # Lỗi giữ romaji chưa dịch (Whisper artifacts)
        romaji_artifacts = ['Garam tsuite', 'an ketsu', 'Vagina', 'Anus',
                            'Pussy', 'Dick', 'hentai', 'ecchi']
        for art in romaji_artifacts:
            if art.lower() in trans.lower():
                logger.warning(f"VN untranslated artifact '{art}' in: {trans[:60]}")
        try:
            from .ja_qa_checker import JaQAChecker
            if JaQAChecker.check_negation_mismatch(orig, trans):
                logger.warning(f"JA-VI negation mismatch: orig={orig[:40]!r} vs trans={trans[:40]!r}")
        except Exception:
            pass

    def _normalize_mistaken_character_names(self, translations: List[str]) -> None:
        """Tự động sửa các mistaken_aliases (nghe/phiên âm lệch) về tên canonical
        theo ranh giới từ (\\b) ngay trên bản dịch trước khi kiểm tra tính nhất quán.
        """
        if not getattr(self, 'character_names', None):
            return
        for entry in self.character_names:
            if not isinstance(entry, dict):
                continue
            canonical = (entry.get('canonical') or '').strip()
            if not canonical or len(canonical) < 3:
                continue
            mistaken = entry.get('mistaken_aliases') or []
            for alias in mistaken:
                if not alias or not isinstance(alias, str):
                    continue
                alias_clean = alias.strip()
                if len(alias_clean) < 4:
                    continue
                if alias_clean.lower() == canonical.lower():
                    continue
                if alias_clean.lower() in self._VI_COMMON_WORDS_LOWER:
                    continue
                if not re.match(r'^[A-Za-z\-]+$', alias_clean):
                    continue
                pattern = re.compile(rf'\b{re.escape(alias_clean)}\b', re.IGNORECASE)
                for idx, trans in enumerate(translations):
                    if trans and pattern.search(trans):
                        translations[idx] = pattern.sub(canonical, trans)

    def _check_character_name_consistency(self, translations: List[str]) -> bool:
        """Kiểm tra tên nhân vật có nhất quán không (dùng ranh giới từ \\b).

        Nếu có nhiều cách viết khác nhau của cùng tên (vd: Ayunee + Ayune) → reject.

        P1-3 fix: build name_variants from self.character_names (config-driven) instead
        of hardcoding Ayunee/Ayune/Ayumi. Falls back to hardcoded defaults if config empty.
        """
        self._normalize_mistaken_character_names(translations)

        # shortcut: config-driven name variants
        name_variants = self._build_name_variants()
        if not name_variants:
            return True  # no character names configured — nothing to check

        variant_patterns = {}
        for canonical, variants in name_variants.items():
            compiled_list = []
            for v in variants:
                if len(v) < 3 or v in self._VI_COMMON_WORDS_LOWER:
                    continue
                if not re.match(r'^[a-z\-]+$', v):
                    continue
                compiled_list.append((v, re.compile(rf'\b{re.escape(v)}\b', re.IGNORECASE)))
            if compiled_list:
                variant_patterns[canonical] = compiled_list

        # Nếu cùng 1 canonical có nhiều cách viết khác nhau (alias khác nhau) → reject
        # (ví dụ: cùng là 'Ayunee' nhưng 1 dòng 'Ayunee', 1 dòng 'Ayuune' cùng xuất hiện)
        canonical_to_aliases_hit = {}
        for trans in translations:
            if not trans:
                continue
            for canonical, compiled_list in variant_patterns.items():
                for v, pat in compiled_list:
                    if pat.search(trans):
                        canonical_to_aliases_hit.setdefault(canonical, set()).add(v)
        for canonical, aliases in canonical_to_aliases_hit.items():
            if len(aliases) > 1:
                logger.error(
                    f"Character name inconsistency: '{canonical}' found under "
                    f"{len(aliases)} spellings: {sorted(aliases)}"
                )
                return False
        return True

    def _build_name_variants(self) -> dict:
        """Build name_variants from self.character_names config.

        Format expected (matches config.yaml 'character_names'):
          [
            {"canonical": "Ayune", "aliases": ["Ayunee", "あゆね"], "mistaken_aliases": ["Aya"]},
            ...
          ]

        Returns {canonical_lower: [variant1_lower, variant2_lower, ...]}.
        """
        if not getattr(self, 'character_names', None):
            return {}
        result = {}
        for entry in self.character_names:
            if not isinstance(entry, dict):
                continue
            canonical = entry.get('canonical', '').strip()
            if not canonical:
                continue
            canon_low = canonical.lower()
            variants = set()
            # primary aliases (Latin only, >= 3 chars)
            for a in entry.get('aliases', []) or []:
                if a and a.strip():
                    al = a.strip().lower()
                    if len(al) >= 3 and re.match(r'^[a-z\-]+$', al):
                        variants.add(al)
            # mistaken aliases (Whisper mishearings) — Latin only, >= 4 chars
            for a in entry.get('mistaken_aliases', []) or []:
                if a and a.strip():
                    al = a.strip().lower()
                    if len(al) >= 4 and re.match(r'^[a-z\-]+$', al):
                        variants.add(al)
            # canonical itself is also a valid alias (so we detect it as a hit)
            if len(canon_low) >= 3 and re.match(r'^[a-z\-]+$', canon_low):
                variants.add(canon_low)
            if variants:
                result[canonical] = sorted(variants)
        return result

    def _detect_repetition(self, translations: List[str], threshold: float = 0.7) -> bool:
        """Phát hiện text lặp lại trong bản dịch.

        Threshold 0.5 (down từ 0.7) để bắt cả trường hợp lặp nhẹ như
        "Anh ơi, anh ơi" vs "Em ơi, em ơi" (~67% Jaccard) — trước đây
        bị miss vì threshold cao quá.
        """
        if len(translations) < 2:
            return False

        repeat_count = 0
        for i, trans in enumerate(translations):
            if not trans.strip():
                continue
            trans_lower = trans.lower()
            # Bỏ qua function words/phrases phổ biến trong Vietnamese subs
            # e.g. "bạn", "tôi", "có", "không", "là", "gì", "vậy", "ơi"
            common_words = {'bạn', 'tôi', 'có', 'không', 'là', 'gì', 'vậy', 'ơi',
                            'của', 'trong', 'với', 'cho', 'và', 'như', 'thì', 'đang',
                            'mà', 'để', 'ra', 'vào', 'nào', 'sao', 'hả', 'à', 'ạ'}
            words = trans.split()
            meaningful_words = {re.sub(r'[^\w]', '', w.lower()) for w in words
                               if len(w) > 1 and re.sub(r'[^\w]', '', w.lower()) not in common_words}

            for j, other in enumerate(translations):
                if i != j and other.strip():
                    other_lower = other.lower()
                    other_words = other.split()
                    other_meaningful = {re.sub(r'[^\w]', '', w.lower()) for w in other_words
                                        if len(w) > 1 and re.sub(r'[^\w]', '', w.lower()) not in common_words}
                    # Chỉ flag nếu ≥3 từ có nghĩa GIỐNG NHAU (trùng phạm vi > 70%)
                    if len(meaningful_words) >= 3 and len(other_meaningful) >= 3:
                        intersection = meaningful_words & other_meaningful
                        sim = len(intersection) / max(len(meaningful_words), len(other_meaningful))
                        if sim > threshold:
                            repeat_count += 1
                            break

        return repeat_count >= 3  # ≥3 cặp dòng trùng nhau → mới flag

    def _similarity(self, s1: str, s2: str) -> float:
        """Tính độ giống nhau giữa 2 chuỗi."""
        if not s1 or not s2:
            return 0.0
        s1_set = set(s1.split())
        s2_set = set(s2.split())
        if not s1_set or not s2_set:
            return 0.0
        intersection = len(s1_set & s2_set)
        union = len(s1_set | s2_set)
        return intersection / union if union > 0 else 0.0
