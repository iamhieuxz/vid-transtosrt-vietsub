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

# --- P1 (audit NSFS-504): chống hallucination của Whisper trên khoảng không thoại ---
# Đo thực tế: khi hạ ngưỡng VAD / tăng gain, Whisper sinh đúng những câu dưới đây ở
# đoạn chỉ có nhạc hoặc tiếng thở. Đây là artefact của dữ liệu train (phụ đề), không
# phải lời thoại. VAD 0.35 hiện tại đã chặn tốt; guard này để không rác lọt nếu cấu
# hình thay đổi hoặc VAD bỏ sót.
# (a) Boilerplate — KHÔNG BAO GIỜ là thoại trong phim, lọc bất kể thời lượng.
_WHISPER_BOILERPLATE_RE = re.compile(
    r'(ご視聴(いただき)?ありがとうございました|'
    r'最後までご視聴|ご覧いただきありがとうございました|'
    r'チャンネル登録|高評価|'
    r'字幕(提供|制作|は|を)?|'
    r'動画を?ご視聴|'
    r'Thank you for watching|Subtitles by)',
    re.IGNORECASE,
)
# (b) Câu CÓ THỂ là thoại thật -> chỉ coi là hallucination khi thời lượng vô lý
#     so với độ dài chữ (vd 15 giây chỉ để nói "おやすみなさい").
_WHISPER_AMBIGUOUS = frozenset({
    'おやすみなさい', 'おやすみ', 'お疲れ様でした', 'お疲れさまでした',
    'お疲れ様です', 'ありがとうございました', 'はい', 'いいえ',
})
_HALLUCINATION_MIN_SECONDS = 6.0
_HALLUCINATION_MAX_CHARS = 15


class WhisperTranscriber:
    def __init__(self, config: dict):
        self.model_size = config.get('model_size', 'large-v3-turbo')
        self.device = config.get('device', 'cuda')
        self.compute_type = config.get('compute_type', 'float16')
        self.language = config.get('language', None)
        self.beam_size = config.get('beam_size', 5)
        self.vad_filter = config.get('vad_filter', True)
        self.vad_threshold = config.get('vad_threshold', 0.35)
        self.min_silence_duration_ms = config.get('min_silence_duration_ms', 500)
        self.speech_pad_ms = config.get('speech_pad_ms', 450)
        self.max_speech_duration_s = config.get('max_speech_duration_s', 15)
        self.no_speech_threshold = config.get('no_speech_threshold', 0.85)
        self.log_prob_threshold = config.get('log_prob_threshold', -1.35)
        self.compression_ratio_threshold = config.get('compression_ratio_threshold', 2.8)
        self.condition_on_previous_text = config.get('condition_on_previous_text', False)
        self.initial_prompt = config.get('initial_prompt', None)
        self.hotwords = config.get('hotwords', None)
        self.adult_boost = bool(config.get('adult_boost', False))
        # Tiền xử lý âm thanh: chuỗi ffmpeg -af (vd 'highpass=f=80,dynaudnorm=f=150:g=15')
        # + hệ số volume. Mặc định rỗng/1.0 = không lọc, giữ hành vi cũ.
        # Tên nhân vật JA của phim đang chạy, nối vào initial_prompt để Whisper bớt nghe sai tên.
        self.prompt_names = [n for n in (config.get('prompt_names') or []) if n]
        self.audio_filter = (config.get('audio_filter') or '').strip()
        self.audio_volume = float(config.get('audio_volume', 1.0) or 1.0)
        # Cue vua 6s (chuẩn phụ đề). Segment Whisper dài hơn sẽ được tách
        # bằng word timestamps; rỗng thì fallback về 1 cue gốc.
        self.max_cue_ms = config.get('max_cue_ms', 6000)
        # Ngưỡng cứng: KHÔNG cue nào dài quá 12s, kể cả khi Whisper gán sai
        # timestamp qua cảnh im lặng (JUR-412 từng có cue 51 phút).
        self.hard_cue_cap_ms = config.get('hard_cue_cap_ms', 12000)
        # Khoảng lặng (giây) bắt buộc ngắt cue trước khi nối từ kế tiếp.
        self.hard_gap_split_s = config.get('hard_gap_split_s', 1.2)
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
        (e.g. Ollama / llama-server) so it gets the full VRAM budget."""
        if hasattr(self, 'model') and self.model is not None:
            del self.model
            self.model = None
        try:
            import gc
            gc.collect()
        except Exception:
            pass
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
        print(
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
        af_chain = [f for f in (self.audio_filter,) if f]
        if self.audio_volume != 1.0:
            af_chain.append(f"volume={self.audio_volume}")
        cmd.append("-vn")
        if af_chain:
            cmd.extend(["-af", ",".join(af_chain)])
        cmd.extend([
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
            logger.info(f"[TRANSCRIBER] Tách âm thanh từ video (ffmpeg): {video_path}")
            self.extract_audio(
                video_path, audio_path,
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
            )

            if getattr(self, 'model', None) is None:
                self.model = self._load_model_with_fallback()

            lang = language or self.language
            logger.info(f"[TRANSCRIBER] Starting Whisper transcription (language={lang})")

            vad_params = dict(
                threshold=self.vad_threshold,
                min_silence_duration_ms=self.min_silence_duration_ms,
                speech_pad_ms=self.speech_pad_ms,
                max_speech_duration_s=self.max_speech_duration_s,
            )
            transcribe_kwargs = dict(
                language=lang,
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
                vad_parameters=vad_params,
                word_timestamps=True,
                no_speech_threshold=self.no_speech_threshold,
                log_prob_threshold=self.log_prob_threshold,
                compression_ratio_threshold=self.compression_ratio_threshold,
                condition_on_previous_text=self.condition_on_previous_text,
            )
            # Group A3 + Uncen Adult Boost: Truyền initial_prompt + hotwords tiếng Nhật nhạy cảm
            # để Whisper nhận diện chuẩn thoại rên/thì thầm và tránh lỗi đồng âm (中出し vs 仲出し, 今日中に vs 教授に)
            if (lang or '').lower() in ('ja', 'japanese'):
                try:
                    import inspect
                    sig = inspect.signature(self.model.transcribe)
                    # Bo tu 18+ chi bat khi adult_boost (main.py set theo translation.mode=uncen):
                    # gợi ý mạnh làm Whisper bịa từ đó ở đoạn im lặng/nhạc nền của phim thường.
                    adult = getattr(self, 'adult_boost', False)
                    default_hotwords = getattr(self, 'hotwords', None) or (
                        "中出し 気持ちいい 気持ち良い 気持ちよく イク イッちゃう おまんこ まんこ おちんちん ちんぽ "
                        "射精 フェラ 舐めて 奥まで 濡れてる 感じてる 恥ずかしい 変態 エッチ 性処理 内緒 鳴き声 発散 "
                        "お母さん 娘 彼氏 彼女 旦那 主人 奥さん 夫婦 セフレ 不倫 浮気 婚約 就活 就活生 久しぶり 大きい声 玄関 今日中に 激務"
                        if adult else ""
                    )
                    default_prompt = getattr(self, 'initial_prompt', None) or (
                        "気持ちいい、気持ちよく、イク、イッちゃう、だめ、やめて、おまんこ、まんこ、おちんちん、ちんぽ、"
                        "中出し、射精、フェラ、舐めて、奥まで、激しい、濡れてる、感じてる、恥ずかしい、"
                        "変態、エッチ、性処理、内緒、鳴き声、発散、お母さん、娘、旦那、主人、奥さん、夫婦、不倫、浮気、就活、就活生、久しぶり、大きい声、玄関"
                        if adult else ""
                    )
                    if self.prompt_names:
                        default_prompt = "、".join([p for p in [default_prompt] if p] + self.prompt_names)
                        default_hotwords = " ".join([h for h in [default_hotwords] if h] + self.prompt_names)
                    if 'hotwords' in sig.parameters and default_hotwords:
                        transcribe_kwargs['hotwords'] = default_hotwords
                    if 'initial_prompt' in sig.parameters and default_prompt:
                        transcribe_kwargs['initial_prompt'] = default_prompt
                except Exception:
                    pass

            segments, info = self.model.transcribe(
                audio_path,
                **transcribe_kwargs,
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
                        # P1: segment kéo dài bất thường mà chữ rất ngắn = hallucination
                        # trên khoảng lặng (vd 15s chỉ nói "おやすみなさい"). Bỏ trước khi
                        # tách cue để không ghi rác vào SRT.
                        if self._is_hallucination_segment(
                                segment.text, segment.end - segment.start):
                            logger.debug(
                                f"[TRANSCRIBER] Dropped hallucination segment "
                                f"({segment.end - segment.start:.1f}s): {segment.text.strip()[:50]!r}"
                            )
                            pbar.update(segment.end - last_end)
                            last_end = segment.end
                            continue
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
        hard_cap_ms = getattr(self, 'hard_cue_cap_ms', 12000)
        duration = (segment.end - segment.start) * 1000

        # Segment ngắn hoặc không có word info → giữ nguyên 1 cue
        words = getattr(segment, 'words', None)
        if duration <= max_ms or not words:
            start = segment.start
            end = min(segment.end, start + hard_cap_ms / 1000)
            if not words and segment.end > end:
                # Không có word timestamps: chia text theo dấu câu và phân bổ thời
                # gian theo độ dài để KHÔNG mất chữ mà vẫn cap 12s/cue.
                return self._split_by_proportion(
                    segment.text.strip(), start, segment.end, hard_cap_ms, offset,
                )
            return [(
                self._format_time(start + offset),
                self._format_time(end + offset),
                segment.text.strip(),
            )]

        # Chuẩn hóa mốc thời gian từng từ TRƯỚC khi ghép cue.
        # Whisper hay kéo `end` của 1 từ vượt qua cảnh im lặng dài (mất hàng chục
        # phút) → cue dài 51 phút (JUR-412) và từ đầu câu sau bị dồn vào đuôi
        # cue trước ('確' | '実に', '離婚' | 'の', 'ち' | 'んぽ').
        norm_words = []
        n_words = len(words)
        for idx_w, w in enumerate(words):
            w_start = float(w.start)
            w_end = float(w.end)
            if idx_w + 1 < n_words:
                w_end = min(w_end, float(words[idx_w + 1].start))
            est = self._estimate_speech_s(getattr(w, 'word', '') or '')
            if w_end - w_start > max(4.0, est * 3):
                # Từ bị kéo giãn qua im lặng → giới hạn theo độ dài lời nói thực tế.
                w_end = w_start + est
            norm_words.append((w, w_start, w_end))

        cues = []
        chunk = []
        # Khoảng lặng tối thiểu (giây) buộc phải ngắt cue TRƯỚC khi thêm từ kế tiếp.
        hard_gap_s = float(getattr(self, 'hard_gap_split_s', 1.2) or 1.2)
        for idx_w, (word, w_start, w_end) in enumerate(norm_words):
            if chunk:
                gap_before = w_start - chunk[-1][2]
                if gap_before >= hard_gap_s:
                    cues.append(chunk)
                    chunk = []
            chunk.append((word, w_start, w_end))
            chunk_ms = (chunk[-1][2] - chunk[0][1]) * 1000
            chunk_text = ''.join(x[0].word for x in chunk).strip()
            next_word = norm_words[idx_w + 1][0] if idx_w + 1 < n_words else None

            # Tránh cắt ngay giữa 1 từ ghép tiếng Nhật (khi 2 mảnh sát nhau < 0.08s và không kết thúc bằng trợ từ/dấu câu)
            w_str = (word.word or '').strip()
            gap_to_next = (
                norm_words[idx_w + 1][1] - w_end if idx_w + 1 < n_words else 999.0
            )
            is_natural_break = (
                self._ends_clause(word)
                or gap_to_next >= 0.12
                or (bool(w_str) and w_str[-1] in 'はがをにへでとものやねよかからけどし、。！？!?…')
            )

            # Cắt khi:
            # 1. Đạt max_ms và có dấu kết mệnh đề
            # 2. Đạt 1.25x max_ms (>= 5 ký tự) tại điểm ngắt tự nhiên (trợ từ / khoảng nghỉ >= 0.12s)
            # 3. Đạt ngưỡng cứng 12s — KHÔNG cue nào được dài quá 12s
            if (
                (chunk_ms >= max_ms and self._ends_clause(word))
                or (chunk_ms >= max_ms * 1.25 and len(chunk_text) >= 5 and is_natural_break)
                or chunk_ms >= hard_cap_ms
            ):
                cues.append(chunk)
                chunk = []
        if chunk:
            cues.append(chunk)

        result = []
        for group in cues:
            text = ''.join(x[0].word for x in group).strip()
            if not text:
                continue
            start_s = group[0][1]
            end_s = min(group[-1][2], start_s + hard_cap_ms / 1000)
            result.append((
                self._format_time(start_s + offset),
                self._format_time(end_s + offset),
                text,
            ))
        return result or [(
            self._format_time(segment.start + offset),
            self._format_time(min(segment.end, segment.start + hard_cap_ms / 1000) + offset),
            segment.text.strip(),
        )]

    @staticmethod
    def _estimate_speech_s(text: str) -> float:
        """Ước lượng thời lượng lời nói (giây) từ độ dài text tiếng Nhật.

        shortcut: ~6 ký tự/giây cho hội thoại Nhật; sai số chấp nhận được vì
        chỉ dùng để chặn cue trôi qua cảnh im lặng, cần thay bằng forced
        alignment khi đòi độ chính xác tuyệt đối.
        """
        clean = (text or '').strip()
        if not clean:
            return 0.5
        return len(clean) / 6.0 + 0.4

    def _split_by_proportion(self, text, start, end, hard_cap_ms, offset):
        """Chia text (không có word timestamps) theo dấu câu + phân bổ thời gian.

        Không có word timestamps thì không biết lời nói nằm ở đâu trong segment,
        nên phân bổ theo tỉ lệ độ dài câu rồi cap mỗi cue ở `hard_cap_ms` —
        đảm bảo KHÔNG cue nào trôi qua cảnh im lặng (JUR-412 từng có 51 phút).
        """
        text = (text or '').strip()
        if not text:
            return []
        chunks = [c.strip() for c in re.split(r'(?<=[。！？!?…])', text) if c.strip()]
        if not chunks:
            chunks = [text]
        total_len = sum(len(c) for c in chunks) or 1
        window = max(0.1, end - start)
        cap_s = hard_cap_ms / 1000.0
        out = []
        cur = start
        for c in chunks:
            dur = min(window * (len(c) / total_len), cap_s)
            out.append((
                self._format_time(cur + offset),
                self._format_time(cur + dur + offset),
                c,
            ))
            cur += dur
        return out

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

    def _is_hallucination_segment(self, text: str, duration: float) -> bool:
        """True nếu segment là hallucination của Whisper trên khoảng không thoại.

        Chỉ áp dụng cho các câu MƠ HỒ (có thể là thoại thật như "おやすみなさい")
        và chỉ khi thời lượng vô lý so với lượng chữ — 15 giây cho 7 ký tự.
        Boilerplate phụ đề đã bị `_is_noise_segment` lọc bất kể thời lượng.
        """
        t = (text or '').strip()
        if not t:
            return False
        if _WHISPER_BOILERPLATE_RE.search(t):
            return True
        if duration >= _HALLUCINATION_MIN_SECONDS and len(t) <= _HALLUCINATION_MAX_CHARS:
            # Bỏ dấu câu/space khi so khớp để bắt "おやすみなさい。" và "お や す み"
            norm = re.sub(r'[\s。、！？!?…]+', '', t)
            if norm in _WHISPER_AMBIGUOUS:
                return True
        return False

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

        # 3b. Boilerplate phụ đề/YouTube — không bao giờ là thoại trong phim.
        # Đo trên NSFS-504 (audit P1): Whisper sinh "ご視聴ありがとうございました"
        # ở đoạn chỉ có nhạc khi hạ ngưỡng VAD.
        if _WHISPER_BOILERPLATE_RE.search(text):
            return True

        # 4. Lap lai ky tu (thuong la loi nhan dien, vd: "eeeee" hoac "-----")
        # NHUNG giu lai tieng keu cam xuc tieng Nhat (vd: "ああああ", "んんんん")
        if re.match(r'^([^\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF])\1{3,}$', text):
            return True

        # 5. Lap lai cum 2-3 ky tu nhieu lan:
        # CHI loc neu la chu Latin/Katakana vo nghia (vd: "yeah yeah yeah", "アアホアアホ"),
        # TUYET DOI KHONG loc cac tu cam xuc nguoi lon tieng Nhat hay lap 3 lan
        # nhu "ダメダメダメ", "イクイクイク", "やめてやめてやめて", "いやいやいや", "待って待って待って"!
        _ADULT_REPEAT_WHITELIST = (
            'だめ', 'ダメ', 'いく', 'イク', 'やめて', 'いや', 'イヤ', 'まって', '待って',
            'すごい', 'やばい', 'ヤバい', 'そこ', 'ここ', 'もっと', 'あかん', 'むり', '無理',
            'すき', '好き', '気持ち', 'きもち', 'いたい', '痛い', 'あつい', '熱い',
        )
        repeat_pattern = re.match(r'^(.{2,3})(\s*\1){2,}$', text, re.IGNORECASE)
        if repeat_pattern:
            if not any(w in text for w in _ADULT_REPEAT_WHITELIST) and not re.search(r'[\u3040-\u309F\u4E00-\u9FFF]', text):
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
