"""Test 2-tier pipeline."""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load config
import yaml
with open('config.yaml', 'r', encoding='utf-8-sig') as f:
    config = yaml.safe_load(f)

print('=== CONFIG CHECK ===')
print('ja_context.enabled:', config.get('ja_context', {}).get('enabled'))
print('ja_context.window_size:', config.get('ja_context', {}).get('window_size'))
print()

# Test pipeline import
print('=== IMPORT CHECK ===')
from core.pipeline import TranslationPipeline
print('TranslationPipeline OK')
from core.ja_context_analyzer import JaContextAnalyzer
print('JaContextAnalyzer OK')
from core.database import Database
print('Database OK')

# Test get_context_window
print()
print('=== DB METHOD CHECK ===')
db_path = config.get('database', {}).get('path', 'data/translator.db')
os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else '.', exist_ok=True)
db = Database(db_path)
if hasattr(db, 'get_context_window'):
    print('db.get_context_window OK')
else:
    print('db.get_context_window NOT FOUND')
    sys.exit(1)

# Test init pipeline
print()
print('=== PIPELINE INIT ===')
try:
    pipeline = TranslationPipeline(config)
    print('Pipeline init OK')
    print('  ja_2tier_enabled:', pipeline.ja_2tier_enabled)
    print('  ja_context_analyzer:', pipeline.ja_context_analyzer is not None)
    print('  is_japanese_source:', pipeline._is_japanese_source)
except Exception as e:
    print('Pipeline init FAILED:', e)
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test _get_context_block (with no context in DB)
print()
print('=== _get_context_block TEST ===')
# Mock project_id = 9999 (khong co)
result = pipeline._get_context_block(9999, 0, 5)
print('Empty context result (should be empty string):', repr(result))
if result == '':
    print('OK - returns empty when no context')
else:
    print('FAIL - should return empty string')
    sys.exit(1)

print()
print('=' * 60)
print('ALL CHECKS PASSED!')
print('=' * 60)
print()
print('De chay 2-tier pipeline voi AV1.srt:')
print('  1. Make sure Ollama is running: ollama serve')
print('  2. Run: python main.py')
print('  3. Set input to: E:\\HIDE\\JAV\\AV1\\AV1.srt')
print('  4. Choose [7] Bat dau dich')
print()
print('Worker A se:')
print('  - Doc 174 dong, chia thanh 10 context-windows (18 dong/cua)')
print('  - Goi model 10 lan de phan tich speakers, pronouns, tone, setting')
print('  - Luu vao bang window_contexts trong DB')
print()
print('Worker B se:')
print('  - Query context_window theo start_pos')
print('  - Inject context block vao prompt')
print('  - Dich voi nhan vat + xung ho nhat quan toan phim')
