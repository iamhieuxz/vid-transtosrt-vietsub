import os
import subprocess
import logging
import tempfile
from tqdm import tqdm
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class WhisperTranscriber:
    def __init__(self, config: dict):
        self.model_size = config.get('model_size', 'large-v3-turbo')
        self.device = config.get('device', 'cuda')
        self.compute_type = config.get('compute_type', 'float16')
        self.language = config.get('language', None)
        self.beam_size = config.get('beam_size', 5)
        self.vad_filter = config.get('vad_filter', True)
        self.min_silence_duration_ms = config.get('min_silence_duration_ms', 500)

        logger.info(f"[TRANSCRIBER] Loading Whisper model: {self.model_size} on {self.device} ({self.compute_type})")
        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type
        )

    def extract_audio(self, video_path, audio_path=None):
        """Trich xuat audio 16kHz mono WAV tu video bang ffmpeg."""
        if audio_path is None:
            audio_path = tempfile.mktemp(suffix='.wav')
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-y",
            audio_path
        ]
        logger.debug(f"[TRANSCRIBER] Running ffmpeg: ffmpeg -i {video_path} ...")
        subprocess.run(cmd, check=True, capture_output=True)
        return audio_path

    def transcribe(self, video_path, output_srt_path, language=None):
        """
        Chuyen video thanh file SRT.
        - Trich xuat am thanh.
        - Chay Whisper.
        - Xuat SRT dung dinh dang.
        """
        audio_path = None
        try:
            audio_path = tempfile.mktemp(suffix='.wav')
            self.extract_audio(video_path, audio_path)

            lang = language or self.language
            logger.info(f"[TRANSCRIBER] Starting transcription (language={lang})")

            segments, info = self.model.transcribe(
                audio_path,
                language=lang,
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
                vad_parameters=dict(
                    min_silence_duration_ms=self.min_silence_duration_ms,
                )
            )

            segment_list = list(segments)
            total_duration = info.duration or 0

            with open(output_srt_path, 'w', encoding='utf-8') as f:
                idx = 1
                with tqdm(total=total_duration, unit='s', desc="[TRANSCRIBER] Transcribing", 
                         bar_format="{l_bar}{bar}| {n:.1f}s/{total}s ({percentage:>3.0f}%)") as pbar:
                    last_end = 0
                    for segment in segment_list:
                        start = self._format_time(segment.start)
                        end = self._format_time(segment.end)
                        text = segment.text.strip()
                        f.write(f"{idx}\n{start} --> {end}\n{text}\n\n")
                        idx += 1
                        pbar.update(segment.end - last_end)
                        last_end = segment.end

            logger.info(f"[TRANSCRIBER] Complete: {output_srt_path} ({idx - 1} segments)")
            return output_srt_path
        finally:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)

    def _format_time(self, seconds):
        """Chuyen giay thanh dinh dang SRT: HH:MM:SS,mmm"""
        hrs = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"
