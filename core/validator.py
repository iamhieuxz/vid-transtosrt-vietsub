import re
import logging
from typing import List, Dict, Set

logger = logging.getLogger(__name__)

class Validator:
    def validate_json_translation(self, json_data: List[Dict], expected_ids: Set[int]) -> bool:
        if not isinstance(json_data, list):
            logger.error("JSON is not a list")
            return False
        if len(json_data) != len(expected_ids):
            logger.error(f"JSON length {len(json_data)} != expected {len(expected_ids)}")
            return False

        seen_ids = set()
        for item in json_data:
            if not isinstance(item, dict) or 'id' not in item or 'text' not in item:
                logger.error("Item missing 'id' or 'text'")
                return False
            if not isinstance(item['id'], int) or not isinstance(item['text'], str):
                logger.error(f"Invalid type in item: {item}")
                return False
            if item['id'] not in expected_ids:
                logger.error(f"id {item['id']} not in expected_ids {expected_ids}")
                return False
            if item['id'] in seen_ids:
                logger.error(f"Duplicate id: {item['id']}")
                return False
            seen_ids.add(item['id'])

        if seen_ids != expected_ids:
            missing = expected_ids - seen_ids
            logger.error(f"Missing ids: {missing}")
            return False
        return True

    def validate_window_content(self, originals: List[str], translations: List[str]) -> bool:
        if len(originals) != len(translations):
            logger.error(f"Line count mismatch: {len(originals)} vs {len(translations)}")
            return False

        for orig, trans in zip(originals, translations):
            # Kiểm tra placeholder
            placeholders = re.findall(r'%[sd]|\{[^{}]+\}', orig)
            for ph in placeholders:
                if ph not in trans:
                    logger.error(f"Missing placeholder '{ph}' in: {trans}")
                    return False
            # Dòng gốc không rỗng mà dịch rỗng
            if orig.strip() and not trans.strip():
                logger.error(f"Empty translation for: {orig}")
                return False
            # Cảnh báo nếu bản dịch dài bất thường (hallucination)
            if len(orig) > 0 and len(trans) / len(orig) > 8:
                logger.warning(f"Translation is too long: {orig} -> {trans[:50]}...")
        return True