# -*- coding: utf-8 -*-
"""Test cac fix chat luong dich sau kiem tra NSPS-692.

- Validator chan ky tu CJK/Cyrillic lot vao ban dich
- _split_by_words tach cue dai bang word timestamps
"""
import io
import os
import sys
import types

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAIL = []


def check(ok, label):
    print(('  PASS ' if ok else '  FAIL ') + label)
    if not ok:
        FAIL.append(label)


# ================================ 1. Validator chan script leak
print('=== 1. Validator chan ky tu Nhat/Cyrillic lot vao ban dich ===')
from core.validator import Validator

v = Validator()

# Cue 39 cua NSPS-692: "Nó cứ lừ đừ不出来 mà" -> phai REJECT
ok = not v.validate_window_content(
    ['だ Burgundy が出てこないし'],
    ['Nó cứ lừ đừ不出来 mà'],
)
check(ok, 'chan "不出来" (CJK loai) trong ban dich')

# Cue 159: "Vậy thì chỉ一杯 thôi" -> phai REJECT
ok = not v.validate_window_content(
    ['じゃあ一杯だけ'],
    ['Vậy thì chỉ一杯 thôi'],
)
check(ok, 'chan "一杯" (CJK loai) trong ban dich')

# Cue 95: "Миho đại ca" -> Cyrillic -> phai REJECT
ok = not v.validate_window_content(
    ['ミホ兄貴来たぞ'],
    ['Миho đại ca đến đây rùi nhỉ'],
)
check(ok, 'chan "Миho" (Cyrillic loai) trong ban dich')

# Kana loai -> REJECT
ok = not v.validate_window_content(['ああ'], ['Vângミホさん'])
check(ok, 'chan kana "ミホ" lot vao ban dich Viet')

# KHONG duoc bao dong gia: ban dich binh thuong
ok = v.validate_window_content(['朝から会議なんでしょ'], ['Thời gian à, từ sáng đã có họp rồi nhỉ'])
check(ok, 'ban dich Viet binh thuong van PASS')

ok = v.validate_window_content(['結婚して3年'], ['Đã kết hôn được 3 năm'])
check(ok, 'ban dich co so Viet van PASS')

# Ten Nhat duoc dich san thi van PASS (chuẩn phu de Viet chi dung Latin)
ok = v.validate_window_content(
    ['ミホさん、お招きありがとうございます'],
    ['Cảm ơn chị Miho đã mời'],
)
check(ok, 'ten Nhat duoc dich san, khong con ky tu la gai van PASS')

# Nhung giu ten Nhat kem nghia trong ban dich van la loi -> reject
ok = not v.validate_window_content(
    ['ミホさん、お招きありがとうございます'],
    ['Cảm ơn chị Miho (ミホ) đã mời'],
)
check(ok, 'chan khi con giu ten Nhat kem nghia trong ban dich')

print('\n=== 2. _split_by_words tach cue dai ===')
from core.transcriber import WhisperTranscriber


def w(word, start, end):
    return types.SimpleNamespace(word=word, start=start, end=end)


# Segment 70.6s voi 2 cau, moi cau ~3s thuc te
seg = types.SimpleNamespace(
    start=0.53,
    end=71.16,
    text='時間よ 朝から会議なんでしょ',
    words=[
        w('時間よ', 0.53, 1.90),
        w('朝から', 2.10, 2.80),
        w('会議', 2.90, 3.60),
        w('なんで', 3.70, 4.30),
        w('でしょ', 4.40, 5.00),
    ],
)

# Tao object ma khong can load model that
t = WhisperTranscriber.__new__(WhisperTranscriber)
t.max_cue_ms = 6000

cues = t._split_by_words(seg, 0.0)
print('  Input: segment 70.63s, 5 words (nhanh)')
for s, e, txt in cues:
    print('    %s --> %s  %r' % (s, e, txt))
check(len(cues) == 1, 'chunk nhanh giu nguyen 1 cue')
check(cues[0][0] == '00:00:00,530' and cues[0][1] == '00:00:05,000',
      'dung word timestamps thay vi timestamp doan sai cua segment (70s -> 5s)')

# Segment 70.6s voi nhieu tu -> phai tach
seg_long = types.SimpleNamespace(
    start=0.53,
    end=71.16,
    text='a。b。c。d。e。f。',
    words=[
        w('時間よ。', 0.53, 4.0),
        w('朝から会議。', 4.5, 9.0),
        w('早く起きて。', 9.5, 14.0),
        w('準備して。', 14.5, 19.0),
        w('朝食できてる。', 19.5, 24.0),
        w('から早く。', 24.5, 29.0),
        w('準備してきて。', 29.5, 34.0),
        w('ね早く準備。', 34.5, 39.0),
        w('いただきます。', 39.5, 44.0),
        w('結婚して。', 44.5, 49.0),
        w('三年。', 49.5, 54.0),
        w('夫には。', 54.5, 59.0),
        w('不満はない。', 59.5, 64.0),
    ],
)
cues = t._split_by_words(seg_long, 0.0)
print('  Input: segment 70.63s, %d words' % len(seg_long.words))
for s, e, txt in cues:
    print('    %s --> %s  %r' % (s, e, txt))
check(len(cues) > 1, 'segment dai tach thanh %d cue (truoc: 1 cue 70s)' % len(cues))

# Mỗi cue phai nằm trong ngan cua code: 1.5x max_ms + 1 tu (trường hợp xấu nhất
# khi chunk vừa vượt ngưỡng rồi gặp 1 từ dài). Test data o day: ~1.05s/tu -> 9.5s.
def _secs(ts):
    h, m, rest = ts.split(':')
    return int(h) * 3600 + int(m) * 60 + float(rest.replace(',', '.'))


max_dur = max(_secs(e) - _secs(s) for s, e, _ in cues)
print('  Cue dai nhat sau khi tach: %.1fs (guoc 1.5x max_ms = %.1fs)'
      % (max_dur, t.max_cue_ms * 1.5 / 1000))
check(max_dur <= t.max_cue_ms * 1.5 / 1000 + 1.5,
      'khong con cue vo le (9.5s thay vi 70s/1794s cua NSPS-692)')
check(max_dur < 15.0, 'mọi cue duoi 15s — doc kip theo chuan phu de')

# Khong mat text
all_text = ''.join(txt for _, _, txt in cues)
check(all_text == ''.join(x.word for x in seg_long.words),
      'khong mat chu khi tach cue')

# Segment khong co words -> fallback 1 cue
seg_nw = types.SimpleNamespace(start=0.0, end=100.0, text='test', words=None)
cues = t._split_by_words(seg_nw, 0.0)
check(len(cues) == 1 and cues[0][1] == '00:01:40,000',
      'khong co word timestamps -> fallback 1 cue goc')

# Offset (transcribe_interval)
cues = t._split_by_words(seg_nw, 60.0)
check(cues[0][0] == '00:01:00,000', 'offset duoc cong don')

print('\n=== KET QUA ===')
if FAIL:
    print('FAIL %d:' % len(FAIL))
    for f in FAIL:
        print('  -', f)
    sys.exit(1)
print('TAT CA PASS')