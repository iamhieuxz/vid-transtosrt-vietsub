"""
Module kiem tra chat luong file SRT truoc khi dich.
Phat hien cac loi pho bien cua Whisper (nghe nham), tu Nhat co van de,
cau qua ngan, ky tu la, va goi y sua chua.
"""
import re
import logging
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)


# Các pattern whisper thường nghe sai
WHISPER_COMMON_MISTAKES = {
    # Whisper hay nghe nhầm
    "ツマゴ": "卵子 (trứng) hoặc 妻 (vợ) tùy ngữ cảnh",
    "生態室": "治療室 (phòng trị liệu)",
    "腹見": "お腹見せ (cho xem bụng) - KHÔNG PHẢI 'kiểm tra bụng'",
    "腹見なさい": "Câu khiêu dâm: 'cho tôi xem/ấn vào bụng em'",
    "旦那": "chồng / ông chủ",
    "かゆこ": "Tên riêng - giữ nguyên Kachiko",
    "たけお": "Tên riêng - giữ nguyên Takao",
    "高田": "Tên riêng - giữ nguyên Takada",
    "タケホー": "Tên riêng - giữ nguyên Takesho/Takao",
    "あゆみ": "Tên riêng - giữ nguyên Ayumi",
    "二階": "tầng hai",
    "奥の部屋": "phòng trong cùng",
    "つなわたり": "đi về phía trước",
    "ビールある": "có bia rồi",
    "家計測": "quản lý tài chính gia đình",
    "生態師": "整体師 (thầy trị liệu)",
    "素顔": "sức mạnh thật / khuôn mặt thật",
    "支持": "hỗ trợ / ủng hộ",
    "家にいるとき": "khi ở nhà",
    "末っ子": "con út",
    "旦那": "anh / chồng",
}

# Các câu thường bị model "làm sạch" quá mức (uncen)
UNCEN_SANITIZED_PATTERNS = [
    r"お腹.*見せ",
    r"妊娠.*し.*やす",
    r"子供.*作",
    r"性行為",
    r"sex",
    r"SEX",
    r"fuck",
]

# Tên riêng phổ biến trong JAV
COMMON_NAMES = [
    "Takesho", "Takao", "Takeo", "Ayumi", "Ayumi-san",
    "Kachiko", "Kayuko", "Takada", "Takeda",
    "Yamada", "Tanaka", "Suzuki", "Sato", "Watanabe",
    "Ito", "Kimura", "Hayashi",
]


class WhisperPreCheck:
    """Phát hiện và gợi ý sửa các lỗi phổ biến của Whisper trước khi dịch."""

    def __init__(self, source_lang: str = 'ja'):
        self.source_lang = source_lang
        self.issues: List[Dict] = []
        self.suggestions: List[str] = []

    def check_file(self, srt_path: str) -> Dict:
        """
        Kiểm tra file SRT và trả về báo cáo.
        Returns: {
            'total_lines': int,
            'issues_count': int,
            'critical': List[Dict],
            'warnings': List[Dict],
            'suggestions': List[str],
        }
        """
        self.issues = []
        self.suggestions = []

        try:
            with open(srt_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Khong the doc file {srt_path}: {e}")
            return {'total_lines': 0, 'issues_count': 0, 'critical': [], 'warnings': [], 'suggestions': []}

        # Parse SRT
        blocks = self._parse_srt(content)
        total_lines = len(blocks)

        # Chạy các kiểm tra
        if self.source_lang == 'ja':
            self._check_ja_patterns(blocks)

        self._check_short_lines(blocks)
        self._check_special_chars(blocks)
        self._check_whisper_artifacts(content)
        self._check_name_honorific(blocks)

        # Phân loại
        critical = [i for i in self.issues if i['severity'] == 'critical']
        warnings = [i for i in self.issues if i['severity'] == 'warning']

        return {
            'total_lines': total_lines,
            'issues_count': len(self.issues),
            'critical': critical,
            'warnings': warnings,
            'suggestions': self.suggestions,
        }

    def _parse_srt(self, content: str) -> List[Dict]:
        """Parse SRT thành list of dicts."""
        blocks = []
        # Split by double newline
        raw_blocks = re.split(r'\n\s*\n', content.strip())
        for raw in raw_blocks:
            lines = raw.strip().split('\n')
            if len(lines) < 3:
                continue
            try:
                idx_line = lines[0].strip()
                if not idx_line.isdigit():
                    continue
                idx = int(idx_line)
                time_line = lines[1].strip()
                text_lines = [l.strip() for l in lines[2:] if l.strip()]
                blocks.append({
                    'id': idx,
                    'time': time_line,
                    'text': '\n'.join(text_lines),
                })
            except (ValueError, IndexError):
                continue
        return blocks

    def _check_ja_patterns(self, blocks: List[Dict]):
        """Kiểm tra các pattern Nhật hay bị Whisper nghe sai."""
        for block in blocks:
            text = block['text']
            # Kiểm tra các từ nghi ngờ
            for wrong, correct in WHISPER_COMMON_MISTAKES.items():
                if wrong in text:
                    self.issues.append({
                        'line_id': block['id'],
                        'text': text,
                        'pattern': wrong,
                        'suggestion': correct,
                        'severity': 'critical' if wrong in ['ツマゴ', '腹見', '生態室', '生態師'] else 'warning',
                    })
                    self.suggestions.append(
                        f"Line {block['id']}: thay '{wrong}' → '{correct}' (gốc: {text[:60]!r})"
                    )

    def _check_short_lines(self, blocks: List[Dict]):
        """Cảnh báo các câu quá ngắn (1-2 ký tự) thường là lỗi whisper."""
        for block in blocks:
            text = block['text'].strip()
            # Câu chỉ có 1-2 ký tự thường là lỗi
            if 0 < len(text) <= 2 and text not in ['。', '、', '!', '?', '！', '？', '...']:
                self.issues.append({
                    'line_id': block['id'],
                    'text': text,
                    'pattern': 'too_short',
                    'suggestion': 'Câu quá ngắn - có thể là lỗi Whisper',
                    'severity': 'warning',
                })

    def _check_special_chars(self, blocks: List[Dict]):
        """Kiểm tra ký tự đặc biệt bất thường."""
        for block in blocks:
            text = block['text']
            # BOM, zero-width space
            for bad_char in ['\ufeff', '\u200b', '\u200c', '\u200d']:
                if bad_char in text:
                    self.issues.append({
                        'line_id': block['id'],
                        'text': text[:50],
                        'pattern': f'invisible_char:{repr(bad_char)}',
                        'suggestion': 'Strip ký tự invisible',
                        'severity': 'warning',
                    })

    def _check_whisper_artifacts(self, content: str):
        """Kiểm tra artifact phổ biến của Whisper trong toàn file."""
        # Dấu hiệu whisper output bị lỗi nặng
        artifacts = [
            (r'\[BLANK_AUDIO\]', 'Blank audio marker'),
            (r'\(.*\)', 'Có ngoặc đơn - thường là whisper thêm vào'),
            (r'\[.*\]', 'Có ngoặc vuông - có thể là whisper meta'),
        ]
        for pattern, desc in artifacts:
            matches = re.findall(pattern, content)
            if matches:
                self.suggestions.append(f"Phát hiện {len(matches)} '{desc}' - nên review")

    def _check_name_honorific(self, blocks: List[Dict]):
        """Phát hiện tên riêng có kèm honorific suffix bất thường.

        Whisper hay output tên Nhật kèm honorific (さん, くん, ちゃん, 様)
        mà LLM dịch có thể hiểu nhầm (vd: 匠さん → "Craft-san" thay vì "Takumi-san").
        Pattern phổ biến cần flag:
          - Tên + honorific bị tách khỏi câu (đứng riêng 1 dòng)
          - Honorific viết sai (sữa, kun, chan thay vì さん, くん, ちゃん)
          - Tên có dấu "-" hoặc ":" ở giữa (whisper hay chèn)

        shortcut: chỉ flag, KHÔNG auto-fix (vì LLM đã dịch rồi, để registry handle).
        """
        # Pattern 1: tên kèm honorific đứng riêng (có thể là câu trả lời gọi tên)
        standalone_name = re.compile(
            r'^[　\s]*([ぁ-んァ-ン一-龯]{2,})(さん|様|ちゃん|くん|君|殿|氏)\s*[。.!?]?\s*$',
            re.MULTILINE
        )
        # Pattern 2: honorific viết sai romaji (san, kun, chan, sama)
        wrong_honorific = re.compile(
            r'\b([A-Z][a-z]+)[- ](san|kun|chan|sama)\b',
        )
        # Pattern 3: tên có dấu ":" hoặc "-" giữa (whisper hay chèn)
        colon_name = re.compile(r'([ぁ-んァ-ン一-龯]{2,})[-:]([ぁ-んァ-ン一-龯]{2,})')

        for block in blocks:
            text = block.get('text', '')
            if not text:
                continue
            idx = block.get('line_id', block.get('id', block.get('index', 0)))
            # Check pattern 1: standalone name
            for m in standalone_name.finditer(text):
                self.issues.append({
                    'line_id': idx,
                    'severity': 'warning',
                    'type': 'name_standalone',
                    'message': f"Tên riêng đứng riêng: {m.group(0).strip()!r} - "
                               f"có thể là câu trả lời gọi tên, cần review ngữ cảnh",
                })
            # Check pattern 2: wrong honorific
            for m in wrong_honorific.finditer(text):
                self.issues.append({
                    'line_id': idx,
                    'severity': 'warning',
                    'type': 'honorific_romaji',
                    'message': f"Honorific viết sai romaji: {m.group(0)!r} - "
                               f"Whisper artifact, LLM có thể dịch sai",
                })
            # Check pattern 3: colon/hyphen
            for m in colon_name.finditer(text):
                self.issues.append({
                    'line_id': idx,
                    'severity': 'warning',
                    'type': 'name_colon',
                    'message': f"Tên có dấu phân cách: {m.group(0)!r} - "
                               f"có thể là 2 nhân vật nói liên tiếp",
                })

    def auto_fix_simple(self, srt_path: str, output_path: Optional[str] = None) -> str:
        """
        Tự động sửa các lỗi đơn giản mà KHÔNG cần LLM.
        Returns đường dẫn file đã sửa.
        """
        output_path = output_path or srt_path

        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()

        original = content
        fixes_applied = []

        # 1. Strip BOM
        if content.startswith('\ufeff'):
            content = content[1:]
            fixes_applied.append('strip BOM')

        # 2. Strip zero-width chars
        for bad in ['\u200b', '\u200c', '\u200d', '\u2060']:
            count = content.count(bad)
            if count > 0:
                content = content.replace(bad, '')
                fixes_applied.append(f'strip {count}x {repr(bad)}')

        # 3. Normalize newlines
        content = re.sub(r'\r\n', '\n', content)
        content = re.sub(r'\r', '\n', content)
        content = re.sub(r'\n{3,}', '\n\n', content)

        # 4. Fix common whisper mistakes (conservative)
        ja_corrections = {
            # Whisper hay nghe "素顔" thành các từ vô nghĩa
            # Nhưng đây là false positive cao nên KHÔNG auto-fix
        }
        # (giữ comment - không auto-fix để tránh sai)

        if content != original:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Auto-fix applied: {', '.join(fixes_applied)}")
            logger.info(f"Saved to: {output_path}")
        else:
            logger.info("No auto-fix needed")

        return output_path

    def report(self, srt_path: str) -> str:
        """Tạo báo cáo text để in ra console."""
        result = self.check_file(srt_path)

        lines = []
        lines.append(f"\n{'='*60}")
        lines.append(f"  WHISPER PRE-CHECK REPORT: {srt_path}")
        lines.append(f"{'='*60}")
        lines.append(f"Total lines: {result['total_lines']}")
        lines.append(f"Issues found: {result['issues_count']}")
        lines.append(f"  Critical: {len(result['critical'])}")
        lines.append(f"  Warnings: {len(result['warnings'])}")

        if result['critical']:
            lines.append(f"\n{'-'*60}")
            lines.append("  CRITICAL ISSUES (cần review trước khi dịch):")
            lines.append(f"{'-'*60}")
            for issue in result['critical'][:20]:  # Top 20
                line_id = issue.get('line_id', issue.get('line', '?'))
                lines.append(f"  Line {line_id}: {issue.get('pattern', issue.get('type', 'issue'))}")
                text = issue.get('text', issue.get('message', ''))
                lines.append(f"    Text: {text[:70]}")
                if issue.get('suggestion'):
                    lines.append(f"    Fix:  {issue['suggestion']}")
                lines.append("")

        if result['warnings']:
            lines.append(f"\n{'-'*60}")
            lines.append(f"  WARNINGS ({len(result['warnings'])} total, top 10):")
            lines.append(f"{'-'*60}")
            for issue in result['warnings'][:10]:
                line_id = issue.get('line_id', issue.get('line', '?'))
                label = issue.get('pattern', issue.get('type', 'issue'))
                text = issue.get('text', issue.get('message', ''))
                lines.append(f"  Line {line_id}: {label} - {text[:50]}")

        if result['suggestions']:
            lines.append(f"\n{'-'*60}")
            lines.append("  SUGGESTIONS:")
            lines.append(f"{'-'*60}")
            for s in result['suggestions'][:10]:
                lines.append(f"  • {s}")

        lines.append(f"\n{'='*60}\n")
        return '\n'.join(lines)


def run_precheck(srt_path: str, source_lang: str = 'ja', auto_fix: bool = True) -> Dict:
    """
    Hàm tiện ích: chạy pre-check trên file SRT.
    Returns: dict kết quả
    """
    checker = WhisperPreCheck(source_lang=source_lang)

# Auto-fix đơn giản trước
    if auto_fix:
        srt_path = checker.auto_fix_simple(srt_path)

    # Report (shortcut: gán trước để tránh UnboundLocalError nếu report() raise)
    report_text = ""
    try:
        report_text = checker.report(srt_path)
    except Exception as e:
        report_text = f"[!] Precheck report failed: {e}"

    # Safe print cho Windows console
    try:
        print(report_text)
    except UnicodeEncodeError:
        # Fallback: encode sang ASCII-safe
        safe_text = report_text.encode('ascii', 'replace').decode('ascii')
        print(safe_text)

    return checker.check_file(srt_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python core/precheck.py <srt_path> [lang]")
        sys.exit(1)
    path = sys.argv[1]
    lang = sys.argv[2] if len(sys.argv) > 2 else 'ja'
    run_precheck(path, source_lang=lang)
