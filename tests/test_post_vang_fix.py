"""Test post-processor fix 'vâng' -> 'hai' hallucination."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.post_processor import RuleBasedPostProcessor


def test_basic_vang_fixes():
    """LLM gõ 'vâng' thay vì 'hai' cho số 2 trong tiếng Nhật."""
    pp = RuleBasedPostProcessor()
    cases = [
        # ('input', 'expected_output')
        ('Khi vâng người kết hôn và có con', 'Khi hai người kết hôn và có con'),
        ('Vì vâng người có mối quan hệ rất thân', 'Vì hai người có mối quan hệ rất thân'),
        ('Cả vâng đều vội vàng yêu cầu nhau', 'Cả hai đều vội vàng yêu cầu nhau'),
        ('Anh có định kết hôn không?', 'Anh có định kết hôn không?'),  # unchanged
        ('Hôm nay vâng người cùng ăn cơm', 'Hôm nay hai người cùng ăn cơm'),
        # Capitalize preserve
        ('Vâng người đi', 'Hai người đi'),
        ('Vâng anh em ruột', 'Hai anh em ruột'),
        ('Vâng bên đều sai', 'Hai bên đều sai'),
        ('Vâng vợ chồng về', 'Hai vợ chồng về'),
    ]
    for inp, expect in cases:
        out = pp.process_line(inp)
        assert out == expect, f'FAIL: {inp!r} -> {out!r} (expect {expect!r})'
    print('test_basic_vang_fixes: 9/9 pass')


def test_real_jur708():
    """Apply to JUR-708.srt thực tế - phải catch 4 lỗi đã identify."""
    import re
    srt_path = r'E:\HIDE\JAV\JUR-708\JUR-708.srt'
    if not os.path.exists(srt_path):
        print('test_real_jur708: SKIP (file not present)')
        return
    with open(srt_path, 'r', encoding='utf-8') as f:
        text = f.read()
    blocks = re.split(r'\n\s*\n', text.strip())
    subs = []
    for b in blocks:
        lines = b.strip().split('\n')
        if len(lines) >= 3:
            subs.append((lines[0], '\n'.join(lines[2:])))

    pp = RuleBasedPostProcessor()
    changes = []
    for idx, content in subs:
        new = pp.process_line(content)
        if new != content:
            changes.append((idx, content, new))

    # Phải catch tối thiểu 4 dòng (16, 36, 79, 115)
    assert len(changes) >= 4, f'Catches {len(changes)}, expected >= 4'
    # Verify các dòng cụ thể
    indices_changed = {c[0] for c in changes}
    for required in ('16', '36', '79', '115'):
        assert required in indices_changed, f'Missing line {required}'
    # Verify output chứa "hai" thay vì "vâng" trong các dòng đó
    for idx, before, after in changes:
        if idx in ('16', '36', '79', '115'):
            assert 'vâng' not in after, f'Line {idx} still has "vâng": {after!r}'
    print(f'test_real_jur708: {len(changes)}/171 lines fixed, 4 critical lines all caught')


if __name__ == '__main__':
    test_basic_vang_fixes()
    test_real_jur708()
    print('\nAll tests passed!')
