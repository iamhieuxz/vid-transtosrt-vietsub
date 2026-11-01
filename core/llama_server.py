"""Quan sinh voi cua llama-server (llama.cpp) ben trong pipeline.

Vi sao can: RTX 3060 Ti 8GB khong du cho Whisper large-v3 (CPU fallback neu thieu VRAM)
va GGUF 5.4GB cung luc. Pipeline therefore own the two phases:

    transcribe (Whisper giu VRAM)  ->  clear_vram()  ->  llama-server (giu VRAM)
                                            ^                    |
                                            --- auto_stop -------'

Gui.py khong can ma hanh rieng: no chi spawn `main.py` lam subprocess, nen vo
duong ben trong run_translation() du de bao tram cho ca CLI lan GUI.

Cac flag mac dinh duoc CHON BANG CACH DO THU NGHIAEM tren may nay (xem bench.py):
    --spec-draft-n-max 1  -> 66-72 t/s   (n-max 3 nguoc lai chi 49-53 t/s)
    --cache-type-k/v q8_0 -> KV ~16 KB/token, 16K ctx chi ~268 MB
    --repeat-penalty 1.0  -> can cho MTP draft khong bi loi
"""
from __future__ import annotations

import atexit
import os
import subprocess
import time
import logging

logger = logging.getLogger(__name__)


class LlamaServerError(RuntimeError):
    """llama-server kh the khoi dong hoac kh phan hoi trong thoi gian cho phep."""


_MANAGER: "LlamaServerManager | None" = None
_MANAGER_KEY = None


def get_manager(config: dict) -> "LlamaServerManager":
    """Mot process chi co mot llama-server, nen moi ngung dung chung mot manager.

    main.py can no de nhiem VRAM truoc Whisper va tat sau khi dich xong;
    TranslationPipeline can no de bat lenh luc model duoc can. Ne'u moi noi `LlamaServerManager(config)`
    thi khong noi dung nao nam duoc PID cua lenh da thuc su chay.
    """
    global _MANAGER, _MANAGER_KEY
    key = repr(sorted(dict((config or {}).get('llama_server') or {}).items()))
    if _MANAGER is None or _MANAGER_KEY != key:
        if _MANAGER is not None and _MANAGER.running:
            logger.warning("[LLAMA] config llama_server doi -> thu tat server cua config cu")
            _MANAGER.stop(reason='config changed')
        _MANAGER = LlamaServerManager(config)
        _MANAGER_KEY = key
    return _MANAGER


class LlamaServerManager:
    """Doc block `llama_server:` trong config.yaml va nam lifecycle cua no."""

    def __init__(self, config: dict):
        self.cfg = dict((config or {}).get('llama_server') or {})
        self.enabled = bool(self.cfg.get('enabled', False))
        self.host = str(self.cfg.get('host', '127.0.0.1'))
        self.port = int(self.cfg.get('port', 8080))
        self.alias = str(self.cfg.get('alias', 'qwen35-9b-mtp'))
        self.auto_stop = bool(self.cfg.get('auto_stop', True))
        # stop_reused: cung tat server do NGUOI bat tay (vd start-llama.ps1) khi
        # pipeline can VRAM hoac ket thuc run. Truoc khi kill phai xac minh image
        # dung la llama-server.exe (_pid_is_llama_server) de khong giet oan app khac.
        self.stop_reused = bool(self.cfg.get('stop_reused', True))
        self.startup_timeout = float(self.cfg.get('startup_timeout', 180))
        self.log_dir = self.cfg.get('log_dir') or os.path.expanduser('~/llama-logs')
        self._proc = None
        self._started_by_us = False
        self._last_err_log = ''

    # -- vi tri server dang dau? ---------------------------------------------
    def manages_url(self, url: str) -> bool:
        """Pipeline chi can quan ly khi model dang chay THAT tro den server nay.

        So khuc voi PORT, khong chi voi host: neu URL tro 8180 trong khi manager nam
        8080 thi do la hai server khac nhau, gop chung lai se khac dong bat om.
        """
        if not self.enabled or not url:
            return False
        u = str(url).lower()
        if '/v1' not in u:
            return False
        if f':{self.port}' in u:
            return True
        # URL kh ghi port ro (80/443 mac dinh) -> thi khop khi cung host.
        return f'//{self.host}/' in u or '//localhost/' in u

    def _base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def health(self) -> bool:
        import requests
        try:
            r = requests.get(f"{self._base()}/health", timeout=3)
            return r.status_code == 200 and r.json().get('status') == 'ok'
        except Exception:
            return False

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -- lenh ---------------------------------------------------------------
    def build_args(self) -> list:
        exe = self.cfg.get('exe')
        model = self.cfg.get('model')
        if not exe or not os.path.isfile(str(exe)):
            raise LlamaServerError(f"llama-server.exe khong ton tai: {exe}")
        if not model or not os.path.isfile(str(model)):
            raise LlamaServerError(f"File GGUF khong ton tai: {model}")

        args = [str(exe),
                '--model', str(model),
                '--alias', self.alias,
                '--n-gpu-layers', str(self.cfg.get('ngl', 99)),
                '--ctx-size', str(self.cfg.get('ctx', 16384)),
                '--ubatch-size', str(self.cfg.get('ubatch', 128)),
                '--flash-attn', 'on' if self.cfg.get('flash_attn', True) else 'off',
                '--cache-type-k', str(self.cfg.get('kv_cache_type', 'q8_0')),
                '--cache-type-v', str(self.cfg.get('kv_cache_type', 'q8_0')),
                '--host', self.host,
                '--port', str(self.port),
                '--temp', str(self.cfg.get('temperature', 0.2)),
                '--repeat-penalty', str(self.cfg.get('repeat_penalty', 1.0)),
                '--parallel', str(self.cfg.get('parallel', 1))]
        if self.cfg.get('jinja', True):
            args.append('--jinja')
        reasoning = self.cfg.get('reasoning', None)
        if reasoning is not None and reasoning != '':
            # YAML 1.1 doc `off`/`on` thanh bool -> phai map nguoc lai. Neu de nguyen
            # `if cfg.get('reasoning')` thi `off` (=False) lam flag bi bo qua am tham.
            r = 'off' if reasoning is False else 'on' if reasoning is True else str(reasoning)
            args += ['--reasoning', r]
        if self.cfg.get('reasoning_budget') is not None:
            # 0 = ket thuc thinking ngay. Can thiet voi model co template tu sinh
            # khoi suy luan dai: --reasoning off khong du (do that: model sinh du
            # 2048 token thinking, cham max_tokens va tra content RONG).
            args += ['--reasoning-budget', str(int(self.cfg['reasoning_budget']))]
        spec = self.cfg.get('spec') or {}
        if spec.get('type') and int(spec.get('draft_n_max', 1)) > 0:
            args += ['--spec-type', str(spec['type']),
                     '--spec-draft-n-max', str(int(spec['draft_n_max']))]
        args += [str(a) for a in (self.cfg.get('extra_args') or [])]
        return args

    # -- pidfile: chong orphan khi bi kill manh (GUI nut Dung dung TerminateProcess,
    #    atexit se khong chay, nen lan sau phai tu don don).
    def _pidfile(self) -> str:
        return os.path.join(self.log_dir, 'llama-server.pid')

    def _read_stale_pid(self):
        try:
            with open(self._pidfile(), encoding='ascii') as f:
                return int(f.read().strip() or 0)
        except Exception:
            return None

    def _write_pid(self, pid: int):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            with open(self._pidfile(), 'w', encoding='ascii') as f:
                f.write(str(pid))
        except Exception as e:
            logger.debug(f"pidfile khong ghi duoc: {e}")

    def _clear_pid(self):
        try:
            os.remove(self._pidfile())
        except Exception:
            pass

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if not pid:
            return False
        try:
            import ctypes
            STILL_ACTIVE = 259
            if os.name == 'nt':
                kernel32 = ctypes.windll.kernel32
                h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if not h:
                    return False
                code = ctypes.c_ulong()
                ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
                kernel32.CloseHandle(h)
                return bool(ok) and code.value == STILL_ACTIVE
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def _port_occupied_foreign(self) -> bool:
        """Co cho da bind port nay (vd: project khac tren 0.0.0.0:8080).

        Windows cho phep llama-server bind 127.0.0.1:8080 ben canh mot 0.0.0.0:8080
        da ton tai, va uu tien bind cu the hon - hai server trung mot port nhu the
        rat de hieu sai khi chan doan, nen phat hien som va bao doi port.
        """
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.4)
                return s.connect_ex((self.host, self.port)) == 0
        except Exception:
            return False

    # -- hanh dong ----------------------------------------------------------
    def ensure_running(self) -> bool:
        """Khoi dong model neu can, cho den khi /health = ok. Tra ve True neu san sang."""
        if not self.enabled:
            return False
        if self.health():
            if not self._started_by_us:
                logger.info(f"[LLAMA] Dang co server san tren {self._base()} - dung lai, khong tu tat")
            return True

        if self.running:
            # Chinh ta da spawn no va no dang load model -> tiep tuc cho, khoi noi tranh port.
            logger.info("[LLAMA] Server cua pipeline dang load, tiep tuc cho /health")
            err_path = self._last_err_log
        else:
            self._reap_orphan()
            if self._port_occupied_foreign():
                raise LlamaServerError(
                    f"Port {self.port} dang co ung dung khac lang nghe (phat qua /health cua "
                    f"llama-server). Doi sang port khac o CAl cho: llama_server.port "
                    f"va models.llama.ollama_url trong config.yaml.")
            args = self.build_args()
            os.makedirs(self.log_dir, exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            out_path = os.path.join(self.log_dir, f'pipeline-{stamp}.log')
            err_path = os.path.join(self.log_dir, f'pipeline-{stamp}.log.err')
            logger.info(f"[LLAMA] Khoi dong llama-server (port {self.port}), log: {err_path}")
            logger.info(f"[LLAMA] cmdline: {' '.join(args)}")

            # stdout/stderr PHAI tro ra file rieng, khong duoc ke PIPE cua main.py:
            # GUI doc stdout den EOF, server ke tu pipe se giam pipe dong mai mai.
            with open(out_path, 'wb') as out, open(err_path, 'wb') as err:
                self._proc = subprocess.Popen(
                    args, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                    cwd=os.path.dirname(str(self.cfg.get('exe'))),
                )
            self._started_by_us = True
            self._write_pid(self._proc.pid)
            self._last_err_log = err_path

        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise LlamaServerError(
                    f"llama-server thoat voi ma {self._proc.returncode}. "
                    f"Xem log: {err_path}\n{self._tail(err_path)}")
            if self.health():
                logger.info(f"[LLAMA] Model '{self.alias}' san sang sau {time.time()-(deadline-self.startup_timeout):.1f}s")
                return True
            time.sleep(0.5)
        self.stop(reason='startup timeout')
        raise LlamaServerError(
            f"llama-server khong health sau {self.startup_timeout}s. Xem log: {err_path}")

    def free_vram(self) -> bool:
        """Goi TRUOC Whisper: day VRAM cua LLM ra de ASR khong phai ngam CPU fallback."""
        if not self.enabled:
            return False
        if not self.health():
            self._reap_orphan()
            return False
        if self._started_by_us or self._pid_alive(self._read_stale_pid()):
            logger.info("[LLAMA] Tat server de nhiem VRAM cho Whisper")
            return self.stop(reason='free VRAM for transcription')
        if self.stop_reused:
            logger.info("[LLAMA] stop_reused=true -> tat server ben ngoai de nhiem VRAM cho Whisper")
            return self.stop(reason='free VRAM for transcription (reused server)')
        logger.warning(
            "[LLAMA] Van con mot llama-server KHONG do pipeline nay bat tren port "
            f"{self.port}. Whisper se thieu VRAM. Tat thu cong: taskkill /IM llama-server.exe /F "
            f"(hoac set llama_server.stop_reused: true)")
        return False

    def stop(self, reason: str = '') -> bool:
        proc, self._proc = self._proc, None
        stopped = False
        if proc and proc.poll() is None:
            logger.info(f"[LLAMA] Dung llama-server ({reason or 'normal'})")
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            stopped = True
        elif self.stop_reused and self.health():
            # Server dung chung (do user bat thu cong truoc do): pipeline chi reuse,
            # mac dinh van tat sau khi vietsub hoan thanh de giai phong VRAM.
            # Tat theo PID dang bind dung port va CHI khi image la llama-server.exe
            # -> khong co nguy co giet oan app khac lang nghe tren port do.
            pid = self._port_pid()
            if pid and self._pid_is_llama_server(pid):
                logger.info(f"[LLAMA] Dung llama-server dung chung (PID {pid}) sau khi xong ({reason or 'normal'})")
                try:
                    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                                   capture_output=True, timeout=20)
                    stopped = True
                except Exception as e:
                    logger.warning(f"[LLAMA] Khong tat duoc llama-server dung chung {pid}: {e}")
            elif pid:
                logger.warning(
                    f"[LLAMA] Port {self.port} do app KHAC (PID {pid}) lang nghe - "
                    f"bo qua, khong tat.")
        self._started_by_us = False
        self._clear_pid()
        if stopped:
            self._wait_gone(10)
        return stopped

    # -- dinh vi server dung chung -------------------------------------------
    def _port_pid(self):
        """PID dang LISTEN tren (host, port) cua manager. None neu khong co."""
        try:
            out = subprocess.run(['netstat', '-ano', '-p', 'tcp'],
                                 capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return None
        for line in out.splitlines():
            parts = line.split()
            # Thu tu cot netstat: Proto | Local Address | Foreign Address | State | PID
            if len(parts) >= 5 and parts[0] == 'TCP' and parts[3].upper() == 'LISTENING':
                local = parts[1]
                if local.rsplit(':', 1)[-1] == str(self.port):
                    try:
                        return int(parts[-1])
                    except ValueError:
                        return None
        return None

    def _pid_is_llama_server(self, pid: int) -> bool:
        """Xac image cua PID dung la llama-server.exe (case-insensitive)."""
        try:
            out = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return False
        first = out.strip().splitlines()[0] if out.strip() else ''
        image = first.split('"')[1].lower() if '"' in first else ''
        return image == 'llama-server.exe'

    def _reap_orphan(self):
        """Giết tiến trình còn sót từ lần chạy trước (bị kill mạnh nên atexit không chạy)."""
        pid = self._read_stale_pid()
        if pid and self._pid_alive(pid) and not (self._proc and self._proc.pid == pid):
            logger.warning(f"[LLAMA] Don orphan tu lan chay truoc (PID {pid})")
            try:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                                   capture_output=True, timeout=20)
                else:
                    os.kill(pid, 15)
            except Exception as e:
                logger.warning(f"[LLAMA] Khong diet duoc orphan {pid}: {e}")
        self._clear_pid()

    def _wait_gone(self, timeout: float):
        """Cho toi khi port ngung phan hoi, de Whisper khoi dung duoc VRAM ngay."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.health():
                return
            time.sleep(0.3)

    @staticmethod
    def _tail(path: str, lines: int = 15) -> str:
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return ''.join(f.readlines()[-lines:])
        except Exception as e:
            return f"(kh doc duoc log: {e})"

    def __enter__(self):
        self.ensure_running()
        return self

    def __exit__(self, *exc):
        if self.auto_stop:
            self.stop(reason='pipeline end')
        return False

    def register_atexit(self):
        """For CLI: Ctrl+C / lenh roi van kh de orphan nam VRAM."""
        if self.auto_stop:
            atexit.register(self._atexit_hook)

    def _atexit_hook(self):
        # Chi tat server do chinh pipeline nay bat; thoat som (precheck fail, Ctrl+C) khong duoc
        # giet server cua user (stop_reused chi dung cho free_vram/stop chu dong).
        if self._started_by_us or self.running:
            self.stop(reason='process exit')
