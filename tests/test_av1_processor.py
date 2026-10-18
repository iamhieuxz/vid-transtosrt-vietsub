"""
Test rule processor len file AV1_fixed.srt.
"""
import sys
import re
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.rules import get_rule_processor

# Doc file AV1.srt (file goc sai)
input_path = r'E:\HIDE\JAV\AV1\AV1.srt'
output_path = r'E:\HIDE\JAV\AV1\AV1_processed.srt'

with open(input_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Parse
blocks = []
raw_blocks = re.split(r'\n\s*\n', content.strip())
for raw in raw_blocks:
    lines = raw.strip().split('\n')
    if len(lines) < 3:
        continue
    try:
        idx = int(lines[0].strip())
        time_line = lines[1].strip()
        text = '\n'.join(l.strip() for l in lines[2:] if l.strip())
        blocks.append({'id': idx, 'time': time_line, 'text': text})
    except ValueError:
        continue

# Apply processor
processor = get_rule_processor('config.yaml')
all_violations = []

for b in blocks:
    new_text, violations = processor.process_translation(b['text'])
    if new_text != b['text']:
        b['text'] = new_text
        all_violations.extend([(b['id'], v) for v in violations])

# Ghi file moi
new_content_parts = []
for b in blocks:
    new_content_parts.append(f"{b['id']}\n{b['time']}\n{b['text']}")
new_content = '\n\n'.join(new_content_parts) + '\n'

with open(output_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print('=' * 80)
print('KET QUA AP DUNG RULE PROCESSOR')
print('=' * 80)
print('So dong xu ly:', len(blocks))
print('So violations:', len(all_violations))
print()
print('Cac violations chi tiet:')
for line_id, v in all_violations[:20]:
    print('  Line {}: pattern="{}" -> "{}"'.format(line_id, v['pattern'], v['replacement']))

# Dem cac tu van con
print()
print('Kiem tra tu loi con sot:')
problems = ['vâng', 'Garam tsuite', 'Vagina', 'Anus', 'Ayunee', 'âmvật']
for p in problems:
    c = new_content.count(p)
    print('  "{}": {}'.format(p, c))

print()
print('File moi:', output_path)
