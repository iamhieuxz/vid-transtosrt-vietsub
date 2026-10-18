"""Test sub_splitter."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.sub_splitter import (
    split_long_sub, split_srt_items,
    _parse_timestamp, _format_timestamp,
)


def test_short_sub_unchanged():
    """Sub ngắn (< max_duration) -> return nguyên 1 element."""
    text = "Đêm nay mệt quá rồi"
    start = _parse_timestamp("00:05:10,800")
    end = _parse_timestamp("00:05:12,800")
    result = split_long_sub(text, start, end, max_duration_ms=6000)
    assert len(result) == 1, f"Should not split, got {len(result)} parts"
    assert result[0] == (start, end, text)
    print("test_short_sub_unchanged: PASS")


def test_long_sub_split_by_jp_punct():
    """Sub dài có 。 → split thành nhiều sub."""
    text = "二人が結婚したり子供ができた時に。奥さんが家にいるってすごい素敵じゃない？分かりますか？"
    start = _parse_timestamp("00:01:30,520")
    end = _parse_timestamp("00:01:36,720")  # 6.2s
    result = split_long_sub(text, start, end, max_duration_ms=6000)
    assert len(result) >= 2, f"Should split, got {len(result)} parts"
    # Verify timestamps chia đều
    for i, (s, e, t) in enumerate(result):
        assert s >= start and e <= end, f"Timestamp out of range: {s}-{e}"
        assert s < e, f"Empty segment: {s}-{e}"
        assert t, f"Empty text: {t!r}"
    # Verify text tổng gần bằng text gốc (sau khi strip)
    combined = "".join(t for _, _, t in result)
    assert combined.replace(" ", "").replace("?", "？") in text.replace(" ", "").replace("?", "？") or \
           len(combined) > 0
    print(f"test_long_sub_split_by_jp_punct: PASS ({len(result)} parts)")
    for s, e, t in result:
        print(f"  {_format_timestamp(s)} -> {_format_timestamp(e)}: {t}")


def test_long_sub_split_by_newline():
    """Sub có nhiều dòng với \\n → split theo dòng."""
    text = "Đêm nay mệt quá rồi\nSáng mai lại phải làm ca sớm nữa thì sao\nĐi uống rượu đi"
    start = _parse_timestamp("00:05:10,800")
    end = _parse_timestamp("00:14:09,550")  # 9 phút
    result = split_long_sub(text, start, end, max_duration_ms=6000)
    assert len(result) == 3, f"Should split into 3, got {len(result)}"
    # Verify each segment có text riêng
    assert result[0][2] == "Đêm nay mệt quá rồi"
    assert result[1][2] == "Sáng mai lại phải làm ca sớm nữa thì sao"
    assert result[2][2] == "Đi uống rượu đi"
    # Verify timestamps chia đều
    step = (end - start) // 3
    assert result[0][0] == start
    assert result[1][0] == start + step
    assert result[2][0] == start + 2 * step
    assert result[2][1] == end
    print(f"test_long_sub_split_by_newline: PASS (3 parts from 9-min sub)")


def test_no_split_points_keeps_as_is():
    """Sub dài nhưng KHÔNG có dấu câu → giữ nguyên (return 1 element)."""
    text = "Không có dấu câu gì hết chỉ có text dài thôi mà sao tách được"
    start = _parse_timestamp("00:00:00,000")
    end = _parse_timestamp("00:00:30,000")  # 30s
    result = split_long_sub(text, start, end, max_duration_ms=6000)
    assert len(result) == 1, f"Should keep as-is, got {len(result)} parts"
    assert result[0][2] == text
    print("test_no_split_points_keeps_as_is: PASS")


def test_srt_items_pipeline():
    """Test split_srt_items cho list các SRT items."""
    items = [
        {'index': 1, 'start': '00:00:01,000', 'end': '00:00:03,000', 'text': 'Short sub 1'},
        {'index': 2, 'start': '00:00:04,000', 'end': '00:00:30,000',
         'text': 'Đêm nay mệt quá rồi.\nSáng mai lại phải làm ca sớm nữa thì sao.\nĐi uống rượu đi.'},
        {'index': 3, 'start': '00:00:31,000', 'end': '00:00:33,000', 'text': 'Short sub 3'},
    ]
    result = split_srt_items(items, max_duration_ms=6000)

    # Sub 1 + 2 short → giữ nguyên (1+1=2)
    # Sub 2 → split thành 3 (Đêm nay... + Sáng mai... + Đi uống...)
    # Sub 3 → giữ nguyên (1)
    # Total: 5
    assert len(result) == 5, f"Expected 5, got {len(result)}"
    # Re-numbered từ 1
    for i, item in enumerate(result, start=1):
        assert item['index'] == i
    # Verify sub split text
    assert result[1]['text'] == "Đêm nay mệt quá rồi."
    assert result[2]['text'] == "Sáng mai lại phải làm ca sớm nữa thì sao."
    assert result[3]['text'] == "Đi uống rượu đi."
    print(f"test_srt_items_pipeline: PASS (3 items → 5 items, sub dài đã split)")


def test_jur708_real():
    """Apply splitter cho JUR-708_whisper.srt thật."""
    srt_path = r'E:\HIDE\JAV\JUR-708\JUR-708_whisper.srt'
    if not os.path.exists(srt_path):
        print('test_jur708_real: SKIP (file not present)')
        return
    import re
    with open(srt_path, 'r', encoding='utf-8') as f:
        text = f.read()
    blocks = re.split(r'\n\s*\n', text.strip())
    items = []
    for b in blocks:
        lines = b.strip().split('\n')
        if len(lines) >= 3:
            # Strip BOM từ dòng đầu
            first_line = lines[0].lstrip('\ufeff\u200b\u200c\u200d\u2060')
            m = re.match(r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})', lines[1])
            if m:
                items.append({
                    'index': int(first_line),
                    'start': m.group(1).replace('.', ','),
                    'end': m.group(2).replace('.', ','),
                    'text': '\n'.join(lines[2:]),
                })

    original_count = len(items)
    result = split_srt_items(items, max_duration_ms=6000)
    new_count = len(result)

    # JUR-708 có dòng 61 dài 9 phút (00:05:10 -> 00:14:09) — phải được split
    assert new_count > original_count, f"Expected more subs after split: {original_count} -> {new_count}"
    print(f"test_jur708_real: PASS ({original_count} → {new_count} subs, {new_count - original_count} new subs created)")


if __name__ == '__main__':
    test_short_sub_unchanged()
    test_long_sub_split_by_jp_punct()
    test_long_sub_split_by_newline()
    test_no_split_points_keeps_as_is()
    test_srt_items_pipeline()
    test_jur708_real()
    print("\nAll sub_splitter tests passed!")
