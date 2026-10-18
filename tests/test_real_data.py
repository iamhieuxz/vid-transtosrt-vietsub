"""Test JaTranslator2 với dữ liệu thực tế - avoid print unicode"""
import logging
import json
import sys

logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')

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

# Load sample data
with open('sample_data.json', 'r', encoding='utf-8') as f:
    samples = json.load(f)

print('Testing with {} real subtitle items'.format(len(samples)))

refiner = JaTranslator2(translator, batch_size=5)

# Test với 5 dòng đầu
source = [s['jp'] for s in samples[:5]]
trans = [s['vi'] for s in samples[:5]]

print('Source ({} lines)'.format(len(source)))
print('Trans ({} lines)'.format(len(trans)))

try:
    result = refiner.refine(source, trans)
    
    # Write result to file
    with open('test_refine_result.json', 'w', encoding='utf-8') as f:
        json.dump({
            'source': source,
            'original': trans,
            'result': result
        }, f, ensure_ascii=False, indent=2)
    print('Result written to test_refine_result.json')
    
    # Check if result is different from original
    changed = sum(1 for o, r in zip(trans, result) if o.strip() != r.strip())
    print('Lines changed: {}/{}'.format(changed, len(result)))
    
except Exception as e:
    print('Error: {}'.format(e))
    import traceback
    traceback.print_exc()

print('Done')
