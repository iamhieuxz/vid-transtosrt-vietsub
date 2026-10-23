# -*- coding: utf-8 -*-
"""Test GlossaryMiner — kiem tra no chi day term uncen, bo qua rac."""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.glossary_miner import GlossaryMiner

FAIL = []


def check(ok, label):
    print(('  PASS ' if ok else '  FAIL ') + label)
    if not ok:
        FAIL.append(label)


class FakeDB:
    """DB giả trả về các cặp đã dịch."""

    def __init__(self, items):
        self._items = items

    def get_all_items(self, project_id):
        return [
            {'original_text': ja, 'translated_text': vi, 'sub_index': i}
            for i, (ja, vi) in enumerate(self._items)
        ]


# Dữ liệu mô phỏng: 8 dòng, có 2 cụm JA lặp lại với VI tương ứng
items = [
    ('ペニスは大きい', 'Dương vật to lắm'),
    ('あrogens', 'Dương vật to lắm'),
    ('雨が降ってる', 'Trời đang mưa'),
    ('ペニスは大きい', 'Dương vật to lắm'),
    ('妻とセックスした', 'Quan hệ tình dục với vợ'),
    ('明日は晴れ', 'Ngày mai nắng'),
    ('ペニスは大きい', 'Dương vật to lắm'),
    ('妻とセックスした', 'Quan hệ tình dục với vợ'),
    ('ペニスは大きい', 'Dương vật to lắm'),
    ('昨日は忙しかった', 'Hôm qua tôi bận'),
    ('妻とセックスした', 'Quan hệ tình dục với vợ'),
    ('妻とセックスした', 'Quan hệ tình dục với vợ'),
]

miner = GlossaryMiner(FakeDB(items), {})
# Chạy KHÔNG lọc để test cơ chế học; bước dò trùng có test riêng ở mục 4.
cands = miner.mine(1, skip_existing=False)

print('=== GlossaryMiner output ===')
for c in cands:
    print('  %r -> %r  (freq=%d, conf=%s)' % (
        c['source_term'], c['target_term'], c['freq'], c['confidence']))
print()

# 1. Phai tim thay cum uncen lap lai
srcs = [c['source_term'] for c in cands]
check(any('ペニス' in s for s in srcs), 'tim thay cum chua ペニス (lap 4 lan)')
check(any('セックス' in s for s in srcs), 'tim thay cum chua セックス (lap 3 lan)')

# 2. KHONG day tu thuong (khong chua tu khoa uncen)
check(not any('雨' in s for s in srcs), 'KHONG day cum tu thuong (雨が降ってる)')
check(not any('晴' in s for s in srcs), 'KHONG day cum tu thuong (明日は晴れ)')

# 3. Target phai la tieng Viet
bad_target = [c for c in cands if any(
    '\u3040' <= ch <= '\u9fff' for ch in c['target_term']
)]
check(not bad_target, 'mọi target deu la tieng Viet (khong con kana/kanji)')

# 4. KHONG hoc lai term da co trong glossary_uncen.yaml
#    Chay voi skip_existing=True (mac dinh cua pipeline)
cands_on = miner.mine(1, skip_existing=True)
srcs_on = [c['source_term'] for c in cands_on]
check('セックス' not in srcs_on, 'KHONG hoc lai セックス (da co trong uncen)')
check('ペニス' not in srcs_on, 'KHONG hoc lai ペニス (da co trong uncen)')
print()
print('  voi skip_existing=True con %d candidate' % len(cands_on))

# 4b. KHONG hoc lai n-gram MO RONG cua term da co
#     旦那 là n-gram con của 旦那さん nhưng 旦那 đã có trong uncen
m = GlossaryMiner(FakeDB([('旦那さん ', 'anh chồng ơi')] * 5), {})
m.existing = {'旦那'}
check(m._is_already_covered('旦那さん', 'anh chồng ơi'),
      'n-gram MO RONG cua term da co bi bo qua (旦那さん chứa 旦那)')

# 4c. Bo qua khi target da ton tai duoi hinh thuc khac
m2 = GlossaryMiner(FakeDB([]), {})
m2.existing = {'ちんぽ'}
m2.__dict__['_targets_cache'] = {'dương vật'}
check(m2._is_already_covered('ちんこ', 'dương vật'),
      'bo qua khi target da ton tai (da hoc roi duoi hinh thuc khac)')

# 4d. KHONG bo qua term THAT su moi
m3 = GlossaryMiner(FakeDB([]), {})
m3.existing = {'セックス'}
m3.__dict__['_targets_cache'] = {'quan hệ tình dục'}
check(not m3._is_already_covered('フェラチオ', 'hút dương vật'),
      'KHONG bo qua term that su moi')

# 5. Chi lay cum dac thuoc, khong phai cụm con la của cụm lon hon
top = max((c['freq'] for c in cands), default=0)
check(all(c['freq'] >= 3 for c in cands),
      'chi lay cum freq >= 3 (nguong cua config)')

# 6. Cap so luong
check(len(cands) <= 50, 'gioi han 50 candidate')

print('\n=== 7. Nguong min_ratio loai cap do kiem chung ===')
# Cap chung xuat hien nhieu nhung khong goc nhau: 妻 xuat hien 6 lan,
# "Quan hệ" xuat hien 4 lan -> P(ja|vi) = 4/4 = 1.0 nhung P(vi|ja) = 4/6 = 0.67
# Van > 0.5 nen van giu. Dong thoi se co nhieu cap bi nhieu hon.
pairs = [
    ('妻がいる', 'Vợ ơi'),
    ('妻がいた', 'Có vợ rồi'),
    ('妻と話した', 'Nói chuyện với vợ'),
    ('妻と歩いた', 'Đi cùng vợ'),
    ('妻を見た', 'Nhìn vợ'),
    ('妻が笑った', 'Vợ cười'),
]
m2 = GlossaryMiner(FakeDB(pairs), {'pipeline': {'learn_glossary_min_freq': 2,
                                                 'learn_glossary_min_ratio': 0.9}})
c2 = m2.mine(1)
print('  voi min_ratio=0.9, con %d candidate' % len(c2))
check(len(c2) == 0, 'nguong cao loai cap do kiem chung')

print('\n=== KET QUA ===')
if FAIL:
    print('FAIL %d:' % len(FAIL))
    for f in FAIL:
        print('  -', f)
    sys.exit(1)
print('TAT CA PASS')