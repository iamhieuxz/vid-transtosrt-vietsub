# -*- coding: utf-8 -*-
"""Regression test cho _clean_markdown — 8 lỗi leak đo được ở JUL-409.

Bug gốc: model 8B trả về "JA -> VN" (lặp cả cặp so sánh) và đổi ký tự cuối
sang bảng mã Thái/Han ("giống nhauเลย", "xấu ý too", "từ giờ开始").
Regex cũ chỉ bắt markdown bold + leading arrow nên cả 8 dòng lọt xuống SRT.
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ja_translator import _clean_markdown

# (input, expected) — lấy nguyên văn từ JUL-409 output
CASES = [
    # 1. "JA -> VN" leak
    ('Cảm ơn → Cảm ơn anh/chị', 'Cảm ơn anh/chị'),
    ('とにかくここに住むなら → Nếu quyết định ở đây thì', 'Nếu quyết định ở đây thì'),
    ('ルールをちゃんと守ってくれよ → Hãy tuân thủ quy tắc thật kỹ nhé',
     'Hãy tuân thủ quy tắc thật kỹ nhé'),
    ('食器も洗濯も全部自分で洗うんだよ → Tất cả việc dọn dẹp, giặt quần áo đều phải tự tay làm đấy',
     'Tất cả việc dọn dẹp, giặt quần áo đều phải tự tay làm đấy'),
    ('ああ → À ừ', 'À ừ'),
    # 2. junk tail (Thái / Han)
    ('Hoàn toàn không giống nhauเลย đấy', 'Hoàn toàn không giống nhau đấy'),
    ('Vì nghĩ rằng mình xấu ý too', 'Vì nghĩ rằng mình xấu ý'),
    ('Vậy thì hôm nay từ giờ开始', 'Vậy thì hôm nay từ giờ'),
    # 3. regression: KHÔNG được phá bản dịch hợp lệ
    ('Cảm ơn anh/chị', 'Cảm ơn anh/chị'),
    ('Tôi sẽ xuất tinh trong', 'Tôi sẽ xuất tinh trong'),
    ('Hút dương vật cho đến khi anh ra', 'Hút dương vật cho đến khi anh ra'),
    ('Đúng rồi呢', 'Đúng rồi'),   # chữ Hán bám đuôi (JUL-837 #18)
    ('', ''),
    # 4. markdown bold + leading arrow (bug cũ, không được hỏng)
    ('**Quan hệ tình dục**', 'Quan hệ tình dục'),
    ('→ Người tình', 'Người tình'),
]


def test_all():
    failed = []
    for raw, expected in CASES:
        got = _clean_markdown(raw)
        if got != expected:
            failed.append((raw, expected, got))
    return failed


if __name__ == '__main__':
    fails = test_all()
    for raw, exp, got in fails:
        print('[FAIL] %r\n   expected %r\n   got      %r' % (raw, exp, got))
    print('\n%d/%d passed' % (len(CASES) - len(fails), len(CASES)))
    sys.exit(1 if fails else 0)