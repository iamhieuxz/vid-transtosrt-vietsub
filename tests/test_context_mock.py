"""Test _get_context_block voi mock data."""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

import yaml
with open('config.yaml', 'r', encoding='utf-8-sig') as f:
    config = yaml.safe_load(f)

from core.pipeline import TranslationPipeline
from core.database import Database

# Tao DB
db_path = config.get('database', {}).get('path', 'data/translator.db')
os.makedirs(os.path.dirname(db_path), exist_ok=True)
if os.path.exists(db_path):
    os.remove(db_path)
db = Database(db_path)

# Gan DB vao config de pipeline dung chung
config.setdefault('database', {})['path'] = db_path
pipeline = TranslationPipeline(config)
pipeline.db = db  # Dam bao cung instance

# Tao project test
project_id = db.create_project(
    name='test_av1',
    input_srt='dummy.srt',
    output_srt='dummy_out.srt',
    source_lang='Japanese',
    target_lang='Vietnamese',
)

# Mock subtitles
db.save_subtitle_items(project_id, [
    {'index': i, 'start': '00:00:01,000', 'end': '00:00:02,000',
     'text': f'Dong test {i}'}
    for i in range(1, 50)
])

# Mock context windows (2 cai)
db.save_window_context(
    project_id, 0, 0, 17,
    speakers_json=json.dumps(['Shota', 'Ayune', 'Shinoda'], ensure_ascii=False),
    pronouns_map=json.dumps({
        'Shota->Ayune': {'self': 'em', 'other': 'chi'},
        'Ayune->Shota': {'self': 'chi', 'other': 'em'},
        'Shinoda->Shota': {'self': 'chu', 'other': 'chau'},
    }, ensure_ascii=False),
    tone='casual',
    setting='nha rieng - bep an',
    summary='Shota moi di hoc ve, dang an toi cung Ayune (me ke) va Shinoda (bac re).',
    status='completed',
)
db.save_window_context(
    project_id, 1, 18, 35,
    speakers_json=json.dumps(['Shota', 'Shinoda'], ensure_ascii=False),
    pronouns_map=json.dumps({
        'Shinoda->Shota': {'self': 'chu', 'other': 'chau'},
        'Shota->Shinoda': {'self': 'chau', 'other': 'chu'},
    }, ensure_ascii=False),
    tone='formal',
    setting='phong rieng Shinoda',
    summary='Shinoda de doa Shota ve tien hoc phi va viec di lam them.',
    status='completed',
)

print('Mock data OK\n')

# Test query context cho translation-window 0-5 (thuoc context-window 0)
print('=== Test 1: Translation-window 0-5 (thuoc context-window 0) ===')
result = pipeline._get_context_block(project_id, 0, 5)
print(result)
print()

# Test query context cho translation-window 16-22 (span 0 va 1)
print('=== Test 2: Translation-window 16-22 (SPAN 2 context-windows) ===')
result = pipeline._get_context_block(project_id, 16, 22)
print(result)
print()

# Test query context cho translation-window 20-30 (thuoc context-window 1)
print('=== Test 3: Translation-window 20-30 (thuoc context-window 1) ===')
result = pipeline._get_context_block(project_id, 20, 30)
print(result)
