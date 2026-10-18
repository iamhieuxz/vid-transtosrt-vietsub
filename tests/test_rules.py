import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.rules import get_rule_processor

print('=== TEST RULE PROCESSOR ===')
processor = get_rule_processor('config.yaml')
print('Canonical map:', len(processor.canonical_map), 'entries')
print('Anti-artifact patterns:', len(processor.artifact_patterns), 'rules')
print()

# Test case 1: Loi "vâng" thay vì "hai"
test1 = "Cả vâng bên đều sướng"
fixed1, violations1 = processor.process_translation(test1)
print('TEST 1: Sua "vâng" -> "hai"')
print('  Goc:', test1)
print('  Moi:', fixed1)
print('  Violations:', len(violations1))
for v in violations1:
    print('    -', v['reason'])
print()

# Test case 2: Ten nhan vat
test2 = "Ayunee à, anh thich Ayune lam"
fixed2, violations2 = processor.process_translation(test2)
print('TEST 2: Ten nhan vat nhat quan')
print('  Goc:', test2)
print('  Moi:', fixed2)
print()

# Test case 3: Whisper artifact
test3 = "Garam tsuite ageru yo (anh se lam em sung suong)"
fixed3, violations3 = processor.process_translation(test3)
print('TEST 3: Whisper artifact')
print('  Goc:', test3)
print('  Moi:', fixed3)
print()

# Test case 4: Tu tieng Anh goc
test4 = "Anh se lam tinh bang Vagina va Anus"
fixed4, violations4 = processor.process_translation(test4)
print('TEST 4: Tu tieng Anh goc')
print('  Goc:', test4)
print('  Moi:', fixed4)
