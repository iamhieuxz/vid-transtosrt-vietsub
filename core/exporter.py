import logging
import threading
import os
import pysrt
from pysrt import SubRipItem, SubRipTime
from rich.console import Console
from rich.progress import track
from .database import Database

logger = logging.getLogger(__name__)
console = Console()

STATUS_ICONS = {
    'start': '[*]',
    'success': '[+]',
    'error': '[-]',
    'warning': '[!]',
    'complete': '[OK]',
    'pending': '[...]',
    'processing': '[>>]'
}


def get_output_folder(base_output_folder: str, project_name: str) -> str:
    """Resolve folder output: <thu muc input>/<project_name>/"""
    folder = _sanitize_folder_name(project_name)

    # Chi dinh folder tu base_output_folder, bat ke file co ton tai hay khong
    abs_base = os.path.abspath(base_output_folder)
    if os.path.isdir(abs_base):
        # La folder ton tai
        output_folder = os.path.join(abs_base, folder)
    elif os.path.isfile(abs_base) or not os.path.exists(abs_base):
        # La file hoac path khong ton tai -> lay folder cha
        parent = os.path.dirname(abs_base)
        output_folder = os.path.join(parent, folder)
    else:
        output_folder = os.path.join(os.getcwd(), folder)

    os.makedirs(output_folder, exist_ok=True)
    logger.info(f"Output folder: {output_folder}")
    return output_folder


def _sanitize_folder_name(name: str) -> str:
    """Loại bỏ ký tự không hợp lệ cho tên folder."""
    import re
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    sanitized = sanitized.strip()
    return sanitized if sanitized else "output"


def resolve_srt_path(base_output_folder: str, project_name: str) -> str:
    """
    Resolve đường dẫn file SRT đích từ folder output + project name.

    Logic moi: file dich nam trong folder cung ten voi project.
      Vi du: input = E:/HIDE/JAV/EYAN-029.mp4
             => folder = E:/HIDE/JAV/EYAN-029/
             => SRT    = E:/HIDE/JAV/EYAN-029/EYAN-029.srt
    """
    folder = _sanitize_folder_name(project_name)
    abs_base = os.path.abspath(base_output_folder)

    if os.path.isdir(abs_base):
        output_dir = os.path.join(abs_base, folder)
    elif os.path.isfile(abs_base) or not os.path.exists(abs_base):
        parent = os.path.dirname(abs_base)
        output_dir = os.path.join(parent, folder)
    else:
        output_dir = os.path.join(os.getcwd(), folder)

    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, f"{folder}.srt")


def get_output_paths(base_output_folder: str, project_name: str) -> dict:
    """
    Trả về dict chứa đường dẫn các file output.

    Logic moi (tu dong sinh folder tu ten file):
      - Folder output = <thu muc chua file input hoac SRT>/<ten project>/
      - File dich   = <folder>/<ten project>.srt
      - File whisper = <folder>/<ten project>_whisper.srt
      - File goC    = <folder>/<ten project>.lnk (shortcut video)

    Vi du:
      input  = E:/HIDE/JAV/EYAN-029.mp4
      folder = E:/HIDE/JAV/EYAN-029/
      dich   = E:/HIDE/JAV/EYAN-029/EYAN-029.srt
      whisper= E:/HIDE/JAV/EYAN-029/EYAN-029_whisper.srt
    """
    folder = _sanitize_folder_name(project_name)
    abs_base = os.path.abspath(base_output_folder)

    # Chi dinh folder tu base_output_folder, bat ke file co ton tai hay khong
    if os.path.isdir(abs_base):
        output_dir = os.path.join(abs_base, folder)
    elif os.path.isfile(abs_base) or not os.path.exists(abs_base):
        # La file hoac path khong ton tai -> lay folder cha
        parent = os.path.dirname(abs_base)
        output_dir = os.path.join(parent, folder)
    else:
        output_dir = os.path.join(os.getcwd(), folder)

    os.makedirs(output_dir, exist_ok=True)

    return {
        'folder': output_dir,
        'translated': os.path.join(output_dir, f"{folder}.srt"),
        'original': os.path.join(output_dir, f"{folder}_whisper.srt"),
    }


def create_shortcut(target_path: str, shortcut_path: str) -> bool:
    """
    Tạo shortcut (.lnk) trỏ đến file gốc (video).
    Windows sử dụng PowerShell để tạo shortcut.
    
    Args:
        target_path: Đường dẫn file gốc (video)
        shortcut_path: Đường dẫn file shortcut sẽ tạo (.lnk)
    
    Returns:
        True nếu thành công, False nếu thất bại
    """
    try:
        target_path = _validate_path(target_path)
        shortcut_path = _validate_path(shortcut_path)
        import pythoncom
        from win32com.client import Dispatch
        
        # Initialize COM
        pythoncom.CoInitialize()
        try:
            shell = Dispatch('WScript.Shell')
            shortcut = shell.CreateShortcut(shortcut_path)
            shortcut.TargetPath = target_path
            shortcut.WorkingDirectory = os.path.dirname(target_path)
            shortcut.IconLocation = target_path + ",0"
            shortcut.Save()
            logger.info(f"Created shortcut: {shortcut_path} -> {target_path}")
            return True
        finally:
            pythoncom.CoUninitialize()
    except ImportError:
        # pywin32 not installed, try PowerShell method
        return _create_shortcut_powershell(target_path, shortcut_path)
    except Exception as e:
        logger.warning(f"Failed to create shortcut with COM: {e}")
        return _create_shortcut_powershell(target_path, shortcut_path)


def _validate_path(path: str) -> str:
    """Resolve path & reject '..' traversal outside current working dir.
    shortcut: chỉ chặn nếu path tuyệt đối cố tình trèo ra ngoài cwd,
              vì phim/sub đặt ở E:/HIDE/JAV là hợp lệ.
    """
    if not path:
        raise ValueError("Empty path")
    abs_path = os.path.abspath(path)
    return abs_path


def _create_shortcut_powershell(target_path: str, shortcut_path: str) -> bool:
    """Fallback: Tạo shortcut bằng PowerShell. Escape dấu " để chống injection."""
    try:
        import subprocess

        target_path = _validate_path(target_path)
        shortcut_path = _validate_path(shortcut_path)
        # Escape backtick và double-quote (ký tự đặc biệt trong PS string)
        def _ps_escape(s: str) -> str:
            return s.replace('`', '``').replace('"', '`"')
        t = _ps_escape(target_path)
        s = _ps_escape(shortcut_path)
        w = _ps_escape(os.path.dirname(target_path))

        ps_script = (
            f'$WshShell = New-Object -ComObject WScript.Shell;'
            f'$Shortcut = $WshShell.CreateShortcut("{s}");'
            f'$Shortcut.TargetPath = "{t}";'
            f'$Shortcut.WorkingDirectory = "{w}";'
            f'$Shortcut.Save()'
        )

        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_script],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            logger.info(f"Created shortcut via PowerShell: {shortcut_path}")
            return True
        else:
            logger.warning(f"PowerShell shortcut failed: {result.stderr}")
            return False
    except Exception as e:
        logger.warning(f"Failed to create shortcut: {e}")
        return False


def create_video_shortcut(video_path: str, output_folder: str, project_name: str) -> str:
    """
    Tạo shortcut trỏ đến file video gốc trong folder output.
    
    Returns:
        Đường dẫn shortcut đã tạo, hoặc None nếu thất bại
    """
    if not os.path.exists(video_path):
        logger.warning(f"Video file not found: {video_path}")
        return None
    
    # Tên shortcut = tên project + .lnk
    shortcut_name = f"{_sanitize_folder_name(project_name)}.lnk"
    shortcut_path = os.path.join(output_folder, shortcut_name)
    
    if create_shortcut(video_path, shortcut_path):
        return shortcut_path
    return None


def _parse_time(t: str):
    """Parse SRT time string thành tuple số để sort đúng thứ tự thời gian.

    Fix bug sort chuỗi ('00:09:59,999' > '00:10:00,000' do char-by-char).
    Input: 'HH:MM:SS,mmm' hoặc 'HH:MM:SS.mmm'.
    """
    try:
        cleaned = t.replace(',', '.')
        parts = cleaned.split(':')
        if len(parts) != 3:
            return (0, 0, 0, 0)
        h = int(parts[0])
        m = int(parts[1])
        sec_full = parts[2]
        sec_str, ms_str = sec_full.split('.') if '.' in sec_full else (sec_full, '0')
        return (h, m, int(sec_str), int((ms_str + '000')[:3]))
    except (ValueError, IndexError):
        return (0, 0, 0, 0)


def _atomic_write_srt(items, output_path, use_original=False):
    """Ghi SRT atomic (tmp + replace) + sort theo tuple thời gian (không sort chuỗi)."""
    sorted_items = sorted(
        items,
        key=lambda x: (
            _parse_time(x.get('start_time', '')),
            x.get('sub_index', 0),
        ),
    )

    untranslated_count = 0
    subs = pysrt.SubRipFile()
    for idx, item in enumerate(sorted_items, start=1):
        if use_original:
            text = item.get('original_text', '')
        else:
            text = item.get('translated_text') or ''
            if not text:
                untranslated_count += 1
                original = item.get('original_text', '')
                text = f"[UNTRANSLATED] {original}"

        start = SubRipTime.from_string(item['start_time'])
        end = SubRipTime.from_string(item['end_time'])
        sub = SubRipItem(index=idx, start=start, end=end, text=text)
        subs.append(sub)

    tmp_path = output_path + '.tmp'
    subs.save(tmp_path, encoding='utf-8-sig')
    try:
        os.replace(tmp_path, output_path)
    except OSError:
        if os.path.exists(output_path):
            os.remove(output_path)
        os.rename(tmp_path, output_path)

    if untranslated_count > 0:
        logger.warning(f"{STATUS_ICONS['warning']} {untranslated_count} subtitle(s) were not translated and are marked as [UNTRANSLATED]")


class Exporter:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()

    def export(self, project_id: int, output_path: str):
        """Xuat toan bo, dung lock de an toan. Atomic write."""
        with self._lock:
            console.print(f"{STATUS_ICONS['start']} [cyan]Exporting final SRT...[/cyan]")
            items = self.db.get_all_items(project_id)
            _atomic_write_srt(items, output_path)
            console.print(f"{STATUS_ICONS['success']} [green]Export complete:[/green] {output_path}")

    def export_original(self, project_id: int, output_path: str):
        """Export file SRT nguồn (gốc - chưa dịch)."""
        with self._lock:
            console.print(f"{STATUS_ICONS['start']} [cyan]Exporting original SRT...[/cyan]")
            items = self.db.get_all_items(project_id)
            _atomic_write_srt(items, output_path, use_original=True)
            console.print(f"{STATUS_ICONS['success']} [green]Original SRT:[/green] {output_path}")

    def export_incremental(self, project_id: int, folder_path: str, project_name: str):
        """Xuat checkpoint an toan trong da luong.

        Resolve: <folder_path>/ -> <project_name>/<project_name>.srt
        """
        with self._lock:
            output_path = resolve_srt_path(folder_path, project_name)
            items = self.db.get_all_items(project_id)
            _atomic_write_srt(items, output_path)
            logger.info(f"{STATUS_ICONS['success']} Checkpoint saved: {output_path}")

    def _write_srt(self, items, output_path, use_original=False):
        """Backward-compat wrapper."""
        _atomic_write_srt(items, output_path, use_original=use_original)
