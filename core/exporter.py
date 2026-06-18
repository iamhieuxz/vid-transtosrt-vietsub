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


def get_output_folder(base_output_path: str, project_name: str) -> str:
    """
    Tạo folder output theo cấu trúc:
    <base_folder>/<project_name>/
    
    Ví dụ: E:/trans-video/test.srt + "test-video" 
            -> E:/trans-video/test-video/
    """
    base_dir = os.path.dirname(base_output_path)
    folder_name = _sanitize_folder_name(project_name)
    output_folder = os.path.join(base_dir, folder_name)
    
    if not os.path.exists(output_folder):
        os.makedirs(output_folder, exist_ok=True)
        logger.info(f"Created output folder: {output_folder}")
    
    return output_folder


def _sanitize_folder_name(name: str) -> str:
    """Loại bỏ ký tự không hợp lệ cho tên folder."""
    import re
    # Thay thế các ký tự không hợp lệ bằng underscore
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Loại bỏ khoảng trắng thừa ở đầu/cuối
    sanitized = sanitized.strip()
    return sanitized if sanitized else "output"


def get_output_paths(base_output_path: str, project_name: str) -> dict:
    """
    Trả về dict chứa đường dẫn các file output:
    - folder: đường dẫn folder output
    - translated: file SRT đích (đích)
    - original: file SRT nguồn (gốc)
    
    Ví dụ input: base_output="E:/trans-video/test.srt", project_name="test-video"
    Kết quả:
      folder: E:/trans-video/test-video/
      translated: E:/trans-video/test-video/test.srt
      original: E:/trans-video/test-video/test-origin.srt
    """
    output_folder = get_output_folder(base_output_path, project_name)
    base_name = os.path.splitext(os.path.basename(base_output_path))[0]
    
    return {
        'folder': output_folder,
        'translated': os.path.join(output_folder, f"{base_name}.srt"),
        'original': os.path.join(output_folder, f"{base_name}-origin.srt"),
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


def _create_shortcut_powershell(target_path: str, shortcut_path: str) -> bool:
    """Fallback: Tạo shortcut bằng PowerShell."""
    try:
        import subprocess
        
        ps_script = f'''
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
$Shortcut.TargetPath = "{target_path}"
$Shortcut.WorkingDirectory = "{os.path.dirname(target_path)}"
$Shortcut.Save()
'''
        
        result = subprocess.run(
            ['powershell', '-Command', ps_script],
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


class Exporter:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()

    def export(self, project_id: int, output_path: str):
        """Xuat toan bo, dung lock de an toan."""
        with self._lock:
            console.print(f"{STATUS_ICONS['start']} [cyan]Exporting final SRT...[/cyan]")
            items = self.db.get_all_items(project_id)
            self._write_srt(items, output_path)
            console.print(f"{STATUS_ICONS['success']} [green]Export complete:[/green] {output_path}")

    def export_original(self, project_id: int, output_path: str):
        """Export file SRT nguồn (gốc - chưa dịch)."""
        with self._lock:
            console.print(f"{STATUS_ICONS['start']} [cyan]Exporting original SRT...[/cyan]")
            items = self.db.get_all_items(project_id)
            self._write_srt(items, output_path, use_original=True)
            console.print(f"{STATUS_ICONS['success']} [green]Original SRT:[/green] {output_path}")

    def export_incremental(self, project_id: int, output_path: str):
        """Xuat hien tai (checkpoint) an toan trong da luong."""
        with self._lock:
            tmp_path = output_path + ".tmp"
            items = self.db.get_all_items(project_id)
            self._write_srt(items, tmp_path)
            try:
                os.replace(tmp_path, output_path)
            except OSError:
                if os.path.exists(output_path):
                    os.remove(output_path)
                os.rename(tmp_path, output_path)
            logger.info(f"{STATUS_ICONS['success']} Checkpoint saved: {output_path}")

    def _write_srt(self, items, output_path, use_original=False):
        """Xuat SRT - sap xep theo start_time de dam bao thu tu dung."""
        sorted_items = sorted(items, key=lambda x: (x.get('start_time', ''), x.get('sub_index', 0)))

        untranslated_count = 0
        subs = pysrt.SubRipFile()
        for idx, item in enumerate(sorted_items, start=1):
            if use_original:
                # Xuất file gốc - không dịch
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

        subs.save(output_path, encoding='utf-8')

        if untranslated_count > 0:
            logger.warning(f"{STATUS_ICONS['warning']} {untranslated_count} subtitle(s) were not translated and are marked as [UNTRANSLATED]")
