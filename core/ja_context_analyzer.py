"""Japanese context analyzer (Worker A) — tóm tắt ngữ cảnh cho 2-tier window.

Module này chạy PRE-PROCESSING trước khi pipeline main dịch:
  - Chia toàn bộ SRT thành các "context-window" (mặc định 20 dòng = 2 translation-window).
  - Với mỗi context-window, gọi model LLM 1 lần để trích xuất:
    + speakers (ai nói)
    + pronouns_map (anh/chị/em mapping giữa các cặp nhân vật)
    + tone (formal / casual / mixed / intimate)
    + setting (bối cảnh: quán cafe, công ty, nhà riêng...)
    + summary (tóm tắt tình huống 1-2 câu)
  - Lưu vào bảng ``window_contexts`` (DB).

Khi Worker B (main pipeline) chạy polish pass cho translation-window i, nó sẽ
query context của context-window chứa i để inject vào prompt → keigo + xưng hô
nhất quán xuyên suốt phim.

Dùng cùng ``TranslatorService`` với main pipeline (không cần thêm model).
"""

from __future__ import annotations

import json
import logging
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Prompt templates
# -----------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích ngữ cảnh phụ đề tiếng Nhật, hỗ trợ dịch giả Việt Nam.
Nhiệm vụ: trích xuất thông tin về nhân vật, ngôi xưng, tone, bối cảnh từ đoạn phụ đề.
"""

_CONTEXT_ANALYSIS_TEMPLATE = """\
{system}

Phân tích đoạn phụ đề tiếng Nhật sau (gồm {n} dòng). Trích xuất:

1. **speakers**: Danh sách nhân vật xuất hiện (theo tên / biệt danh / xưng hô).
   VD: ["Taro", "Hanako", "Nhân viên quán"].

2. **pronouns_map**: Ánh xạ xưng hô giữa các cặp nhân vật. Với mỗi cặp (A, B):
   - A nói với B: A dùng "tôi"/"anh"/"em"/"cậu"/"tớ"/"tao"...
   - B nói với A: B dùng "tôi"/"anh"/"em"/"cậu"/"tớ"/"tao"...
   Dựa trên keigo và particle (ね, よ, ぞ, かな, わ...) trong câu.
   Format: {{"Taro->Hanako": {{"self": "tớ", "other": "cậu"}}, "Hanako->Taro": {{"self": "tớ", "other": "cậu"}}}}
   Nếu không rõ, để {{"self": null, "other": null}}.

3. **tone**: Một trong: "formal" (khách/lần đầu gặp), "casual" (quen thân), "intimate" (rất thân), "mixed" (nhiều cấp độ).

4. **setting**: Bối cảnh ngắn gọn (VD: "quán cafe", "văn phòng", "nhà riêng ban đêm", "trường học").

5. **summary**: Tóm tắt tình huống trong đoạn này (1-2 câu, tiếng Việt).

QUY TẮC ĐẦU RA:
- Trả về ĐÚNG JSON object (không phải array), không kèm text ngoài JSON.
- Schema: {{"speakers": [...], "pronouns_map": {{...}}, "tone": "...", "setting": "...", "summary": "..."}}
- Nếu đoạn quá ngắn / không có thoại → trả JSON rỗng (speakers=[], tone="unknown", setting="unknown", summary="").

ĐOẠN PHỤ ĐỀ:
{lines}
"""


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _number_lines(lines: List[str]) -> str:
    return "\n".join(f"{i+1}. {l}" for i, l in enumerate(lines))


def _safe_parse_json(text: str) -> Optional[dict]:
    """Parse JSON từ model output, chịu lỗi JSON repair."""
    try:
        from json_repair import repair_json
        return json.loads(repair_json(text))
    except Exception:
        pass
    # Fallback: tìm JSON object lớn nhất
    import re
    try:
        matches = re.findall(r"\{.*\}", text, re.DOTALL)
        for match in reversed(matches):
            try:
                parsed = json.loads(match)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    except Exception:
        pass
    return None


# -----------------------------------------------------------------------------
# Worker A
# -----------------------------------------------------------------------------

class JaContextAnalyzer:
    """Tóm tắt ngữ cảnh toàn phim trước khi pipeline main chạy.

    Parameters
    ----------
    translator : TranslatorService
        Đã được khởi tạo với model phù hợp cho tiếng Nhật.
    context_window_size : int
        Số dòng source mỗi context-window (mặc định 20).
    timeout_per_window : int
        Timeout mỗi call model (giây).
    """

    def __init__(self, translator, context_window_size: int = 18, timeout_per_window: int = 60):
        self.translator = translator
        self.context_window_size = context_window_size
        self.timeout_per_window = timeout_per_window

    def analyze_project(self, project_id: int, db) -> dict:
        """Scan toàn bộ project, tạo context cho từng context-window.

        Returns dict với stats: {total, completed, failed, skipped}.
        """
        items = db.get_all_items(project_id)
        if not items:
            logger.warning("JaContextAnalyzer: no items in project")
            return {"total": 0, "completed": 0, "failed": 0, "skipped": 0}

        # Chia thành context-window (20 dòng mỗi window, không overlap)
        ctx_size = self.context_window_size
        windows = []
        for i in range(0, len(items), ctx_size):
            chunk = items[i:i + ctx_size]
            windows.append({
                "context_window_index": i // ctx_size,
                "start_pos": i,
                "end_pos": i + len(chunk) - 1,
                "lines": [it["original_text"] for it in chunk],
            })

        # Check xem context nào đã có rồi (cache hit)
        existing = {c["context_window_index"]: c for c in db.list_context_windows(project_id)}
        existing_completed = sum(1 for c in existing.values() if c["status"] == "completed")

        stats = {
            "total": len(windows),
            "completed": existing_completed,
            "failed": 0,
            "skipped": 0,
        }

        logger.info(
            f"JaContextAnalyzer: {len(windows)} context-windows "
            f"({existing_completed} cached, {len(windows) - existing_completed} to analyze)"
        )

        for w in windows:
            idx = w["context_window_index"]
            # Skip nếu đã completed
            if idx in existing and existing[idx]["status"] == "completed":
                continue
            try:
                result = self._analyze_one_window(w["lines"])
                if result is None:
                    db.mark_context_failed(project_id, idx, "Empty/invalid JSON")
                    stats["failed"] += 1
                    continue
                db.save_window_context(
                    project_id, idx, w["start_pos"], w["end_pos"],
                    speakers_json=json.dumps(result.get("speakers", []), ensure_ascii=False),
                    pronouns_map=json.dumps(result.get("pronouns_map", {}), ensure_ascii=False),
                    tone=result.get("tone", "unknown"),
                    setting=result.get("setting", "unknown"),
                    summary=result.get("summary", ""),
                    status="completed",
                )
                stats["completed"] += 1
                logger.debug(f"Context-window {idx}: OK")
            except Exception as e:
                logger.warning(f"Context-window {idx} failed: {e}")
                db.mark_context_failed(project_id, idx, str(e))
                stats["failed"] += 1

        return stats

    def _analyze_one_window(self, source_lines: List[str]) -> Optional[dict]:
        """Gọi model 1 lần để phân tích 1 context-window."""
        numbered = _number_lines(source_lines)
        prompt = _CONTEXT_ANALYSIS_TEMPLATE.format(
            system=_SYSTEM_PROMPT,
            n=len(source_lines),
            lines=numbered,
        )
        start = time.time()
        try:
            raw = self.translator.generate(prompt, temperature=0.0)
        except Exception as e:
            logger.warning(f"JaContextAnalyzer: generate failed: {e}")
            return None
        elapsed = time.time() - start
        if elapsed > self.timeout_per_window:
            logger.warning(f"JaContextAnalyzer: slow window ({elapsed:.1f}s)")
        return _safe_parse_json(raw)
