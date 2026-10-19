"""
Pipeline rule processor.
Ap nhat cac anti-artifact rules va character name mapping tu config.yaml.
"""
import re
import yaml
import logging
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)


class RuleProcessor:
    """Ap dung cac quy tac chinh sua sau khi dich."""

    def __init__(self, config_path: str):
        with open(config_path, 'r', encoding='utf-8-sig') as f:
            self.config = yaml.safe_load(f) or {}
        self.character_names = self.config.get('character_names', [])
        self.anti_artifacts = self.config.get('anti_artifacts', [])
        self._build_canonical_map()
        self._build_artifact_patterns()

    def _build_canonical_map(self):
        """Tao map: alias -> canonical name.
        Neu cung ten co nhieu cach viet, ta thay the het ve 1 dang chuan.
        """
        self.canonical_map = {}
        for entry in self.character_names:
            canonical = entry['canonical']
            for alias in entry.get('aliases', []):
                self.canonical_map[alias] = canonical

    def _build_artifact_patterns(self):
        """Compile cac regex pattern cho anti-artifact."""
        self.artifact_patterns = []
        for art in self.anti_artifacts:
            try:
                pat = re.compile(art['pattern'], re.IGNORECASE)
                self.artifact_patterns.append({
                    'pattern': pat,
                    'replacement': art.get('replacement', ''),
                    'severity': art.get('severity', 'warning'),
                    'reason': art.get('reason', ''),
                })
            except re.error as e:
                logger.warning(f"Invalid anti-artifact pattern {art['pattern']}: {e}")

    def apply_character_names(self, text: str) -> str:
        """Thay the alias ten nhan vat bang canonical name."""
        if not text:
            return text
        result = text
        for alias, canonical in self.canonical_map.items():
            if alias in result:
                result = result.replace(alias, canonical)
        return result

    def apply_anti_artifacts(self, text: str) -> Tuple[str, list]:
        """Ap dung anti-artifact rules. Tra ve (text_moi, danh_sach_vi_pham)."""
        if not text:
            return text, []
        violations = []
        result = text
        for rule in self.artifact_patterns:
            new_result = rule['pattern'].sub(rule['replacement'], result)
            if new_result != result:
                violations.append({
                    'pattern': rule['pattern'].pattern,
                    'replacement': rule['replacement'],
                    'severity': rule['severity'],
                    'reason': rule['reason'],
                })
                result = new_result
        return result, violations

    def process_translation(self, text: str) -> Tuple[str, list]:
        """Pipeline day du: character names + anti-artifacts.
        Tra ve (text_da_sua, danh_sach_vi_pham).
        """
        if not text:
            return text, []
        all_violations = []

        # Buoc 1: Thay the ten nhan vat
        text = self.apply_character_names(text)

        # Buoc 2: Ap dung anti-artifact rules
        text, violations = self.apply_anti_artifacts(text)
        all_violations.extend(violations)

        return text, all_violations

    def process_window(self, translations: List[str]) -> Tuple[List[str], list]:
        """Xu ly ca window dich. Tra ve (list_da_sua, danh_sach_vi_pham)."""
        new_translations = []
        all_violations = []
        for trans in translations:
            new_trans, violations = self.process_translation(trans)
            new_translations.append(new_trans)
            all_violations.extend(violations)
        return new_translations, all_violations


def get_rule_processor(config_path: str = 'config.yaml') -> RuleProcessor:
    """Singleton getter."""
    global _processor_instance
    if '_processor_instance' not in globals():
        _processor_instance = RuleProcessor(config_path)
    return _processor_instance
