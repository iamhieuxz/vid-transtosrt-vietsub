"""audio_filter / audio_volume / prompt_names của WhisperTranscriber (không load model thật)."""
import os
os.environ['HF_HUB_OFFLINE'] = '1'

from unittest.mock import MagicMock, patch

from core.transcriber import WhisperTranscriber


def _make(cfg):
    with patch('core.transcriber.WhisperModel', return_value=MagicMock()):
        return WhisperTranscriber(cfg)


def _ffmpeg_cmd(cfg):
    t = _make(cfg)
    with patch('core.transcriber.subprocess.run') as run:
        t.extract_audio('v.mp4', 'o.wav')
    return run.call_args[0][0]


def test_default_has_no_af():
    assert '-af' not in _ffmpeg_cmd({})


def test_filter_and_volume_are_chained():
    cmd = _ffmpeg_cmd({'audio_filter': 'highpass=f=80', 'audio_volume': 1.5})
    assert cmd[cmd.index('-af') + 1] == 'highpass=f=80,volume=1.5'


def test_af_comes_before_output_codec():
    cmd = _ffmpeg_cmd({'audio_filter': 'highpass=f=80'})
    assert cmd.index('-af') < cmd.index('-acodec')


def test_prompt_names_reach_whisper(tmp_path):
    t = _make({'language': 'ja', 'prompt_names': ['マサト', 'メイ']})
    seg = MagicMock(end=1.0, start=0.0, words=None)
    seg.text = 'x'
    captured = {}

    # Hàm thật có tham số tên để inspect.signature thấy hotwords/initial_prompt
    def fake_transcribe(audio, hotwords=None, initial_prompt=None, **kw):
        captured.update(hotwords=hotwords, initial_prompt=initial_prompt)
        return [seg], MagicMock(duration=1.0)

    t.model.transcribe = fake_transcribe
    out = tmp_path / 'o.srt'
    with patch.object(t, 'extract_audio'):
        t.transcribe('v.mp4', str(out), language='ja')
    assert 'マサト' in captured['initial_prompt'] and 'メイ' in captured['initial_prompt']
    assert 'マサト' in captured['hotwords']
