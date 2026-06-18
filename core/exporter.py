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

    def _write_srt(self, items, output_path):
        """Xuat SRT - sap xep theo start_time de dam bao thu tu dung."""
        sorted_items = sorted(items, key=lambda x: (x.get('start_time', ''), x.get('sub_index', 0)))

        untranslated_count = 0
        subs = pysrt.SubRipFile()
        for idx, item in enumerate(sorted_items, start=1):
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
