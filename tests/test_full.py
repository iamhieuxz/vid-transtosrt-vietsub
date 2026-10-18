import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import fix_srt_with_rules

# Test trên file gốc AV1.srt
out_path, fixed, violations = fix_srt_with_rules(r'E:\HIDE\JAV\AV1\AV1.srt')

print()
print('=' * 80)
print('KET QUA CUOI CUNG')
print('=' * 80)
print('So dong sua:', fixed)
print('So violations:', len(violations))

# Verify cac loi da duoc sua
with open(r'E:\HIDE\JAV\AV1\AV1.srt', 'r', encoding='utf-8') as f:
    orig = f.read()
with open(out_path, 'r', encoding='utf-8') as f:
    new = f.read()

print()
print('Kiem tra cac tu loi con sot:')
for p in ['vâng', 'Garam tsuite', 'Vagina', 'Anus', 'Ayunee', 'âmvật']:
    print('  "{}": goc={}, moi={}'.format(p, orig.count(p), new.count(p)))
