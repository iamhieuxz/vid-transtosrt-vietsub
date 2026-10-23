import os
import sys
import re
import site
import subprocess
import logging
import tempfile
from tqdm import tqdm

# Force UTF-8 stdout/stderr (Windows console mac dinh cp1252 -> charmap crash khi
# in tieng Viet co dau \u1eb5 v.v. trong log/print)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, OSError):
    pass

# Fix cublas64_12.dll not found: prepend NVIDIA DLL paths to PATH before loading
# faster-whisper. Tìm trong TẤT CẢ site-packages (kể cả user global) thay vì
# hardcode venv path (project mới có thể dùng global pip).
import site as _site

_nvidia_dll_paths = set()
for _sp in _site.getsitepackages() + [_site.getusersitepackages()]:
    if not _sp:
        continue
    for _sub in ("cublas", "cuda_nvrtc", "cudnn", "cuda_runtime"):
        _p = os.path.join(_sp, "nvidia", _sub, "bin")
        if os.path.exists(_p):
            _nvidia_dll_paths.add(_p)

for _p in _nvidia_dll_paths:
    if _p not in os.environ.get('PATH', ''):
        os.environ['PATH'] = _p + os.pathsep + os.environ.get('PATH', '')
    try:
        os.add_dll_directory(_p)
    except (AttributeError, OSError):
        pass  # không phải Windows hoặc path không tồn tại

if _nvidia_dll_paths:
    logger_init = logging.getLogger(__name__)
    logger_init.debug(f"Prepended NVIDIA DLL paths: {_nvidia_dll_paths}")

# Suppress huggingface_hub HTTP request spam (rất nhiều log khi download)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

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
        self.speech_pad_ms = config.get('speech_pad_ms', 300)
        # Cue vua 6s (chuẩn phụ đề). Segment Whisper dài hơn sẽ được tách
        # bằng word timestamps; rỗng thì fallback về 1 cue gốc.
        self.max_cue_ms = config.get('max_cue_ms', 6000)
        # Cờ cho phép auto-fallback về CPU khi GPU load fail (OOM / thiếu DLL).
        # Mặc định True vì GPU 4GB không chạy nổi large-v3.
        self.allow_cpu_fallback = config.get('allow_cpu_fallback', True)

        logger.info(f"[TRANSCRIBER] Loading Whisper model: {self.model_size} on {self.device} ({self.compute_type})")

        # Check cache trước khi load để cảnh báo user nếu cần download
        cache_info = self._check_model_cache(self.model_size)
        if cache_info['cached']:
            logger.info(
                f"[TRANSCRIBER] Model '{self.model_size}' đã có trong cache "
                f"({cache_info['total_mb']}MB). Bỏ qua download."
            )
        else:
            logger.warning(
                f"[TRANSCRIBER] Model '{self.model_size}' CHƯA có trong cache "
                f"({cache_info['total_mb']}MB hiện có - thiếu model.bin). "
                f"Sẽ tải về từ HuggingFace Hub (có thể mất vài phút tùy tốc độ mạng)."
            )
            print(
                f"\n[DOWNLOAD] Model '{self.model_size}' chưa có sẵn.\n"
                f"         Đang tải từ HuggingFace Hub...\n"
                f"         Nếu tốc độ chậm, hãy thử:\n"
                f"           1) Đợi cho xong (model ~800MB cho turbo, ~3GB cho large-v3)\n"
                f"           2) Hoặc Ctrl+C và chạy: HF_ENDPOINT=https://hf-mirror.com python main.py\n",
                flush=True
            )

        self.model = self._load_model_with_fallback()

    def _detect_vram_mb(self):
        """Trả về VRAM MB. Trả None nếu không đọc được."""
        try:
            import subprocess
            out = subprocess.run(
                ['nvidia-smi', '--query-gpu=memory.total', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5
            )
            if out.returncode == 0 and out.stdout.strip():
                return int(out.stdout.strip().split('\n')[0])
        except (FileNotFoundError, Exception):
            pass
        return None

    @staticmethod
    def _estimate_min_vram_mb(model_size: str) -> int:
        """VRAM tối thiểu cho từng model size (ước lượng)."""
        table = {
            'tiny': 400, 'tiny.en': 400,
            'base': 500, 'base.en': 500,
            'small': 1000, 'small.en': 1000,
            'medium': 2500, 'medium.en': 2500,
            'large-v1': 4500, 'large-v2': 4500, 'large-v3': 4500,
            'large-v3-turbo': 3500, 'distil-large-v3': 3500,
        }
        return table.get(model_size, 4500)

    @staticmethod
    def _check_model_cache(model_size: str) -> dict:
        """Kiểm tra xem model đã có trong HF cache chưa.
        Trả về dict với keys: cached, total_mb, has_model_bin, snapshot_path
        """
        from pathlib import Path
        import os as _os
        # Repo mapping (faster-whisper dùng các repo này trên HF Hub)
        repo_map = {
            'tiny': 'Systran/faster-whisper-tiny',
            'tiny.en': 'Systran/faster-whisper-tiny.en',
            'base': 'Systran/faster-whisper-base',
            'base.en': 'Systran/faster-whisper-base.en',
            'small': 'Systran/faster-whisper-small',
            'small.en': 'Systran/faster-whisper-small.en',
            'medium': 'Systran/faster-whisper-medium',
            'medium.en': 'Systran/faster-whisper-medium.en',
            'large-v1': 'Systran/faster-whisper-large-v1',
            'large-v2': 'Systran/faster-whisper-large-v2',
            'large-v3': 'Systran/faster-whisper-large-v3',
            'large-v3-turbo': 'mobiuslabsgmbh/faster-whisper-large-v3-turbo',
            'distil-large-v3': 'Systran/faster-distil-whisper-large-v3',
        }
        repo_id = repo_map.get(model_size, f'Systran/faster-whisper-{model_size}')
        # Tên folder trong cache: models--{org}--{name} với '/' thay bằng '--'
        folder_name = 'models--' + repo_id.replace('/', '--')
        hub_cache = Path(_os.path.expanduser('~')) / '.cache' / 'huggingface' / 'hub' / folder_name
        result = {'cached': False, 'total_mb': 0.0, 'has_model_bin': False, 'snapshot_path': None}
        if not hub_cache.exists():
            return result
        blobs_dir = hub_cache / 'blobs'
        snapshots_dir = hub_cache / 'snapshots'
        if blobs_dir.exists():
            total = 0
            has_bin = False
            for f in blobs_dir.iterdir():
                if f.is_file():
                    total += f.stat().st_size
                    # model.bin thường là file lớn nhất, blob name là sha256 không tên
                    # Check snapshots/refs để xác định file nào là model.bin
            # Check snapshot symlinks
            if snapshots_dir.exists():
                for snap in snapshots_dir.iterdir():
                    if snap.is_dir():
                        mb = snap / 'model.bin'
                        if mb.exists() or mb.is_symlink():
                            # Resolve symlink để check size thật
                            try:
                                real = mb.resolve()
                                if real.exists():
                                    mb_size = real.stat().st_size
                                    if mb_size > 100_000_000:  # > 100MB tức là file thật
                                        has_bin = True
                                        result['snapshot_path'] = str(snap)
                            except (OSError, RuntimeError):
                                pass
                        break
            result['total_mb'] = round(total / (1024 * 1024), 1)
            result['has_model_bin'] = has_bin
            result['cached'] = has_bin
        return result

    def clear_vram(self):
        """Free GPU memory after transcription. Call before loading another model
        (e.g. Ollama) so it gets the full VRAM budget."""
        if hasattr(self, 'model') and self.model is not None:
            del self.model
            self.model = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
                logger.info("[TRANSCRIBER] Cleared VRAM")
        except ImportError:
            pass  # CPU-only env, nothing to clear
        except Exception as e:
            logger.warning(f"[TRANSCRIBER] clear_vram failed (non-blocking): {e}")

    def ensure_model_downloaded(self):
        """Download Whisper model weights if not cached. Idempotent.
        Uses faster-whisper's auto-download (instantiate + warmup).
        """
        info = self._check_model_cache(self.model_size)
        if info['cached']:
            logger.info(f"[TRANSCRIBER] Model '{self.model_size}' cached, skip download")
            return
        logger.warning(
            f"[TRANSCRIBER] Model '{self.model_size}' not cached "
            f"({info['total_mb']}MB hiện có). Tải về từ HuggingFace Hub..."
        )
        console.print(
            f"\n[DOWNLOAD] Model '{self.model_size}' chưa có sẵn.\n"
            f"         Đang tải từ HuggingFace Hub (lần đầu mất vài phút)...\n",
            flush=True
        )
        logger.info(f"[DOWNLOAD] Whisper model '{self.model_size}' downloading...")
        # faster-whisper downloads weights on first inference call.
        # Pre-download by running a 1s dummy transcribe on silence.
        try:
            import numpy as np
            sample_rate = 16000
            silent = np.zeros(sample_rate, dtype=np.float32)
            # Use already-loaded model if any, else load now
            model = self.model if hasattr(self, 'model') and self.model is not None \
                else self._load_model_with_fallback()
            segments, _ = model.transcribe(
                silent, language=self.language, beam_size=1, vad_filter=False,
            )
            list(segments)  # force iteration to trigger download
            logger.info(f"[DOWNLOAD] Whisper model '{self.model_size}' ready")
        except Exception as e:
            raise RuntimeError(
                f"Auto-download Whisper model thất bại: {e}. "
                f"Thử chạy thủ công: HF_ENDPOINT=https://hf-mirror.com python -c "
                f"\"from faster_whisper import WhisperModel; "
                f"WhisperModel('{self.model_size}', device='cpu', compute_type='int8')\""
            ) from e

    def _load_model_with_fallback(self):
        """Load model với auto-fallback:
        1. Thử device cấu hình (cuda/cpu)
        2. Nếu fail vì OOM/DLL thiếu → fallback CPU nếu allow_cpu_fallback
        3. Nếu large model trên GPU < 4GB → cảnh báo user
        """
        try:
            # Cảnh báo VRAM nếu có thể detect
            if self.device == 'cuda' and self.compute_type == 'float16':
                vram = self._detect_vram_mb()
                need = self._estimate_min_vram_mb(self.model_size)
                if vram and vram < need:
                    logger.warning(
                        f"[TRANSCRIBER] GPU VRAM ({vram}MB) < khuyến nghị cho "
                        f"model '{self.model_size}' (cần ~{need}MB). Có thể OOM. "
                        f"Đang auto-fallback về int8 nếu có thể."
                    )
                    # Thử int8 trước (ít memory hơn ~50%)
                    try:
                        return WhisperModel(
                            self.model_size, device='cuda', compute_type='int8',
                        )
                    except Exception as e:
                        logger.warning(f"[TRANSCRIBER] int8 GPU cũng fail: {e}")
                        if not self.allow_cpu_fallback:
                            raise
                        logger.info("[TRANSCRIBER] Fallback sang CPU int8 (chậm hơn ~10-20x nhưng ổn định)")
                        return WhisperModel(
                            self.model_size, device='cpu', compute_type='int8',
                        )
            return WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type
            )
        except Exception as e:
            err_msg = str(e)
            is_dll_error = 'cublas' in err_msg.lower() or 'cudnn' in err_msg.lower() or 'Error code: 126' in err_msg
            is_oom = 'out of memory' in err_msg.lower() or 'cuda' in err_msg.lower() and 'memory' in err_msg.lower()

            if self.allow_cpu_fallback and (is_dll_error or is_oom or 'failed' in err_msg.lower()):
                logger.warning(
                    f"[TRANSCRIBER] GPU load fail ({type(e).__name__}: {e}). "
                    f"Auto-fallback sang CPU int8..."
                )
                return WhisperModel(
                    self.model_size, device='cpu', compute_type='int8',
                )
            raise  # Re-raise nếu không fallback được

    def extract_audio(self, video_path, audio_path=None, start_seconds=None, duration_seconds=None):
        """Extract 16kHz mono WAV, optionally for a bounded video interval."""
        tmp_handle = None
        if audio_path is None:
            tmp_handle = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
            audio_path = tmp_handle.name
            tmp_handle.close()
        cmd = [
            "ffmpeg",
        ]
        if start_seconds is not None:
            cmd.extend(["-ss", str(max(0.0, float(start_seconds)))])
        cmd.extend([
            "-i", video_path,
        ])
        if duration_seconds is not None:
            cmd.extend(["-t", str(max(0.0, float(duration_seconds)))])
        cmd.extend([
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-y",
            audio_path
        ])
        logger.debug(f"[TRANSCRIBER] Running ffmpeg: ffmpeg -i {video_path} ...")
        subprocess.run(cmd, check=True, capture_output=True)
        return audio_path

    def transcribe(self, video_path, output_srt_path, language=None,
                   start_seconds=None, duration_seconds=None):
        """
        Chuyen video thanh file SRT.
        - Trich xuat am thanh.
        - Chay Whisper.
        - Xuat SRT dung dinh dang.
        """
        audio_path = None
        try:
            tmp_handle = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
            audio_path = tmp_handle.name
            tmp_handle.close()
            self.extract_audio(
                video_path, audio_path,
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
            )

            lang = language or self.language
            logger.info(f"[TRANSCRIBER] Starting transcription (language={lang})")

            segments, info = self.model.transcribe(
                audio_path,
                language=lang,
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
                vad_parameters=dict(
                    min_silence_duration_ms=self.min_silence_duration_ms,
                    speech_pad_ms=self.speech_pad_ms,
                ),
                word_timestamps=True,
            )

            segment_list = list(segments)
            total_duration = info.duration or 0

            with open(output_srt_path, 'w', encoding='utf-8') as f:
                idx = 1
                with tqdm(total=total_duration, unit='s', desc="[TRANSCRIBER] Transcribing",
                         bar_format="{l_bar}{bar}| {n:.1f}s/{total}s ({percentage:>3.0f}%)") as pbar:
                    last_end = 0
                    for segment in segment_list:
                        offset = max(0.0, float(start_seconds or 0.0))
                        # Tách segment dài thành nhiều cue dựa trên word timestamps
                        # (fix cue 70s+ từ NSPS-692). shortcut: chỉ áp dụng nếu
                        # Whisper trả về word-level info; rỗng thì fallback 1 cue.
                        sub_cues = self._split_by_words(segment, offset)
                        for cue_start, cue_end, cue_text in sub_cues:
                            if self._is_noise_segment(cue_text):
                                logger.debug(f"[TRANSCRIBER] Skipping noise segment: {cue_text[:50]}")
                                continue
                            f.write(
                                f"{idx}\n{cue_start} --> {cue_end}\n{cue_text}\n\n"
                            )
                            idx += 1
                        pbar.update(segment.end - last_end)
                        last_end = segment.end
            logger.info(f"[TRANSCRIBER] Complete: {output_srt_path} ({idx - 1} segments)")
            return output_srt_path
        finally:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)

    def transcribe_interval(self, video_path, start_seconds, duration_seconds, language=None):
        """Re-transcribe a bounded interval and return absolute-time SRT cues."""
        with tempfile.NamedTemporaryFile(suffix='.srt', delete=False) as handle:
            output_path = handle.name
        try:
            self.transcribe(
                video_path,
                output_path,
                language=language,
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
            )
            import pysrt
            return [
                {
                    'start': str(sub.start),
                    'end': str(sub.end),
                    'text': sub.text,
                }
                for sub in pysrt.open(output_path, encoding='utf-8')
            ]
        finally:
            if os.path.exists(output_path):
                os.remove(output_path)

    def _split_by_words(self, segment, offset: float = 0.0):
        """Chia segment dài thành nhiều cue nhờ word timestamps.

        Whisper đo timestamp theo cả câu, nên một câu ngắn có thể bị gán
        hàng chục giây (cue 70s trong NSPS-692). Word timestamps đo thời lượng
        thật từng từ nên tách cue ra khớp với lời nói.

        Trả về list (start_ts, end_ts, text); nếu không có word timestamps thì
        trả về đúng 1 cue theo timestamp gốc của segment.
        """
        max_ms = self.max_cue_ms
        duration = (segment.end - segment.start) * 1000

        # Segment ngắn hoặc không có word info → giữ nguyên 1 cue
        words = getattr(segment, 'words', None)
        if duration <= max_ms or not words:
            return [(
                self._format_time(segment.start + offset),
                self._format_time(segment.end + offset),
                segment.text.strip(),
            )]

        def _eff_start(w):
            w_len = max(1, len((getattr(w, 'word', '') or '').strip()))
            max_word_dur = max(1.2, min(3.0, w_len * 0.35))
            if (w.end - w.start) > max_word_dur:
                return max(w.start, w.end - max(0.4, w_len * 0.18))
            return w.start

        cues = []
        chunk = []
        for word in words:
            chunk.append(word)
            chunk_ms = (chunk[-1].end - _eff_start(chunk[0])) * 1000
            chunk_text = ''.join(w.word for w in chunk).strip()
            # Cắt khi từ này kết thúc mệnh đề VÀ chunk đã đủ dài, hoặc khi
            # chunk vượt ngưỡng cứng mà không có dấu câu để bám (1.5×) và đủ >= 5 ký tự.
            if (chunk_ms >= max_ms and self._ends_clause(word)) or (
                chunk_ms >= max_ms * 1.5 and len(chunk_text) >= 5
            ):
                cues.append(chunk)
                chunk = []
        if chunk:
            cues.append(chunk)

        result = []
        for group in cues:
            text = ''.join(w.word for w in group).strip()
            if not text:
                continue
            result.append((
                self._format_time(_eff_start(group[0]) + offset),
                self._format_time(group[-1].end + offset),
                text,
            ))
        return result or [(
            self._format_time(segment.start + offset),
            self._format_time(segment.end + offset),
            segment.text.strip(),
        )]

    @staticmethod
    def _ends_clause(word) -> bool:
        """Từ này kết thúc mệnh đề chưa — dùng làm mốc cắt cue."""
        text = (word.word or '').strip()
        return bool(text) and text[-1] in '。？！!?、,.．,.'

    def _format_time(self, seconds):
        """Chuyen giay thanh dinh dang SRT: HH:MM:SS,mmm"""
        total_ms = round(seconds * 1000)
        hrs = total_ms // 3600000
        mins = (total_ms % 3600000) // 60000
        secs = (total_ms % 60000) // 1000
        millis = total_ms % 1000
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

    def _is_noise_segment(self, text: str) -> bool:
        """
        Kiem tra xem segment co phai la noise/rmusic/rác hay khong.
        Filter nang cao hon:
        """
        import re

        if not text or len(text.strip()) < 2:
            return True

        text = text.strip()

        # 1. Chi co ky tu dac biet
        special_only = re.match(r'^[\s\.,!?~\-_\[\]\(\)\/\\]+$', text)
        if special_only:
            return True

        # 2. Chi la so
        if re.match(r'^[\d\s\.,]+$', text):
            return True

        # 3. Tu rác thuong gap (thu tu thap -> check truoc)
        noise_words = {
            # Tieng Anh rác
            'music', '♪', '♫', '♬', '🎵', '🎶',
            'applause', 'laugh', 'laughing', 'cheering',
            'silence', 'quiet', 'noise',
            # Tieng Nhat rác
            'ウホッ', 'ドンドコ', 'パンパカパー',
            # Ky tu don le
            'ア', 'イ', 'ウ', 'エ', 'オ',
            'カ', 'キ', 'ク', 'ケ', 'コ',
            # Ky tu Latin don le (thuong la loi nhan dien)
            'h', 'hm', 'um', 'er',
            'mmm', 'hhh', 'uhh', 'ahh', 'ohh',
            # Symbol rác
            '💓', '💔', '💕', '💖', '💗', '💘', '💙', '💚',
        }
        if text.lower() in noise_words:
            return True

        # 4. Lap lai ky tu (thuong la loi nhan dien)
        # vd: "アアアア" hoặc "eeeee"
        if re.match(r'^(.)\1{3,}$', text):
            return True

        # 5. Lap lai cum 2-3 ky tu nhieu lan (thuong la loi nhan dien)
        # vd: "アアホアアホ" hoặc "yeah yeah yeah"
        repeat_pattern = re.match(r'^(.{2,3})(\s*\1){2,}$', text, re.IGNORECASE)
        if repeat_pattern:
            return True

        # 6. Do dai qua ngan (thuong la noise)
        if len(text) <= 2 and not re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', text):
            # Neu la tieng Nhat/Trung thi 1-2 ky tu co the hop le
            pass
        elif len(text) <= 1:
            return True

        # 7. Ty le ky tu dac biet qua cao
        if len(text) > 0:
            special_count = len(re.findall(r'[\[\]\(\)\/\\~_\-]', text))
            if special_count / len(text) > 0.5:
                return True

        # 8. Tieng Nhat nhung chi la katakana don le (thuong la loi)
        katakana_only = re.match(r'^[\u30A0-\u30FF]+$', text)
        if katakana_only and len(text) <= 3:
            # Mot so katakana don le la noise
            single_katakana = {'ア', 'イ', 'ウ', 'エ', 'オ', 'カ', 'キ', 'ク', 'ケ', 'コ', 
                              'サ', 'シ', 'ス', 'セ', 'ソ', 'タ', 'チ', 'ツ', 'テ', 'ト',
                              'ナ', 'ニ', 'ヌ', 'ネ', 'ノ', 'ハ', 'ヒ', 'フ', 'ヘ', 'ホ',
                              'マ', 'ミ', 'ム', 'メ', 'モ', 'ヤ', 'ユ', 'ヨ', 'ラ', 'リ',
                              'ル', 'レ', 'ロ', 'ワ', 'ヲ', 'ン'}
            if text in single_katakana:
                return True

        # 9. Tieng Viet nhung khong co nguyen am dung (thuong la loi)
        if re.search(r'[\u00C0-\u1EF9]', text):  # co dau tieng Viet
            vowels = re.findall(r'[aeiouàáảãạèéẻẽẹìíỉĩịòóỏõọùúủũụỳýỷỹ]', text, re.IGNORECASE)
            if len(vowels) == 0 and len(text) > 2:
                return True

        return False
