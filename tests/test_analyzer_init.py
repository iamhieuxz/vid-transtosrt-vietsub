"""Debug JaContextAnalyzer init."""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.DEBUG)

import yaml
with open('config.yaml', 'r', encoding='utf-8-sig') as f:
    config = yaml.safe_load(f)

print('Config ja_context:', config.get('ja_context'))
print('Config project source_lang:', config.get('project', {}).get('source_lang'))
print()

try:
    from core.ja_context_analyzer import JaContextAnalyzer
    from core.translator import TranslatorService
    print('Imports OK')

    # Init translator
    model_cfg = config.get('model', {})
    translator = TranslatorService(
        model_name=model_cfg.get('name', 'qwen3-abliterated:8b'),
        ollama_url=model_cfg.get('ollama_url', 'http://localhost:11434'),
        temperature=model_cfg.get('temperature', 0.1),
        repeat_penalty=model_cfg.get('repeat_penalty', 1.2),
        num_ctx=model_cfg.get('num_ctx', 4096),
        num_predict=model_cfg.get('num_predict', 1024),
    )
    print('Translator OK')

    # Init analyzer
    analyzer = JaContextAnalyzer(translator, context_window_size=18)
    print('JaContextAnalyzer OK')
    print('  context_window_size:', analyzer.context_window_size)
except Exception as e:
    print('ERROR:', e)
    import traceback
    traceback.print_exc()
