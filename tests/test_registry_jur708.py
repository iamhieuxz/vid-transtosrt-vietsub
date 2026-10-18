"""Test character registry với JUR-708 thật."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import re
import yaml
from core.character_registry import CharacterRegistry


def test_jur708_real():
    """Apply registry validate cho từng dòng trong JUR-708.srt (VI output).
    Expect phát hiện tối thiểu 1-2 lỗi: Ayumi->Aya, Takumi->Craft, Kawaguchi->Takao.
    """
    srt_path = r'E:\HIDE\JAV\JUR-708\JUR-708.srt'
    if not os.path.exists(srt_path):
        print('test_jur708_real: SKIP (file not present)')
        return
    with open(srt_path, 'r', encoding='utf-8') as f:
        text = f.read()
    blocks = re.split(r'\n\s*\n', text.strip())
    subs = []
    for b in blocks:
        lines = b.strip().split('\n')
        if len(lines) >= 3:
            subs.append((lines[0], lines[1], '\n'.join(lines[2:])))

    # Lấy whisper JA từng dòng tương ứng (cùng index)
    ja_path = r'E:\HIDE\JAV\JUR-708\JUR-708_whisper.srt'
    with open(ja_path, 'r', encoding='utf-8') as f:
        text_ja = f.read()
    blocks_ja = re.split(r'\n\s*\n', text_ja.strip())
    subs_ja = {}
    for b in blocks_ja:
        lines = b.strip().split('\n')
        if len(lines) >= 3:
            subs_ja[lines[0]] = '\n'.join(lines[2:])

    # Mock DB với speakers giả lập (từ context của phim JUR-708)
    from unittest.mock import MagicMock
    db = MagicMock()
    # Speakers có thể xuất hiện trong các context-window (mock từ phân tích trước)
    # Window 0: cảnh party giới thiệu → Kawaguchi, Sayu, Takumi, Ayumi
    # Window 1: cảnh tâm sự → Kawaguchi, Ayumi
    # Window 2: cảnh thân mật → Takumi (được gọi là 匠), Kawaguchi
    db.list_context_windows.return_value = [
        {"context_window_index": 0, "status": "completed",
         "speakers_json": '["川口くん", "さゆちゃん", "たくみさん", "あゆみさん"]'},
        {"context_window_index": 1, "status": "completed",
         "speakers_json": '["川口くん", "あゆみさん"]'},
        {"context_window_index": 2, "status": "completed",
         "speakers_json": '["匠さん", "川口くん"]'},
    ]
    reg = CharacterRegistry(db, project_id=1)
    reg.build()
    print(f'  Registry: {len(reg._cache)} speakers')
    for k, v in reg._cache.items():
        print(f'    {k} -> {v}')

    # Validate từng dòng
    issues = []
    for idx, timing, vi in subs:
        ja = subs_ja.get(idx, '')
        if not ja or not vi:
            continue
        fixed = reg.validate_translation(ja, vi)
        if fixed and fixed != vi:
            issues.append((idx, vi, fixed))

    print(f'\n  {len(issues)} dòng có thể auto-fix:')
    for idx, before, after in issues[:10]:
        print(f'    {idx}:')
        print(f'      BEFORE: {before}')
        print(f'      AFTER:  {after}')
    assert len(issues) >= 1, f'Expected at least 1 fix, got {len(issues)}'
    print(f'test_jur708_real: PASS ({len(issues)} issues found)')


if __name__ == '__main__':
    test_jur708_real()
