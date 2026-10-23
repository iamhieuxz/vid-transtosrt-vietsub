# -*- coding: utf-8 -*-
"""Tim target_term / context_hint tieng Viet mat dau trong glossary_*.yaml.

Loi nay rat nghiem trong: LLM doc "nguoi tinh" (mat dau) se dich tiep
"bạn tình" → hỏng đúng mục tiêu chính của mode uncen.
"""
import glob
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import yaml

# Tu Viet co dau bat buoc — neu target khong co dau la LOI (tru khi la ten rieng).
REQUIRE_DIACRITIC = re.compile(
    r'\b(nguoi|tinh|khong|duoc|truong|vo|chong|ngoai|uong|bo|di|lam|'
    r'cam|goi|thich|nhanh|moi|roi|den|ra|vao|cho|khit)\b'
)


def has_diacritic(s):
    return any('\u00c0' <= c <= '\u1ef3' for c in s)


bad = []
for f in sorted(glob.glob('glossary*.yaml')):
    data = yaml.safe_load(io.open(f, encoding='utf-8').read())
    for e in data:
        tgt = e.get('target_term', '')
        # Bỏ qua tên riêng (chữ Latin) — chúng không cần dấu
        if re.match(r'^[A-Za-z][A-Za-z\s\-\.]*$', tgt):
            continue
        m = REQUIRE_DIACRITIC.search(tgt)
        if m and not has_diacritic(tgt):
            bad.append((f, e['source_term'], tgt))

if bad:
    print('[FAIL] %d target_term mat dau tieng Viet:' % len(bad))
    for f, s, t in bad:
        print('  %-24s %-14s -> %r' % (f, s, t))
    print('\n-> LLM se doc tu nay va tu dich tiep sang tu lich su.')
    sys.exit(1)

print('[ OK ] tat ca target_term giu dau tieng Viet')