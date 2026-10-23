"""Verify VAD config from config.yaml is read correctly by WhisperTranscriber."""
import os
os.environ['HF_HUB_OFFLINE'] = '1'

import sys
sys.path.insert(0, '.')

import yaml
from unittest.mock import patch, MagicMock


with open('config.yaml', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)

wcfg = cfg['whisper']
print('Config from config.yaml:')
print(f'  vad_filter: {wcfg.get("vad_filter")}')
print(f'  min_silence_duration_ms: {wcfg.get("min_silence_duration_ms")}')
print(f'  speech_pad_ms: {wcfg.get("speech_pad_ms")}')

# Mock WhisperModel để không tải model
with patch('core.transcriber.WhisperModel') as mock_wm:
    mock_wm.return_value = MagicMock()
    from core.transcriber import WhisperTranscriber

    t = WhisperTranscriber(wcfg)

print()
print('Transcriber attributes:')
print(f'  vad_filter: {t.vad_filter}')
print(f'  min_silence_duration_ms: {t.min_silence_duration_ms}')
print(f'  speech_pad_ms: {t.speech_pad_ms}')

# Verify equality
ok = (
    t.vad_filter == wcfg.get('vad_filter')
    and t.min_silence_duration_ms == wcfg.get('min_silence_duration_ms')
    and t.speech_pad_ms == wcfg.get('speech_pad_ms')
)
print()
print(f'Mapping OK: {ok}')
sys.exit(0 if ok else 1)