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

    def __init__(self, db, project_id: int, character_names: Optional[List[dict]] = None):
        self.db = db
        self.project_id = project_id
        self.injected_character_names = character_names
        self._cache: Optional[Dict[str, str]] = None
        self._cache_hash: Optional[str] = None

    def build(self) -> Dict[str, str]:
        """Gom speakers từ tất cả window_contexts, config.character_names VÀ
        tự động trích xuất từ subtitle_items bằng fugashi + pykakasi (không cần Worker A).

        Trả về dict {ja_name: canonical_vi_name}.
        """
        if self._cache is not None:
            return self._cache

        from .ja_qa_checker import extract_character_names_from_lines, ja_to_hepburn

        # 1. Tự động trích xuất thêm từ subtitle_items nếu DB có get_all_items
        raw_lines: List[str] = []
        if hasattr(self.db, "get_all_items"):
            try:
                items = self.db.get_all_items(self.project_id) or []
                if isinstance(items, list):
                    raw_lines = [
                        (it.get("original_text") or "")
                        for it in items
                        if isinstance(it, dict) and it.get("original_text")
                    ]
            except Exception:
                raw_lines = []

        # Load character_names từ config + injected + auto-extracted
        canonical_map = self._load_canonical_map(raw_lines=raw_lines)

        # Gom speakers + cách viết + tần suất
        ja_counter: Counter = Counter()
        ja_to_aliases: Dict[str, Counter] = {}

        if hasattr(self.db, "list_context_windows"):
            try:
                contexts = self.db.list_context_windows(self.project_id) or []
            except Exception:
                contexts = []
        else:
            contexts = []

        for ctx in contexts:
            if not isinstance(ctx, dict) or ctx.get("status") != "completed":
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
                canonical_key = self._find_canonical_key(sp, canonical_map)
                if canonical_key:
                    ja_to_aliases.setdefault(canonical_key, Counter())[sp] += 1

        # Bổ sung trực tiếp mọi alias tiếng Nhật từ canonical_map (hoạt động ngay cả khi Worker A tắt)
        for c_key, entry in canonical_map.items():
            for alias in entry.get("aliases", []) or []:
                if alias and re.search(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]', alias):
                    ja_counter[alias] += 1

        # Build mapping
        result: Dict[str, str] = {}
        for sp, count in ja_counter.items():
            canonical_key = self._find_canonical_key(sp, canonical_map)
            if canonical_key:
                result[sp] = canonical_map[canonical_key]["canonical"]
            else:
                if re.match(r'^[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]+$', sp):
                    hep = ja_to_hepburn(sp)
                    result[sp] = hep if hep else sp
                else:
                    result[sp] = sp

        # Cache với hash
        self._cache = result
        self._cache_hash = self._compute_hash(result)
        logger.info(
            f"CharacterRegistry: {len(result)} unique character alias(es) in project {self.project_id}"
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
        # Ưu tiên khớp tên tiếng Nhật dài trước (VD: '吉田さん' -> '吉田' trước '田')
        # và ghi nhận các khoảng vị trí (span) đã khớp để tên ngắn con không khớp chồng lên tên dài!
        sorted_items = sorted(self._cache.items(), key=lambda kv: -len(kv[0]))
        covered_spans: List[Tuple[int, int]] = []

        for ja_name, canonical_vi in sorted_items:
            if not ja_name or ja_name not in source_line:
                continue
            # Tìm tất cả vị trí xuất hiện của ja_name trong source_line chưa bị tên dài hơn bao phủ
            uncovered_positions = []
            start_pos = 0
            while True:
                idx = source_line.find(ja_name, start_pos)
                if idx < 0:
                    break
                end_pos = idx + len(ja_name)
                is_covered = any(cs <= idx and end_pos <= ce for cs, ce in covered_spans)
                if not is_covered:
                    uncovered_positions.append((idx, end_pos))
                start_pos = idx + 1

            if not uncovered_positions:
                continue
            covered_spans.extend(uncovered_positions)

            # Có mention tên JA trong source — kiểm tra theo ranh giới từ (\b) để 'miku' không khớp nhầm 'mikuru'
            canonical_lower = canonical_vi.lower()
            if re.search(r'\b' + re.escape(canonical_lower) + r'\b', target_lower):
                continue  # OK — đã có canonical đúng từ

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

            # Cover case Takao/Kawaguchi: target có canonical của 1 nhân vật KHÁC
            # trong config (alias hoặc also_known_as) nhưng không có canonical này
            aka_wrong = self._find_wrong_forms_with_aka(
                ja_name, canonical_vi, target_line, source_line=source_line
            )
            if aka_wrong:
                fixed = target_line
                for wrong in aka_wrong:
                    pattern = re.compile(r'\b' + re.escape(wrong) + r'\b', re.IGNORECASE)
                    fixed = pattern.sub(canonical_vi, fixed)
                if fixed != target_line:
                    return fixed
        return None

    def _find_wrong_forms_with_aka(
        self,
        ja_name: str,
        canonical: str,
        target: str,
        source_line: str = "",
    ) -> List[str]:
        """Tìm các dạng sai bao gồm cả 'also_known_as' từ config.

        Trường hợp: source JA = "川口くん" (→ Kawaguchi), target VI = "Takao"
        → Takao là alias của 1 nhân vật khác trong config, nhưng cũng là also_known_as
        của Kawaguchi → phải fix Takao → Kawaguchi.
        """
        canonical_map = self._load_canonical_map()
        target_lower = target.lower()
        wrong = []
        canonical_lower = canonical.lower()
        # Tìm key của canonical
        key = canonical.lower()
        # Lấy also_known_as (nếu có)
        aka = []
        if key in canonical_map:
            aka = canonical_map[key].get("also_known_as", []) or []

        # Nếu có also_known_as được cấu hình tường minh cho nhân vật này -> kiểm tra trước
        for a in aka:
            if a and a.lower() != canonical_lower:
                if re.search(r'\b' + re.escape(a.lower()) + r'\b', target_lower):
                    wrong.append(a)

        # Kiểm tra các nhân vật khác trong canonical_map (chỉ khi nhân vật đó KHÔNG xuất hiện trong câu nguồn)
        for ck, entry in canonical_map.items():
            entry_canonical = entry["canonical"]
            if entry_canonical.lower() == canonical_lower:
                continue  # Bỏ chính nó
            all_aliases = entry.get("aliases", []) or []
            all_akas = entry.get("also_known_as", []) or []
            # Nếu nhân vật này cũng có mặt trong câu gốc JA (câu có 2 nhân vật), tuyệt đối không ghi đè tên họ!
            if source_line and any(a and a in source_line for a in all_aliases):
                continue
            # Chỉ coi là nhầm lẫn giữa 2 nhân vật nếu có liên kết also_known_as
            if not aka and not all_akas:
                continue
            for alias in all_aliases + all_akas:
                if not alias or alias.lower() == canonical_lower:
                    continue
                if re.search(r'\b' + re.escape(alias.lower()) + r'\b', target_lower) and canonical_lower not in target_lower:
                    wrong.append(alias)
        return wrong

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

    def _load_canonical_map(self, raw_lines: Optional[List[str]] = None) -> Dict[str, dict]:
        """Load character_names từ config.yaml + injected + auto-extracted. Cache per-instance."""
        if getattr(self, "_canonical_map_cache", None) is not None and not raw_lines:
            return self._canonical_map_cache
        try:
            import yaml
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"CharacterRegistry: failed to load config.yaml: {e}")
            cfg = {}

        entries = list(cfg.get("character_names", []) or [])
        if self.injected_character_names:
            entries.extend(self.injected_character_names)
        if raw_lines:
            try:
                from .ja_qa_checker import extract_character_names_from_lines
                entries = extract_character_names_from_lines(raw_lines, existing_names=entries)
            except Exception as e:
                logger.debug(f"CharacterRegistry auto-extract skipped: {e}")

        result = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            canonical = entry.get("canonical", "").strip()
            if not canonical:
                continue
            aliases = list(entry.get("aliases", []) or [])
            mistaken = list(entry.get("mistaken_aliases", []) or [])
            for m in mistaken:
                if m and m not in aliases:
                    aliases.append(m)
            also_known_as = list(entry.get("also_known_as", []) or [])
            key = canonical.lower()
            if key in result:
                for a in aliases:
                    if a not in result[key]["aliases"]:
                        result[key]["aliases"].append(a)
                for aka in also_known_as:
                    if aka not in result[key]["also_known_as"]:
                        result[key]["also_known_as"].append(aka)
            else:
                result[key] = {
                    "canonical": canonical,
                    "aliases": aliases,
                    "also_known_as": also_known_as,
                }
        self._canonical_map_cache = result
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

        canonical_present = bool(re.search(r'\b' + re.escape(canonical_lower) + r'\b', target_lower))

        # (1) Alias check từ config
        if key in canonical_map:
            for alias in canonical_map[key]["aliases"]:
                if alias.lower() == canonical_lower:
                    continue
                if re.search(r'\b' + re.escape(alias.lower()) + r'\b', target_lower):
                    if not canonical_present and alias not in wrong:
                        wrong.append(alias)

        # (2) Substring rút gọn: kiểm tra word nào trong target bắt đầu
        # bằng 2 ký tự đầu của canonical nhưng KHÔNG phải canonical
        if not canonical_present and len(canonical) >= 4:
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
                                   'kia', 'nay', 'do', 'miku', 'sato', 'tsuyoshi'}:
                        if w_lower != canonical_lower:
                            pass
                        continue
                    if w not in wrong:
                        wrong.append(w)

        # (3) Fuzzy similarity (rapidfuzz) cho các từ viết hoa không dấu tiếng Việt gần giống canonical
        if canonical_lower not in target_lower and len(canonical) >= 4:
            try:
                from rapidfuzz import fuzz
                cap_words = re.findall(r'\b[A-Z][a-z]{2,11}\b', target)
                for w in cap_words:
                    w_low = w.lower()
                    if w_low == canonical_lower or w in wrong:
                        continue
                    if fuzz.ratio(w_low, canonical_lower) >= 68:
                        wrong.append(w)
            except Exception:
                pass
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
