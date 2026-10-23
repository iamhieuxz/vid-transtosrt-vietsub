"""Verify extract_audio injects volume filter only when audio_volume != 1.0."""
import os
os.environ['HF_HUB_OFFLINE'] = '1'

import sys
sys.path.insert(0, '.')

import yaml
from unittest.mock import patch, MagicMock


with open('config.yaml', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)


def build_and_capture_cmd(volume_value):
    """Build ffmpeg cmd với audio_volume=volume_value, return argv list."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured['cmd'] = cmd
        # Return fake CompletedProcess
        return MagicMock(returncode=0)

    # Mock subprocess.run để bắt cmd
    with patch('core.transcriber.subprocess.run', side_effect=fake_run):
        # Mock WhisperModel để không load
        with patch('core.transcriber.WhisperModel') as mock_wm:
            mock_wm.return_value = MagicMock()

            wcfg = dict(cfg['whisper'])
            wcfg['audio_volume'] = volume_value

            from core.transcriber import WhisperTranscriber
            t = WhisperTranscriber(wcfg)
            t.extract_audio('fake_video.mp4', 'fake_out.wav')

    return captured.get('cmd', [])


print('=== Test: audio_volume=1.0 (default) -> NO -af filter ===')
cmd1 = build_and_capture_cmd(1.0)
has_af_1 = '-af' in cmd1
print(f'  cmd: {cmd1}')
print(f'  has -af: {has_af_1} (expect False)')

print()
print('=== Test: audio_volume=1.5 -> -af volume=1.5 ===')
cmd2 = build_and_capture_cmd(1.5)
has_af_15 = '-af' in cmd2
volume_arg_15 = cmd2[cmd2.index('-af') + 1] if has_af_15 else None
print(f'  cmd: {cmd2}')
print(f'  -af value: {volume_arg_15!r} (expect "volume=1.5")')

print()
print('=== Test: audio_volume=2.0 -> -af volume=2.0 ===')
cmd3 = build_and_capture_cmd(2.0)
has_af_20 = '-af' in cmd3
volume_arg_20 = cmd3[cmd3.index('-af') + 1] if has_af_20 else None
print(f'  -af value: {volume_arg_20!r} (expect "volume=2.0")')

print()
ok_default = not has_af_1
ok_15 = has_af_15 and volume_arg_15 == 'volume=1.5'
ok_20 = has_af_20 and volume_arg_20 == 'volume=2.0'

print()
print(f'Default (1.0) no -af: {ok_default}')
print(f'1.5 -> volume=1.5: {ok_15}')
print(f'2.0 -> volume=2.0: {ok_20}')
sys.exit(0 if (ok_default and ok_15 and ok_20) else 1)