import re
import logging
from typing import List, Dict, Set

logger = logging.getLogger(__name__)

class Validator:
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
            if len(orig) > 0 and len(trans) / len(orig) > 8:
                logger.warning(f"Translation too long, truncating: {trans[:80]}...")
                translations[i] = trans[:150]  # hard cap 150 chars

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

    def _check_character_name_consistency(self, translations: List[str]) -> bool:
        """Kiểm tra tên nhân vật có nhất quán không.

        Nếu có nhiều cách viết khác nhau của cùng tên (vd: Ayunee + Ayune) → reject.

        P1-3 fix: build name_variants from self.character_names (config-driven) instead
        of hardcoding Ayunee/Ayune/Ayumi. Falls back to hardcoded defaults if config empty.
        """
        # shortcut: config-driven name variants
        name_variants = self._build_name_variants()
        if not name_variants:
            return True  # no character names configured — nothing to check

        found = {}
        # group: map each alias to its canonical, so we can detect when same character
        # appears under different spellings (e.g., 'ayunee' and 'ayune' both -> 'Ayunee')
        alias_to_canonical = {}
        for canonical, variants in name_variants.items():
            for v in variants:
                alias_to_canonical[v] = canonical

        for trans in translations:
            if not trans:
                continue
            t_lower = trans.lower()
            for alias, canonical in alias_to_canonical.items():
                if alias in t_lower:
                    found.setdefault(canonical, 0)
                    found[canonical] += 1

        # Nếu cùng 1 canonical có nhiều cách viết khác nhau (alias khác nhau) → reject
        # (ví dụ: cùng là 'Ayunee' nhưng 1 dòng 'Ayunee', 1 dòng 'Ayuune' cùng xuất hiện)
        canonical_to_aliases_hit = {}
        for trans in translations:
            if not trans:
                continue
            t_lower = trans.lower()
            for canonical, variants in name_variants.items():
                for v in variants:
                    if v in t_lower:
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
            variants = set()
            # primary aliases
            for a in entry.get('aliases', []) or []:
                if a and a.strip():
                    variants.add(a.strip().lower())
            # mistaken aliases (Whisper mishearings) — also must be unified
            for a in entry.get('mistaken_aliases', []) or []:
                if a and a.strip():
                    variants.add(a.strip().lower())
            # canonical itself is also a valid alias (so we detect it as a hit)
            variants.add(canonical.lower())
            if variants:
                result[canonical] = sorted(variants)
        return result

    def _detect_repetition(self, translations: List[str], threshold: float = 0.5) -> bool:
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