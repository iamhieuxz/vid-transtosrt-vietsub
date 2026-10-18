"""Debug với logging chi tiết."""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.WARNING, format='%(name)s - %(levelname)s: %(message)s')

import yaml
with open('config.yaml', 'r', encoding='utf-8-sig') as f:
    config = yaml.safe_load(f)

# Thử import trực tiếp
try:
    from core.ja_context_analyzer import JaContextAnalyzer
    print('Import JaContextAnalyzer OK')
except Exception as e:
    print('Import failed:', e)
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    from core.translator import TranslatorService
    print('Import TranslatorService OK')
except Exception as e:
    print('Import failed:', e)
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Thử tạo analyzer
try:
    model_cfg = config.get('model', {})
    translator = TranslatorService(
        model_name=model_cfg.get('name', 'qwen3-abliterated:8b'),
        ollama_url=model_cfg.get('ollama_url', 'http://localhost:11434'),
        temperature=model_cfg.get('temperature', 0.1),
    )
    print('Translator created')

    analyzer = JaContextAnalyzer(translator, context_window_size=18)
    print('Analyzer created:', analyzer)
except Exception as e:
    print('ERROR creating analyzer:')
    import traceback
    traceback.print_exc()
