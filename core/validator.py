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

        for orig, trans in zip(originals, translations):
            placeholders = re.findall(r'%[sd]|%[0-9]*[diouxXeEfFgGcrsab%]|\{[^}]+\}', orig)
            for ph in placeholders:
                if ph not in trans:
                    logger.error(f"Missing placeholder '{ph}' in: {trans}")
                    return False
            # Bắt cả 2 trường hợp: orig có nội dung nhưng trans trống, và cả 2 đều trống
            if not orig.strip() and not trans.strip():
                logger.error(f"Both empty for subtitle index")
                return False
            if orig.strip() and not trans.strip():
                logger.error(f"Empty translation for: {orig}")
                return False
            if len(orig) > 0 and len(trans) / len(orig) > 8:
                logger.warning(f"Translation is too long: {orig} -> {trans[:50]}...")

        if self._detect_repetition(translations):
            logger.error("Translation contains repetitive patterns")
            return False

        return True

    def _detect_repetition(self, translations: List[str], threshold: float = 0.7) -> bool:
        """Phát hiện text lặp lại trong bản dịch."""
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