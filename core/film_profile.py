"""Hồ sơ phim (thể loại, bối cảnh, tóm tắt, vai) tổng hợp từ kết quả Worker A.

Nguyên tắc: tên nhân vật KHÔNG lấy từ output LLM. Danh sách tên đến từ
extract_character_names_from_lines (fugashi, đọc thẳng dòng JA); Worker A chỉ được
phép gán vai cho tên đó. Một tên chỉ được xác nhận khi Worker A nhắc tới nó VÀ alias JA
của nó có thật trong đoạn thoại đó, ở >= min_windows cửa sổ.

Đầu ra:
  - bảng film_profiles (DB) -> chèn vào prompt dịch
  - glossary_<phim>.characters.yaml -> file CHỜ DUYỆT, không tự nạp vào pipeline
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

_JA_CHAR_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_HONORIFICS = ("さん", "ちゃん", "くん", "様")

_PROFILE_PROMPT = """\
Dưới đây là tóm tắt từng đoạn của một phim người lớn Nhật Bản (phụ đề tiếng Nhật).
Các tóm tắt có thể sai chi tiết; chỉ rút ra điểm chung xuất hiện lặp lại.

TÊN NHÂN VẬT ĐÃ XÁC NHẬN: {names}

Trả về ĐÚNG một JSON object, không kèm chữ nào khác:
{{"genre": "...", "setting": "...", "synopsis": "...", "roles": {{"<tên>": "<vai/quan hệ>"}}}}

QUY TẮC:
- Mọi giá trị viết bằng TIẾNG VIỆT, tuyệt đối không dùng chữ Nhật.
- genre: thể loại ngắn gọn (vd: "drama gia đình, ngoại tình").
- setting: bối cảnh chính của cả phim, 1 cụm.
- synopsis: 3-4 câu, chỉ dùng các tên đã xác nhận; người không rõ tên gọi là "người đàn ông" / "người phụ nữ".
- roles: chỉ gồm các tên đã xác nhận, mỗi tên 1 cụm ngắn (vd: "chị dâu", "em chồng thất nghiệp"). Không chắc thì để "".
- KHÔNG thêm bất kỳ tên riêng nào ngoài danh sách.

TÓM TẮT CÁC ĐOẠN:
{summaries}
"""


def _strip_honorific(alias: str) -> str:
    for h in _HONORIFICS:
        if alias.endswith(h):
            return alias[: -len(h)]
    return alias


def _entry_keys(entry: dict) -> List[str]:
    """Các chuỗi dùng để khớp 1 nhân vật: canonical + alias JA gốc (không hậu tố)."""
    keys = {entry["canonical"]}
    for a in entry.get("aliases") or []:
        if _JA_CHAR_RE.search(a):
            keys.add(_strip_honorific(a))
    return [k for k in keys if len(k) >= 2]


def match_name(text: str, entries: List[dict]) -> Optional[str]:
    """Trả canonical nếu `text` (vd 'Mei-san', 'メイ') chứa tên/alias của nhân vật đã xác nhận."""
    low = text.lower()
    for e in entries:
        for k in _entry_keys(e):
            if k.lower() in low:
                return e["canonical"]
    return None


def confirm_names(windows: List[dict], window_texts: List[str], candidates: List[dict],
                  min_windows: int = 3) -> List[dict]:
    """Giữ nhân vật mà Worker A nhắc tới VÀ alias JA có thật trong đoạn thoại, ở >= min_windows cửa sổ."""
    confirmed = []
    for e in candidates:
        if not e.get("canonical"):
            continue
        ja_keys = [k for k in _entry_keys(e) if _JA_CHAR_RE.search(k)]
        n = 0
        for w, text in zip(windows, window_texts):
            try:
                speakers = json.loads(w.get("speakers_json") or "[]")
            except Exception:
                speakers = []
            listed = any(match_name(s, [e]) for s in speakers if isinstance(s, str))
            if listed and any(k in text for k in ja_keys):
                n += 1
        if n >= min_windows:
            confirmed.append({"canonical": e["canonical"], "aliases": e.get("aliases") or [], "windows": n})
    return confirmed


def _validate_profile(data, names: List[str], unconfirmed: Optional[List[str]] = None) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    out = {}
    for k in ("genre", "setting", "synopsis"):
        v = data.get(k, "")
        if not isinstance(v, str) or _JA_CHAR_RE.search(v):
            return None
        out[k] = v.strip()
    # Model hay chép tên chưa xác nhận từ các tóm tắt đoạn vào synopsis -> loại để retry
    blob = " ".join(out.values()).lower()
    if any(re.search(rf"\b{re.escape(n.lower())}\b", blob) for n in (unconfirmed or [])):
        return None
    roles = data.get("roles", {})
    if not isinstance(roles, dict):
        return None
    out["roles"] = {}
    for name, role in roles.items():
        canon = match_name(str(name), [{"canonical": n, "aliases": []} for n in names])
        if canon and isinstance(role, str) and role.strip() and not _JA_CHAR_RE.search(role):
            out["roles"][canon] = role.strip()
    if not (out["genre"] or out["synopsis"]):
        return None
    return out


def build_film_profile(db, project_id: int, translator, candidates: List[dict],
                       min_windows: int = 3, max_summaries: int = 60) -> Optional[dict]:
    """Tổng hợp hồ sơ phim từ window_contexts đã có. Trả dict profile (đã lưu DB) hoặc None."""
    from .ja_context_analyzer import _safe_parse_json

    windows = [w for w in db.list_context_windows(project_id) if w.get("status") == "completed"]
    if not windows:
        return None
    items = db.get_all_items(project_id)
    texts = ["\n".join(it.get("original_text") or "" for it in items[w["start_pos"]:w["end_pos"] + 1])
             for w in windows]

    confirmed = confirm_names(windows, texts, candidates, min_windows)
    names = [c["canonical"] for c in confirmed]
    # Bỏ tóm tắt lẫn chữ Nhật (model trả sai ngôn ngữ) trước khi gom
    unconfirmed = [c["canonical"] for c in candidates if c.get("canonical") and c["canonical"] not in names]
    sums = [w["summary"].strip() for w in windows
            if (w.get("summary") or "").strip() and not _JA_CHAR_RE.search(w["summary"])][:max_summaries]
    # Che tên chưa xác nhận ngay ở đầu vào để model không chép lại vào synopsis
    for n in unconfirmed:
        sums = [re.sub(rf"\b{re.escape(n)}(-?(san|chan|kun))?\b", "một người", s, flags=re.IGNORECASE) for s in sums]

    source_hash = hashlib.sha1(("\n".join(sums) + "|" + ",".join(names)).encode("utf-8")).hexdigest()[:16]
    cached = db.get_film_profile(project_id)
    if cached and cached.get("source_hash") == source_hash:
        return {**cached, "roles": json.loads(cached["roles_json"] or "{}"),
                "names": json.loads(cached["names_json"] or "[]")}

    profile = None
    if sums and names:
        prompt = _PROFILE_PROMPT.format(
            names=", ".join(names),
            summaries="\n".join(f"- {s[:200]}" for s in sums),
        )
        for temp in (0.0, 0.3):
            try:
                raw = translator.generate(prompt, temperature=temp, _retries=0)
            except Exception as e:
                logger.warning(f"film_profile: generate failed @temp={temp}: {e}")
                continue
            profile = _validate_profile(_safe_parse_json(raw), names, unconfirmed)
            if profile:
                break
    if profile is None:
        profile = {"genre": "", "setting": "", "synopsis": "", "roles": {}}

    db.save_film_profile(
        project_id, profile["genre"], profile["setting"], profile["synopsis"],
        json.dumps(profile["roles"], ensure_ascii=False),
        json.dumps(confirmed, ensure_ascii=False), source_hash,
    )
    return {**profile, "names": confirmed, "source_hash": source_hash}


def load_profile(db, project_id: int) -> Optional[dict]:
    row = db.get_film_profile(project_id)
    if not row:
        return None
    return {"genre": row.get("genre") or "", "setting": row.get("setting") or "",
            "synopsis": row.get("synopsis") or "",
            "roles": json.loads(row.get("roles_json") or "{}"),
            "names": json.loads(row.get("names_json") or "[]")}


def render_profile_block(profile: Optional[dict]) -> str:
    """Block chèn vào prompt dịch. Rỗng nếu không có gì đáng tin."""
    if not profile:
        return ""
    parts = []
    if profile.get("genre"):
        parts.append(f"- Thể loại: {profile['genre']}")
    if profile.get("setting"):
        parts.append(f"- Bối cảnh chung: {profile['setting']}")
    if profile.get("roles"):
        parts.append("- Vai: " + "; ".join(f"{n} = {r}" for n, r in profile["roles"].items()))
    if profile.get("synopsis"):
        parts.append(f"- Tóm tắt phim: {profile['synopsis']}")
    if not parts:
        return ""
    return "HỒ SƠ PHIM (chỉ để hiểu ngữ cảnh, KHÔNG dịch lại nội dung này):\n" + "\n".join(parts) + "\n\n"


def write_characters_glossary(profile: dict, project_name: str) -> str:
    """Ghi tên + vai ra glossaries/glossary_<phim>.characters.yaml (CHỜ DUYỆT, pipeline không tự nạp)."""
    import yaml
    safe = re.sub(r"[^\w\-_]", "_", project_name or "unknown")
    base_dir = "glossaries" if os.path.isdir("glossaries") else ""
    path = os.path.join(base_dir, f"glossary_{safe}.characters.yaml") if base_dir else f"glossary_{safe}.characters.yaml"
    if os.path.exists(path):  # file đã duyệt tay: không ghi đè, xuất bản mới bên cạnh
        path = os.path.join(base_dir, f"glossary_{safe}.characters.new.yaml") if base_dir else f"glossary_{safe}.characters.new.yaml"
    entries = []
    for n in profile.get("names", []):
        role = (profile.get("roles") or {}).get(n["canonical"], "")
        hint = f"tên nhân vật" + (f", vai: {role}" if role else "") + f" (xác nhận ở {n.get('windows', '?')} đoạn)"
        ja = {_strip_honorific(a) for a in n.get("aliases", []) if _JA_CHAR_RE.search(a)}
        for src in sorted(ja, key=lambda s: (len(s), s)):
            entries.append({"source_term": src, "target_term": n["canonical"], "context_hint": hint})
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {path}\n# TỰ SINH từ Worker A, CHỜ DUYỆT. Kiểm tra rồi copy entry đúng sang glossary_{safe}.yaml.\n\n")
        if entries:
            yaml.safe_dump(entries, f, allow_unicode=True, sort_keys=False)
        else:
            f.write("[]\n")
    return path
