"""Character registry — validate tên riêng nhất quán toàn phim.

Worker A (JaContextAnalyzer) extract speakers cho MỖI context-window riêng biệt
(18 dòng) → có thể miss 1-2 nhân vật xuất hiện ít. Registry gom tất cả speakers
từ toàn bộ window_contexts trong 1 project, đếm tần suất, xác định tên chuẩn.

Sau khi main pipeline xong:
- Registry build 1 lần (cache theo project_id + content_hash tổng hợp)
- Inject vào validator: cùng JA source tên X nhưng VI dịch thành 2 dạng khác nhau
  → flag warning hoặc auto-fix theo dạng phổ biến nhất.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CharacterRegistry:
    """Build + lookup character name mapping toàn phim."""

    def __init__(self, db, project_id: int):
        self.db = db
        self.project_id = project_id
        self._cache: Optional[Dict[str, str]] = None
        self._cache_hash: Optional[str] = None

    def build(self) -> Dict[str, str]:
        """Gom speakers từ tất cả window_contexts (status=completed).

        Trả về dict {ja_name: canonical_vi_name}.
        Quy tắc chọn canonical:
          1. Nếu có alias trong config.character_names → dùng canonical từ đó
          2. Nếu không → dùng romaji đầu tiên gặp (ưu tiên có dấu hoa đầu)
          3. Cùng tên Kanji xuất hiện nhiều dạng → ưu tiên Hiragana (ít lỗi Whisper)
        """
        if self._cache is not None:
            return self._cache

        # Load character_names từ config
        canonical_map = self._load_canonical_map()

        # Gom speakers + cách viết + tần suất
        ja_counter: Counter = Counter()
        ja_to_aliases: Dict[str, Counter] = {}

        contexts = self.db.list_context_windows(self.project_id)
        for ctx in contexts:
            if ctx.get("status") != "completed":
                continue
            speakers_json = ctx.get("speakers_json") or "[]"
            try:
                speakers = json.loads(speakers_json)
            except Exception:
                continue
            for sp in speakers:
                if not isinstance(sp, str) or not sp.strip():
                    continue
                sp = sp.strip()
                ja_counter[sp] += 1
                # Track các dạng viết khác nhau cho cùng tên
                # (vd: あゆみ / Ayumi / アユミ) — match bằng canonical
                canonical_key = self._find_canonical_key(sp, canonical_map)
                if canonical_key:
                    ja_to_aliases.setdefault(canonical_key, Counter())[sp] += 1

        # Build mapping
        result: Dict[str, str] = {}
        for sp, count in ja_counter.items():
            canonical_key = self._find_canonical_key(sp, canonical_map)
            if canonical_key:
                # Có trong config → dùng canonical
                result[sp] = canonical_map[canonical_key]["canonical"]
            else:
                # Không có trong config → dùng chính nó (giữ romaji)
                # Skip nếu chỉ là Kanji/Hiragana thuần (sẽ gặp issue khi dịch)
                if re.match(r'^[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]+$', sp):
                    # Pure JA — không thể làm canonical VI được, giữ nguyên
                    # để LLM tự quyết định (warning log)
                    logger.debug(
                        f"CharacterRegistry: pure-JA name '{sp}' has no canonical, "
                        f"keeping as-is"
                    )
                result[sp] = sp

        # Cache với hash
        self._cache = result
        self._cache_hash = self._compute_hash(result)
        logger.info(
            f"CharacterRegistry: {len(result)} unique characters in project {self.project_id}"
        )
        return result

    def get_canonical(self, ja_name: str) -> str:
        """Lấy canonical VI name cho 1 JA name. Trả về nguyên nếu không có."""
        if self._cache is None:
            self.build()
        return self._cache.get(ja_name, ja_name) if self._cache else ja_name

    def get_all_canonical(self) -> List[str]:
        """Trả về list tất cả canonical names trong phim (unique, sorted)."""
        if self._cache is None:
            self.build()
        if not self._cache:
            return []
        return sorted(set(self._cache.values()))

    def validate_translation(self, source_line: str, target_line: str) -> Optional[str]:
        """So sánh source JA có chứa character name X với target VI có dùng
        canonical của X không. Trả về suggestion nếu mismatch, None nếu OK.

        Logic đơn giản: nếu source JA chứa tên X (mapped với canonical Y),
        target VI PHẢI chứa Y. Nếu target không có Y → check nếu có alias sai
        (từ config.character_names) → replace.
        """
        if self._cache is None:
            self.build()
        if not self._cache:
            return None

        target_lower = target_line.lower()
        for ja_name, canonical_vi in self._cache.items():
            if ja_name not in source_line:
                continue
            # Có mention tên JA trong source
            canonical_lower = canonical_vi.lower()
            if canonical_lower in target_lower:
                continue  # OK — đã có canonical
            # Tìm các dạng sai
            wrong_forms = self._find_wrong_forms(ja_name, canonical_vi, target_line)
            if wrong_forms:
                fixed = target_line
                for wrong in wrong_forms:
                    # Word boundary để tránh replace 1 phần từ khác
                    pattern = re.compile(r'\b' + re.escape(wrong) + r'\b', re.IGNORECASE)
                    fixed = pattern.sub(canonical_vi, fixed)
                # Verify có thay đổi
                if fixed != target_line:
                    return fixed
        return None

    def _find_canonical_key(self, name: str, canonical_map: Dict[str, dict]) -> Optional[str]:
        """Tìm canonical_key trong config map cho tên name (alias match).

        Strip honorific suffix (さん, ちゃん, くん, 様) trước khi match.
        """
        # Strip common honorifics
        clean = name
        for suffix in ('さん', '様', 'ちゃん', 'くん', '君', '殿', 'san', 'kun', 'chan', 'sama'):
            if clean.endswith(suffix):
                clean = clean[:-len(suffix)]
                break
        for key, entry in canonical_map.items():
            if name in entry["aliases"]:
                return key
            # Cũng check sau khi strip
            if clean in entry["aliases"]:
                return key
        return None

    def _load_canonical_map(self) -> Dict[str, dict]:
        """Load character_names từ config.yaml. Cache per-call."""
        try:
            import yaml
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"CharacterRegistry: failed to load config.yaml: {e}")
            return {}
        result = {}
        for entry in cfg.get("character_names", []) or []:
            canonical = entry.get("canonical", "").strip()
            if not canonical:
                continue
            aliases = entry.get("aliases", []) or []
            # Key = canonical thường (lowercase) để match alias
            result[canonical.lower()] = {
                "canonical": canonical,
                "aliases": aliases,
            }
        return result

    def _find_wrong_forms(self, ja_name: str, canonical: str, target: str) -> List[str]:
        """Tìm các dạng 'sai' của tên trong target.

        2 chiến thuật:
        1. Aliases trong config (loại canonical). VD: あゆみ aliases = [あゆみ, アユミ, Ayumi]
        2. Common Vietnamese shortenings: nếu canonical dài >= 4 ký tự, check
           word trong target có phải rút gọn không (chứa 2 ký tự đầu + len khác).
        """
        canonical_map = self._load_canonical_map()
        key = canonical.lower()
        target_lower = target.lower()
        wrong = []
        canonical_lower = canonical.lower()

        # (1) Alias check từ config
        if key in canonical_map:
            for alias in canonical_map[key]["aliases"]:
                if alias.lower() == canonical_lower:
                    continue
                if alias.lower() in target_lower:
                    # Có alias khác canonical trong target → có thể sai
                    # Nhưng chỉ flag nếu KHÔNG có canonical cùng
                    # (đã check ở validate_translation, nhưng đảm bảo an toàn)
                    if canonical_lower not in target_lower:
                        wrong.append(alias)

        # (2) Substring rút gọn: kiểm tra word nào trong target bắt đầu
        # bằng 2 ký tự đầu của canonical nhưng KHÔNG phải canonical
        if canonical_lower not in target_lower and len(canonical) >= 4:
            words = re.findall(r'\b[A-Za-z]{3,}\b', target)
            for w in words:
                if w.lower() == canonical_lower:
                    continue
                w_lower = w.lower()
                # Check 2 ký tự đầu trùng (heuristic: Aya/Ayumi, Kawa/Kawaguchi)
                if len(w_lower) >= 2 and w_lower[:2] == canonical_lower[:2]:
                    # Loại trừ common Vietnamese words (anh, em, có, không, ...)
                    if w_lower in {'anh', 'em', 'chi', 'toi', 'ban', 'cau', 'thi',
                                   'hay', 'rat', 'lam', 'the', 'khi', 'mot', 'hai',
                                   'ba', 'bon', 'nam', 'sau', 'bay', 'tam', 'chin',
                                   'mui', 'hon', 'vao', 'ra', 'len', 'xuong', 'nha',
                                   'san', 'tan', 'can', 'dam', 'yen', 'gio', 'ngay',
                                   'dem', 'sang', 'trua', 'toi', 'den', 'di', 'den',
                                   'kia', 'nay', 'do'}:
                        continue
                    if w not in wrong:
                        wrong.append(w)
        return wrong

    def _compute_hash(self, mapping: Dict[str, str]) -> str:
        """Hash của registry content, dùng để invalidate cache khi context update."""
        s = json.dumps(mapping, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]

    @property
    def cache_hash(self) -> Optional[str]:
        """Hash hiện tại của registry. None nếu chưa build."""
        return self._cache_hash

    def is_stale(self, db_context_changed: bool) -> bool:
        """Check nếu registry cần rebuild (context thay đổi)."""
        if self._cache is None:
            return True
        return db_context_changed
