"""Debug với logging."""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

import yaml
with open('config.yaml', 'r', encoding='utf-8-sig') as f:
    config = yaml.safe_load(f)

from core.pipeline import TranslationPipeline

# Tao pipeline va xem log
pipeline = TranslationPipeline(config)
print()
print('ja_2tier_enabled:', pipeline.ja_2tier_enabled)
print('ja_context_analyzer:', pipeline.ja_context_analyzer)
print('is_japanese_source:', pipeline._is_japanese_source)
