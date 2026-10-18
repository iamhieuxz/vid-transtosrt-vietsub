"""Test JaTranslator2 với debug chi tiết - write to file"""
import logging
import sys
import json

logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('test')

from core.translator import TranslatorService
from core.ja_translator2 import JaTranslator2

# Load config
import yaml
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

model_cfg = config['models'].get('ja', config.get('model', {}))
translator = TranslatorService(
    model_name=model_cfg['name'],
    ollama_url=model_cfg['ollama_url'],
    temperature=model_cfg.get('temperature', 0.1),
    repeat_penalty=model_cfg.get('repeat_penalty', 1.2),
    num_ctx=model_cfg.get('num_ctx', 8192),
    num_predict=model_cfg.get('num_predict', 1024),
    timeout=model_cfg.get('timeout', 180),
)

results = []

# Test 1: Simple prompt
print('=== Test 1: Simple prompt ===')
try:
    result = translator.generate('Dịch câu này sang tiếng Việt: こんにちは')
    results.append({'test': 'Test 1', 'result': result})
    print('OK - see results.json')
except Exception as e:
    results.append({'test': 'Test 1', 'error': str(e)})
    print(f'Error: {e}')

# Test 2: JaTranslator2 refine
print('=== Test 2: JaTranslator2 batch 2 ===')
try:
    refiner = JaTranslator2(translator, batch_size=2)
    source = ['こんにちは', 'さようなら']
    trans = ['Xin chào', 'Tạm biệt']
    result = refiner.refine(source, trans)
    results.append({'test': 'Test 2', 'result': result, 'type': str(type(result))})
    print('OK - see results.json')
except Exception as e:
    results.append({'test': 'Test 2', 'error': str(e)})
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()

# Test 3: JaTranslator2 với 5 dòng
print('=== Test 3: JaTranslator2 batch 5 ===')
try:
    refiner = JaTranslator2(translator, batch_size=5)
    source = [
        'おはようございます',
        'こんにちは',
        'こんばんは',
        'さようなら',
        'ありがとう'
    ]
    trans = [
        'Chào buổi sáng',
        'Xin chào',
        'Chào buổi tối',
        'Tạm biệt',
        'Cảm ơn'
    ]
    result = refiner.refine(source, trans)
    results.append({'test': 'Test 3', 'result': result})
    print('OK - see results.json')
except Exception as e:
    results.append({'test': 'Test 3', 'error': str(e)})
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()

# Write results to file
with open('test_results.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print('\n=== Results written to test_results.json ===')
