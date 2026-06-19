import logging
import re
import time
import os
import threading
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor
from rich.console import Console
from rich.text import Text
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, SpinnerColumn
from rich.table import Table
from .database import Database
from .translator import TranslatorService
from .exporter import Exporter, get_output_folder, get_output_paths, create_video_shortcut
from .validator import Validator

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

# Prompt translations keyed by target language (English keys for easy extension)
PROMPTS = {
    'vi': {
        'system': (
            'Bạn là dịch giả phụ đề chuyên nghiệp, chuyên dịch từ {src} sang {tgt}.'
            ' Bạn thông thạo văn học, ngôn ngữ, văn hóa và tiếng lóng Trung Quốc.'
        ),
        'glossary_hdr': 'THUAT NGU BUOC PHAI GIU NGUYEN:',
        'history_hdr': 'CAC DONG TRUOC (da dich xong):',
        'current_hdr': 'CAC DONG HIEN TAI (can dich):',
        'next_hdr': 'CAC DONG TIEP THEO (nguon goc):',
        'rules': (
            'YEU CAU NGHIEM NGAT:\n'
            '- Dich CHINH XAC {n} dong trong phan "HIEN TAI"\n'
            '- MOI dong phai dich KHAC NHAU, khong lap lai noi dung\n'
            '- KHONG lap lai cung mot cum tu hoac cau nhieu lan\n'
            '- Tra ve dung JSON array voi {n} phan tu: [{{"id": sub_index, "text": "ban dich"}}, ...]\n'
            '- KHONG them bat ky text nao khac, chi co JSON\n'
            '- KHONG suy nghi truoc, tra loi ngay bang JSON\n'
            '- Dung ngay sau dau ] cua JSON\n\n'
            'LU Y DAC BIET VOI TIENG TRUNG:\n'
            '- Ten rieng, ten nhan vat, ten dia diem: giu nguyen hoac transliterate\n'
            '- Thanh ngu (成语 chengyu): dich theo Y NGHIA, khong theo Tung le\n'
            '- Binh luan man hinh / ghi chu: dich CO NGU CANG, phu hop voi ngon ngu thoai mai\n'
            '- Tieng long / meme Trung Quoc: tim tu Viet tuong duong, khong ghep may\n'
            '- Neu dong chua co gi hoac chi la tieng viet: giu nguyen\n'
            '- Do dai moi dong dich: gan bang do dai goc, phu hop de doc phu de'
        ),
        'output': 'Output:',
    },
    'en': {
        'system': 'You are a professional subtitle translator translating from {src} to {tgt}.',
        'glossary_hdr': 'MANDATORY GLOSSARY:',
        'history_hdr': 'PREVIOUS LINES (already translated):',
        'current_hdr': 'CURRENT LINES (need translation):',
        'next_hdr': 'NEXT LINES:',
        'rules': (
            'STRICT REQUIREMENTS:\n'
            '- Translate EXACTLY {n} lines in the "CURRENT" section\n'
            '- EACH line must be DIFFERENT, do not repeat content\n'
            '- Do NOT repeat the same phrase or sentence multiple times\n'
            '- Return EXACT JSON array with {n} elements: [{{"id": sub_index, "text": "translation"}}, ...]\n'
            '- Do NOT add any other text, only JSON\n'
            '- Do NOT think before answering, respond immediately with JSON\n'
            '- Stop immediately after the ] of the JSON'
        ),
        'output': 'Output:',
    },
    'zh': {
        'system': 'You are a professional subtitle translator translating from {src} to {tgt}.',
        'glossary_hdr': 'MANDATORY GLOSSARY:',
        'history_hdr': 'PREVIOUS LINES (already translated):',
        'current_hdr': 'CURRENT LINES (need translation):',
        'next_hdr': 'NEXT LINES:',
        'rules': (
            'STRICT REQUIREMENTS:\n'
            '- Translate EXACTLY {n} lines in the "CURRENT" section\n'
            '- EACH line must be DIFFERENT, do not repeat content\n'
            '- Do NOT repeat the same phrase or sentence multiple times\n'
            '- Return EXACT JSON array with {n} elements: [{{"id": sub_index, "text": "translation"}}, ...]\n'
            '- Do NOT add any other text, only JSON\n'
            '- Do NOT think before answering, respond immediately with JSON\n'
            '- Stop immediately after the ] of the JSON'
        ),
        'output': 'Output:',
    },
    'ja': {
        'system': 'You are a professional subtitle translator translating from {src} to {tgt}.',
        'glossary_hdr': 'MANDATORY GLOSSARY:',
        'history_hdr': 'PREVIOUS LINES (already translated):',
        'current_hdr': 'CURRENT LINES (need translation):',
        'next_hdr': 'NEXT LINES:',
        'rules': (
            'STRICT REQUIREMENTS:\n'
            '- Translate EXACTLY {n} lines in the "CURRENT" section\n'
            '- EACH line must be DIFFERENT, do not repeat content\n'
            '- Do NOT repeat the same phrase or sentence multiple times\n'
            '- Return EXACT JSON array with {n} elements: [{{"id": sub_index, "text": "translation"}}, ...]\n'
            '- Do NOT add any other text, only JSON\n'
            '- Do NOT think before answering, respond immediately with JSON\n'
            '- Stop immediately after the ] of the JSON'
        ),
        'output': 'Output:',
    },
    'ko': {
        'system': 'You are a professional subtitle translator translating from {src} to {tgt}.',
        'glossary_hdr': 'MANDATORY GLOSSARY:',
        'history_hdr': 'PREVIOUS LINES (already translated):',
        'current_hdr': 'CURRENT LINES (need translation):',
        'next_hdr': 'NEXT LINES:',
        'rules': (
            'STRICT REQUIREMENTS:\n'
            '- Translate EXACTLY {n} lines in the "CURRENT" section\n'
            '- EACH line must be DIFFERENT, do not repeat content\n'
            '- Do NOT repeat the same phrase or sentence multiple times\n'
            '- Return EXACT JSON array with {n} elements: [{{"id": sub_index, "text": "translation"}}, ...]\n'
            '- Do NOT add any other text, only JSON\n'
            '- Do NOT think before answering, respond immediately with JSON\n'
            '- Stop immediately after the ] of the JSON'
        ),
        'output': 'Output:',
    },
}

# Map target_lang name strings to prompt keys
_LANG_KEY_MAP = {
    'vietnamese': 'vi',
    'english': 'en',
    'chinese': 'zh',
    'japanese': 'ja',
    'korean': 'ko',
}


def _is_japanese(lang_name: str) -> bool:
    """Return True nếu source_lang là tiếng Nhật (chuẩn hoá nhiều dạng)."""
    if not lang_name:
        return False
    n = lang_name.strip().lower()
    return n in {'japanese', 'ja', 'tiếng nhật', 'tieng nhat', 'tiếng nhật (japanese)'}


def _resolve_lang_key(lang_name: str) -> str:
    """Resolve a language display name to a prompt key."""
    key = _LANG_KEY_MAP.get(lang_name.lower(), None)
    return key if key else 'en'  # default to English prompt


class TranslationPipeline:
    def __init__(self, config: dict):
        self.config = config
        self.db = Database()
        num_predict = config['model'].get('num_predict', 1024)
        if num_predict < 512:
            logger.warning(f"{STATUS_ICONS['warning']} num_predict={num_predict} is too low, consider setting >= 512")
        self.translator = TranslatorService(
            model_name=config['model']['name'],
            ollama_url=config['model']['ollama_url'],
            temperature=config['model'].get('temperature', 0.1),
            repeat_penalty=config['model'].get('repeat_penalty', 1.2),
            num_ctx=config['model'].get('num_ctx', 4096),
            num_predict=num_predict,
            timeout=config['model'].get('timeout', 120),
            circuit_breaker_threshold=config['pipeline'].get('circuit_breaker_threshold', 5),
            circuit_breaker_cooldown=config['pipeline'].get('circuit_breaker_cooldown', 60),
            max_retries=config['pipeline'].get('max_retries', 3),
            retry_delay=config['pipeline'].get('retry_delay', 2),
        )
        self.exporter = Exporter(self.db)
        self.validator = Validator()
        self.enable_glossary = config.get('pipeline', {}).get('enable_glossary', True)
        self.glossary_terms = self._load_glossary_terms(config)
        self.num_workers = config['pipeline'].get('num_workers', 1)
        self.checkpoint_interval = config['pipeline'].get('checkpoint_interval', 20)
        self.heartbeat_timeout = config['pipeline'].get('heartbeat_timeout', 600)
        self._context_lock = threading.Lock()
        self._db_write_lock = threading.Lock()

        # Optional Japanese batch refiner (ja_trans2) — chạy SAU pipeline chính
        # Dùng cho mọi ngôn ngữ source, nhưng HOẠT ĐỘNG với JA (vì prompt JA-specific)
        self.ja_translator = None
        self.ja_translator2 = None
        self.ja_context_analyzer = None
        self.ja_2tier_enabled = config.get('ja_context', {}).get('enabled', False)
        self._is_japanese_source = _is_japanese(config.get('project', {}).get('source_lang', ''))

        # luôn init ja_trans2 nếu là source JA
        if self._is_japanese_source:
            try:
                from .ja_translator2 import JaTranslator2
                batch_size = config.get('ja_trans2', {}).get('batch_size', 20)
                self.ja_translator2 = JaTranslator2(
                    self.translator,
                    batch_size=batch_size,
                )
                logger.info(f"JaTranslator2 (batch refiner, batch_size={batch_size}) enabled")
            except Exception as e:
                logger.warning(f"Failed to init JaTranslator2: {e}, skipping batch refine")
                self.ja_translator2 = None
            # 2-tier Worker A vẫn giữ nếu bật, nhưng DÙNG với pipeline zh
            if self.ja_2tier_enabled:
                try:
                    from .ja_context_analyzer import JaContextAnalyzer
                    self.ja_context_analyzer = JaContextAnalyzer(
                        self.translator,
                        context_window_size=config.get('ja_context', {}).get('window_size', 18),
                    )
                    logger.info("JaContextAnalyzer (Worker A) enabled")
                except Exception as e:
                    logger.warning(f"Failed to init JaContextAnalyzer: {e}, 2-tier disabled")
                    self.ja_context_analyzer = None

    def _load_glossary_terms(self, config: dict) -> List[dict]:
        """Load glossary from config.yaml."""
        terms = []
        for entry in config.get('glossary', []):
            src = entry.get('source', '').strip()
            tgt = entry.get('target', '').strip()
            if src and tgt:
                terms.append({
                    'source_term': src,
                    'target_term': tgt,
                    'context_hint': entry.get('context', '').strip(),
                })
        return terms

    def run(self, project_id: int):
        """Run the full translation pipeline for a project."""
        project = self.db.get_project(project_id)
        if not project:
            logger.error(f"{STATUS_ICONS['error']} Project {project_id} not found")
            return

        console.print(f"\n{STATUS_ICONS['start']} [cyan]Starting pipeline[/cyan] for project: [bold]{project['name']}[/bold]")

        items = self.db.get_all_items(project_id)
        if not items:
            self._parse_srt(project_id, project['input_srt'])
            self.db.update_project_status(project_id, 'parsed')

        self._create_windows(project_id, project)
        self.db.recover_stuck_tasks(project_id, self.heartbeat_timeout)

        # Worker A: context analysis — chạy TRƯỚC pipeline (dùng với ZH pipeline + ja_trans2)
        if self.ja_context_analyzer is not None:
            console.print(f"{STATUS_ICONS['start']} [cyan]Worker A: analyzing context windows...[/cyan]")
            stats = self.ja_context_analyzer.analyze_project(project_id, self.db)
            console.print(
                f"{STATUS_ICONS['success']} Context analysis: "
                f"{stats['completed']}/{stats['total']} OK, {stats['failed']} failed"
            )
            if stats['completed'] == 0 and stats['total'] > 0:
                console.print(
                    f"{STATUS_ICONS['warning']} [yellow]No context available, "
                    f"running with ZH pipeline + batch refine[/yellow]"
                )
            if stats['completed'] == 0 and stats['total'] > 0:
                console.print(
                    f"{STATUS_ICONS['warning']} [yellow]No context available, "
                    f"running baseline 3-pass without xưng hô mapping[/yellow]"
                )

        console.print(f"{STATUS_ICONS['processing']} [cyan]Processing translation queue...[/cyan]")
        if self.num_workers > 1:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Parallel mode may reduce consistency[/yellow]")
            self._process_parallel(project_id, project)
        else:
            self._process_sequential(project_id, project)

        # Recovery dead windows (dùng standard pipeline zh)
        dead = self.db.count_dead_letter(project_id)
        if dead > 0:
            recovered = self._recover_dead_letters(project_id, project)
            dead = self.db.count_dead_letter(project_id)

        # ja_trans2: batch refine SAU khi pipeline hoàn tất
        # Chỉ chạy nếu là source JA và ja_translator2 đã init
        refined_stats = None
        if self.ja_translator2 is not None and self._is_japanese_source:
            console.print(f"{STATUS_ICONS['start']} [cyan]JaTranslator2: batch refining translated lines...[/cyan]")
            refined_stats = self.ja_translator2.refine_project(
                self.db, project_id,
                progress_callback=lambda done, total: None,
            )
            console.print(
                f"{STATUS_ICONS['success']} Batch refine done: "
                f"{refined_stats['refined']}/{refined_stats['total']} lines refined"
            )

        pending = self.db.count_pending_windows(project_id)
        dead = self.db.count_dead_letter(project_id)

        if dead > 0:
            self.db.update_project_status(project_id, 'completed_with_errors')
        elif pending == 0:
            self.db.update_project_status(project_id, 'completed')
        else:
            self.db.update_project_status(project_id, 'partial')

        # Export to output folder with project name
        self._export_to_folder(project_id, project)

        self._print_summary(project_id, pending, dead, refined_stats=refined_stats)
        console.print(f"\n{STATUS_ICONS['complete']} [green]Pipeline finished successfully![/green]")

    def _export_to_folder(self, project_id: int, project: dict):
        """
        Export files vào folder output theo cấu trúc:
        <output_base>/<project_name>/
          ├── <project_name>.lnk     (shortcut đến video gốc)
          ├── <name>-origin.srt     (file SRT nguồn)
          └── <name>.srt            (file SRT đích)
        """
        project_name = project['name']
        base_output_path = project['output_srt']
        original_video_path = project.get('original_video_path')

        # Tạo folder và lấy đường dẫn các file
        paths = get_output_paths(base_output_path, project_name)

        console.print(f"\n{STATUS_ICONS['start']} [cyan]Exporting to folder:[/cyan] {paths['folder']}")

        # Export file SRT đích (đã dịch)
        self.exporter.export(project_id, paths['translated'])

        # Export file SRT gốc (chưa dịch)
        self.exporter.export_original(project_id, paths['original'])

        # Tạo shortcut đến video gốc
        if original_video_path and os.path.exists(original_video_path):
            shortcut_path = create_video_shortcut(original_video_path, paths['folder'], project_name)
            if shortcut_path:
                console.print(f"{STATUS_ICONS['success']} [green]Video shortcut:[/green] {shortcut_path}")
            else:
                console.print(f"{STATUS_ICONS['warning']} [yellow]Could not create video shortcut[/yellow]")
        else:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Original video not available for shortcut[/yellow]")

    def _print_summary(self, project_id: int, pending: int, dead: int, refined_stats: dict = None):
        """In bang tom tat ket qua."""
        table = Table(title="Translation Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")

        items = self.db.get_all_items(project_id)
        total = len(items)
        translated = sum(1 for it in items if it.get('translated_text'))
        progress_pct = (translated / total * 100) if total > 0 else 0

        table.add_row("Total subtitles", str(total))
        table.add_row("Translated", str(translated))
        table.add_row("Progress", f"{progress_pct:.1f}%")
        table.add_row("Pending", str(pending))
        table.add_row("Failed (dead letter)", str(dead))
        if refined_stats:
            table.add_row(
                "Batch refined (ja_trans2)",
                f"{refined_stats['refined']}/{refined_stats['total']} lines"
            )
        console.print(table)

    def _parse_srt(self, project_id, input_path):
        import pysrt
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"{STATUS_ICONS['error']} SRT file not found: {input_path}")
        subs = pysrt.open(input_path, encoding='utf-8')
        if not subs:
            raise ValueError(f"{STATUS_ICONS['error']} SRT file is empty: {input_path}")
        # Preserve newlines for multi-line subtitles, normalize whitespace
        items = [{'index': s.index, 'start': str(s.start), 'end': str(s.end), 'text': ' '.join(s.text.split())} for s in subs]
        if not items:
            raise ValueError(f"{STATUS_ICONS['error']} No subtitle items found in: {input_path}")
        self.db.save_subtitle_items(project_id, items)
        self.db.update_project_status(project_id, 'parsed', len(items))
        console.print(f"{STATUS_ICONS['success']} [green]Parsed {len(items)} subtitle items[/green]")

    def _create_windows(self, project_id, project):
        items = self.db.get_all_items(project_id)
        total = len(items)
        win_size = project['window_size']
        with self.db._get_connection() as conn:
            if conn.execute("SELECT COUNT(*) FROM windows WHERE project_id=?", (project_id,)).fetchone()[0] > 0:
                return
        for i in range(0, total, win_size):
            chunk = items[i:i+win_size]
            start_sub = chunk[0]['sub_index']
            end_sub = chunk[-1]['sub_index']
            start_pos = i
            end_pos = i + len(chunk) - 1
            lines = [f"[{it['sub_index']}] {it['original_text']}" for it in chunk]
            self.db.save_window(project_id, i//win_size, start_sub, end_sub, start_pos, end_pos, "\n".join(lines))

    def _process_sequential(self, project_id, project):
        total_items = self.db.get_all_items(project_id)
        total_windows = len(total_items) // project['window_size'] + (1 if len(total_items) % project['window_size'] else 0)
        max_retries = self.config.get('pipeline', {}).get('max_retries', 3)
        max_failures = self.config.get('pipeline', {}).get('max_failures', 50)

        completed = 0
        failed = 0
        checkpoint_cnt = 0
        failed_windows = set()

        class FailedColumn(TextColumn):
            def __init__(self):
                super().__init__("")
            def render(self, task):
                return Text(f"({completed}/{failed}/{task.total})")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            FailedColumn(),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Translating...", total=total_windows)

            while True:
                task_info = self.db.claim_task(project_id)
                if not task_info:
                    break
                win_id = task_info['id']
                win_idx = task_info['window_index']

                if win_id in failed_windows:
                    logger.warning(f"{STATUS_ICONS['warning']} Window {win_idx} already exhausted retries, skipping")
                    progress.update(task, advance=1)
                    progress.refresh()
                    continue

                success = self._process_single_task(project_id, task_info, project, use_translated_history=True)
                if success:
                    completed += 1
                    checkpoint_cnt += 1
                    if checkpoint_cnt % self.checkpoint_interval == 0:
                        self.exporter.export_incremental(project_id, project['output_srt'])
                        logger.info(f"{STATUS_ICONS['success']} Checkpoint at {checkpoint_cnt} windows")
                else:
                    failed += 1
                    with self.db._get_connection() as conn:
                        row = conn.execute("SELECT retry_count FROM windows WHERE id=?", (win_id,)).fetchone()
                    if row and row['retry_count'] >= max_retries:
                        failed_windows.add(win_id)
                        self.db.mark_task_dead(win_id, "Exhausted all retries")
                        logger.error(f"{STATUS_ICONS['error']} Window {win_idx} exhausted all retries ({row['retry_count']}/{max_retries}), moved to dead letter")

                progress.update(task, advance=1)
                progress.refresh()

                if failed > 0 and failed >= max_failures and completed == 0:
                    logger.error(f"Aborting: {failed} consecutive failures with 0 success")
                    break

    def _process_parallel(self, project_id, project):
        total_items = self.db.get_all_items(project_id)
        total_windows = len(total_items) // project['window_size'] + (1 if len(total_items) % project['window_size'] else 0)
        max_retries = self.config.get('pipeline', {}).get('max_retries', 3)
        max_failures = self.config.get('pipeline', {}).get('max_failures', 50)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Translating (parallel)...", total=total_windows)

            with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
                futures = {}
                checkpoint_cnt = 0
                completed_cnt = 0
                failed_cnt = 0
                failed_windows = set()
                idle_rounds = 0
                while True:
                    if len(futures) < self.num_workers:
                        task_info = self.db.claim_task(project_id)
                        if task_info:
                            win_id = task_info['id']
                            if win_id in failed_windows:
                                self.db.mark_task_dead(win_id, "Exhausted retries")
                                continue
                            future = executor.submit(self._process_single_task, project_id, task_info, project, True)
                            futures[future] = task_info
                            idle_rounds = 0
                        elif not futures:
                            break
                        else:
                            idle_rounds += 1
                            if idle_rounds > 50:
                                break
                    done = [f for f in futures if f.done()]
                    for f in done:
                        try:
                            ok = bool(f.result())
                        except Exception:
                            ok = False
                        t_info = futures[f]
                        if ok:
                            completed_cnt += 1
                            checkpoint_cnt += 1
                            if checkpoint_cnt % self.checkpoint_interval == 0:
                                self.exporter.export_incremental(project_id, project['output_srt'])
                        else:
                            failed_cnt += 1
                            with self.db._get_connection() as conn:
                                row = conn.execute("SELECT retry_count FROM windows WHERE id=?", (t_info['id'],)).fetchone()
                            if row and row['retry_count'] >= max_retries:
                                failed_windows.add(t_info['id'])
                                self.db.mark_task_dead(t_info['id'], "Exhausted retries")
                        progress.update(task, advance=1, completed=completed_cnt)
                        del futures[f]
                    if failed_cnt >= max_failures and completed_cnt == 0:
                        logger.error(f"Aborting parallel: {failed_cnt} consecutive failures with 0 success")
                        break
                    time.sleep(0.1)

    def _process_single_task(self, project_id, task, project, use_translated_history):
        win_id = task['id']
        win_idx = task['window_index']
        start_sub = task['start_sub_index']
        end_sub = task['end_sub_index']

        # Lock context reads and DB writes to prevent race conditions in parallel mode
        with self._context_lock:
            context = self._get_context(project_id, task['start_pos'], task['end_pos'], project, use_translated=use_translated_history)
            result = self._translate_window(project_id, task, context, project)

            if result:
                translations, source_lines = result
                if self.validator.validate_window_content(source_lines, translations):
                    try:
                        self.db.commit_window(project_id, win_id, start_sub, end_sub, translations)
                        self._save_to_tm(project, source_lines, translations)
                        return True
                    except Exception as e:
                        logger.error(f"{STATUS_ICONS['error']} commit_window failed: {e}")
                        self.db.mark_task_failed(win_id, str(e), retry_count_increment=True)
                        return False
                else:
                    logger.warning(f"{STATUS_ICONS['warning']} Window {win_idx} failed validation")
                    self.db.mark_task_failed(win_id, "Validation failed", retry_count_increment=True)
                    return False
            else:
                self.db.mark_task_failed(win_id, "LLM output invalid", retry_count_increment=True)
                return False

    def _translate_window(self, project_id, task, context, project):
        orig_lines_with_ids = [l.strip() for l in task['original_text'].split('\n') if l.strip()]
        sub_indices = []
        source_lines = []
        for line in orig_lines_with_ids:
            m = re.match(r'^\[(\d+)\]\s*(.*)', line)
            if m:
                sub_indices.append(int(m.group(1)))
                source_lines.append(m.group(2).strip())
            else:
                logger.warning(f"{STATUS_ICONS['warning']} Cannot parse line format, skipping: {line[:50]}...")
                continue

        tm_hits = []
        all_in_tm = True
        for src in source_lines:
            if src:
                trans = self.db.get_translation_memory(
                    self.config['project']['source_lang'],
                    self.config['project']['target_lang'],
                    src, domain=project['name'], min_char_count=4)
                if trans is None:
                    all_in_tm = False
                    break
                tm_hits.append(trans)
            else:
                tm_hits.append("")
        if all_in_tm and len(tm_hits) == len(source_lines):
            logger.debug(f"{STATUS_ICONS['success']} Window {task['window_index']}: all lines from TM")
            return tm_hits, source_lines

        glossary = self._get_glossary(project_id) if self.enable_glossary else []
        src_lang = self.config['project']['source_lang']
        tgt_lang = self.config['project']['target_lang']

        # Japanese 3-pass: literal -> polish -> qa (keigo-aware)
        if self.ja_translator is not None:
            return self._translate_window_ja(
                task, source_lines, sub_indices, glossary, project_id,
            )

        # Pass 1: standard JSON translation
        prompt = self._build_prompt(project, task, context, glossary=glossary)
        try:
            raw = self.translator.generate(prompt, temperature=0.1)
            json_data = self.translator.extract_json(raw)
            if json_data is not None and self.validator.validate_json_translation(json_data, sub_indices):
                mapping = {item['id']: item['text'] for item in json_data}
                translations = [mapping.get(i, '') for i in sub_indices]
                logger.info(f"Window {task['window_index']}: standard pass OK ({len(translations)} lines)")
                return translations, source_lines
        except Exception as e:
            logger.warning(f"Window {task['window_index']}: standard pass failed ({e}), trying fallback...")

        # Pass 2: fallback — chunk + simple prompt, no JSON
        logger.info(f"Window {task['window_index']}: using fallback translation ({len(source_lines)} lines)")
        try:
            translations = self.translator.fallback_translate(
                source_lines, src_lang, tgt_lang, glossary_terms=glossary
            )
            if translations is None:
                logger.error(f"Window {task['window_index']}: fallback returned None")
                return None
            if not self.validator.validate_window_content(source_lines, translations):
                logger.warning(f"Window {task['window_index']}: fallback validation failed")
                return None
            logger.info(f"Window {task['window_index']}: fallback pass OK")
            return translations, source_lines
        except Exception as e:
            logger.error(f"Window {task['window_index']}: fallback exception: {e}")
            return None

    def _translate_window_ja(self, task, source_lines, sub_indices, glossary, project_id):
        """Luồng dịch riêng cho tiếng Nhật: 3-pass (literal → polish → qa)
        + 2-tier context (inject từ Worker A) + Worker C scene-review.

        Trả về (translations, source_lines) giống _translate_window thường.
        """
        from .ja_translator import JaTranslator  # noqa: F401
        import json

        # Lấy context cho window hiện tại (2-tier)
        context_summary = self._get_ja_context_for_window(task, project_id)

        # Pass 1: literal
        rough = self.ja_translator.literal_translate(
            source_lines, glossary_terms=glossary,
        )
        if rough is None:
            logger.warning(f"Window {task['window_index']}: JA literal pass failed")
            return None
        if len(rough) != len(source_lines):
            logger.warning(
                f"Window {task['window_index']}: JA literal returned {len(rough)} "
                f"for {len(source_lines)} lines, trimming"
            )
            rough = (rough + source_lines)[:len(source_lines)]

        # Pass 2: polish (có inject context nếu có)
        polished = self.ja_translator.polish_translate(
            source_lines, rough, glossary_terms=glossary,
            context_summary=context_summary,
        )
        if len(polished) != len(source_lines):
            polished = (polished + source_lines)[:len(source_lines)]

        # Pass 3: qa review (chỉ áp suggestion nếu có)
        qa = None
        try:
            qa = self.ja_translator.qa_review(
                source_lines, polished, glossary_terms=glossary,
                context_summary=context_summary,
            )
            final = self.ja_translator.apply_qa_results(polished, qa)
        except Exception as e:
            logger.debug(f"JA QA pass skipped: {e}")
            final = polished

        # Validate
        if not self.validator.validate_window_content(source_lines, final):
            logger.warning(
                f"Window {task['window_index']}: JA validation failed, "
                f"falling back to polished"
            )
            final = polished
            if not self.validator.validate_window_content(source_lines, final):
                logger.warning(
                    f"Window {task['window_index']}: JA polished also failed, "
                    f"falling back to rough"
                )
                final = rough

        logger.info(
            f"Window {task['window_index']}: JA 3-pass OK "
            f"({len(final)} lines, qa_issues={sum(1 for x in (qa or []) if not x.get('ok'))}, "
            f"context={'yes' if context_summary else 'no'})"
        )
        return final, source_lines

    def _get_ja_context_for_window(self, task, project_id):
        """Lấy context từ bảng window_contexts cho translation-window hiện tại.

        Trả về dict {speakers, pronouns_map, tone, setting, summary} hoặc None.
        """
        try:
            ctx_window_size = self.ja_context_analyzer.context_window_size
            start_pos = task.get('start_pos', 0)
            # start_pos của translation-window -> context_window_index
            ctx_idx = start_pos // ctx_window_size
            ctx = self.db.get_window_context(project_id, ctx_idx)
            if not ctx or ctx.get("status") != "completed":
                return None
            import json as _json
            return {
                "speakers": _json.loads(ctx.get("speakers_json") or "[]"),
                "pronouns_map": _json.loads(ctx.get("pronouns_map") or "{}"),
                "tone": ctx.get("tone", "unknown"),
                "setting": ctx.get("setting", "unknown"),
                "summary": ctx.get("summary", ""),
            }
        except Exception as e:
            logger.debug(f"_get_ja_context_for_window: {e}")
            return None

    def _get_context(self, project_id, start_pos, end_pos, project, use_translated=False):
        items = self.db.get_all_items(project_id)
        hist_size = project['history_size']
        fut_size = project['future_size']
        hist_start = max(0, start_pos - hist_size)
        hist_items = items[hist_start:start_pos]
        hist_lines = []
        for it in hist_items:
            text = it.get('translated_text') if use_translated and it.get('translated_text') else it['original_text']
            hist_lines.append(f"[{it['sub_index']}] {text}")
        history = "\n".join(hist_lines) if hist_lines else ""

        fut_end = min(len(items), end_pos + fut_size + 1)
        fut_items = items[end_pos+1:fut_end]
        future = "\n".join([f"[{it['sub_index']}] {it['original_text']}" for it in fut_items]) if fut_items else ""
        return {"history": history, "future": future}

    def _get_glossary(self, project_id: int) -> List[dict]:
        """
        Lấy glossary từ 2 nguồn: database (per-project) và config.yaml (global).
        Config glossary được ưu tiên nếu enable_glossary=True.
        """
        all_terms = []

        # 1. Config glossary (từ config.yaml) - luôn có nếu được bật
        if self.enable_glossary and self.glossary_terms:
            all_terms.extend(self.glossary_terms)

        # 2. Database glossary (per-project, thêm tay trong CLI)
        db_terms = self.db.get_glossary(project_id)
        for t in db_terms:
            if not any(existing['source_term'] == t['source_term'] for existing in all_terms):
                all_terms.append({
                    'source_term': t['source_term'],
                    'target_term': t['target_term'],
                    'context_hint': t.get('context_hint', ''),
                })

        return all_terms

    def _build_prompt(self, project, task, context, glossary=None):
        src_lang = self.config['project']['source_lang']
        tgt_lang = self.config['project']['target_lang']
        p_key = _resolve_lang_key(tgt_lang)
        p = PROMPTS[p_key]

        # glossary already passed in from _translate_window, no need to reload
        if glossary is None:
            glossary = self._get_glossary(project['id']) if self.enable_glossary else []

        glossary_block = ""
        if glossary:
            glossary_lines = []
            for t in glossary:
                term_line = f"- {t['source_term']} -> {t['target_term']}"
                if t.get('context_hint'):
                    term_line += f" (context: {t['context_hint']})"
                glossary_lines.append(term_line)
            glossary_block = p['glossary_hdr'] + "\n" + "\n".join(glossary_lines) + "\n"

        num_lines = len([l for l in task['original_text'].split('\n') if l.strip()])

        return f"""{p['system'].format(src=src_lang, tgt=tgt_lang)}

{glossary_block}{p['history_hdr']}
{context['history']}

{p['current_hdr']}
{task['original_text']}

{p['next_hdr']}
{context['future']}

{p['rules'].format(n=num_lines)}

### {p['output']}"""

    def _save_to_tm(self, project, source_lines, translations):
        src_lang = self.config['project']['source_lang']
        tgt_lang = self.config['project']['target_lang']
        domain = project['name']
        with self._db_write_lock:
            for s, t in zip(source_lines, translations):
                if s.strip() and t.strip():
                    self.db.save_translation_memory(src_lang, tgt_lang, s, t, domain=domain, confidence=0.95, min_char_count=4)

    def _recover_dead_letters(self, project_id, project):
        """
        Recovery 3-pass cho dead windows:
          Pass 1 (raw)    — dịch thô word-by-word, chunk nhỏ
          Pass 2 (polish) — trau chuốt câu từ, ngữ cảnh, tiếng lóng
          Pass 3 (commit) — validate & lưu vào DB
        """
        dead_windows = self.db.get_dead_letter_windows(project_id)
        if not dead_windows:
            return 0

        recovered = 0
        src_lang = self.config['project']['source_lang']
        tgt_lang = self.config['project']['target_lang']
        glossary = self._get_glossary(project_id) if self.enable_glossary else []
        all_items = self.db.get_all_items(project_id)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"[cyan]Recovering {len(dead_windows)} dead windows...", total=len(dead_windows)
            )

            for dw in dead_windows:
                progress.advance(task)
                progress.refresh()

                # Parse source lines từ original_text
                orig_lines_with_ids = [l.strip() for l in dw['original_text'].split('\n') if l.strip()]
                sub_indices = []
                source_lines = []
                for line in orig_lines_with_ids:
                    m = re.match(r'^\[(\d+)\]\s*(.*)', line)
                    if m:
                        sub_indices.append(int(m.group(1)))
                        source_lines.append(m.group(2).strip())

                if not source_lines:
                    self.db.remove_dead_letter(dw['id'])
                    recovered += 1
                    continue

                # Lấy context xung quanh dead window để polish
                start_pos = dw['start_pos']
                end_pos = dw['end_pos']
                hist_items = all_items[max(0, start_pos - 5):start_pos]
                hist_trans = "\n".join(
                    f"[{it['sub_index']}] {it.get('translated_text') or it['original_text']}"
                    for it in hist_items
                )
                fut_items = all_items[end_pos + 1:end_pos + 6]
                fut_src = "\n".join(
                    f"[{it['sub_index']}] {it['original_text']}"
                    for it in fut_items
                )

                logger.info(f"Recovery[{dw['window_index']}]: pass-1 raw translate ({len(source_lines)} lines)")

                # Japanese 3-pass recovery (keigo-aware)
                if self.ja_translator is not None:
                    # Lấy context từ Worker A (2-tier)
                    ctx_summary = None
                    if self.ja_context_analyzer is not None:
                        ctx_row = self.db.get_context_for_pos(project_id, dw.get('start_pos', 0))
                        if ctx_row and ctx_row.get("status") == "completed":
                            import json as _json
                            ctx_summary = {
                                "speakers": _json.loads(ctx_row.get("speakers_json") or "[]"),
                                "pronouns_map": _json.loads(ctx_row.get("pronouns_map") or "{}"),
                                "tone": ctx_row.get("tone", "unknown"),
                                "setting": ctx_row.get("setting", "unknown"),
                                "summary": ctx_row.get("summary", ""),
                            }
                    rough = self.ja_translator.literal_translate(
                        source_lines, glossary_terms=glossary,
                    )
                    if rough is None or len(rough) != len(source_lines):
                        logger.error(f"Recovery[{dw['window_index']}]: JA literal invalid, skipping")
                        continue
                    polished = self.ja_translator.polish_translate(
                        source_lines, rough, glossary_terms=glossary,
                        context_summary=ctx_summary,
                    )
                    if len(polished) != len(source_lines):
                        polished = (polished + source_lines)[:len(source_lines)]
                    try:
                        qa = self.ja_translator.qa_review(
                            source_lines, polished, glossary_terms=glossary,
                            context_summary=ctx_summary,
                        )
                        polished = self.ja_translator.apply_qa_results(polished, qa)
                    except Exception:
                        pass
                else:
                    raw_trans = self.translator.raw_translate(source_lines, src_lang, tgt_lang, glossary_terms=glossary)
                    if raw_trans is None or len(raw_trans) != len(source_lines):
                        logger.error(f"Recovery[{dw['window_index']}]: raw_translate returned invalid result, skipping")
                        continue

                    logger.info(f"Recovery[{dw['window_index']}]: pass-2 polish translate")
                    polished = self.translator.polish_translate(
                        source_lines, raw_trans, src_lang, tgt_lang,
                        context_before=hist_trans, context_after=fut_src,
                    )
                    if polished is None:
                        polished = raw_trans  # fallback giữ nguyên rough

                # Validate & commit
                raw_fallback = raw_trans if not self.ja_translator else rough
                if not self.validator.validate_window_content(source_lines, polished):
                    logger.warning(f"Recovery[{dw['window_index']}]: polished content failed validation, trying raw")
                    if not self.validator.validate_window_content(source_lines, raw_fallback):
                        logger.error(f"Recovery[{dw['window_index']}]: both raw & polished failed validation, skipping")
                        continue
                    polished = raw_fallback

                try:
                    self.db.commit_window(project_id, dw['window_id'], dw['start_sub_index'], dw['end_sub_index'], polished)
                    self._save_to_tm(project, source_lines, polished)
                    self.db.remove_dead_letter(dw['id'])
                    logger.info(f"Recovery[{dw['window_index']}]: saved {len(polished)} lines")
                    recovered += 1
                except Exception as e:
                    logger.error(f"Recovery[{dw['window_index']}]: commit failed: {e}")
                    continue

        console.print(f"{STATUS_ICONS['success']} [green]Recovered {recovered}/{len(dead_windows)} dead windows[/green]")
        return recovered

    def _worker_c_scene_review(self, project_id, project):
        """Worker C: review scene-40 (4 translation-windows liên tiếp) và auto-fix
        các inconsistency về keigo/xưng hô.

        Chạy SAU khi main pipeline xong. Tự động update DB với suggestion
        từ model (auto-fix theo thống nhất với user).
        """
        import json as _json

        all_items = self.db.get_all_items(project_id)
        if not all_items:
            return

        win_size = project['window_size']
        scene_window_size = self.config.get('ja_context', {}).get('scene_review_size', 36)

        # Gom translation-windows thành scene
        scenes = []
        cur_scene = []
        for it in all_items:
            cur_scene.append(it)
            if len(cur_scene) >= scene_window_size:
                scenes.append(cur_scene)
                cur_scene = []
        if cur_scene:
            scenes.append(cur_scene)

        if not scenes:
            return

        console.print(
            f"{STATUS_ICONS['start']} [cyan]Worker C: scene review "
            f"({len(scenes)} scenes of {scene_window_size} lines)...[/cyan]"
        )

        glossary = self._get_glossary(project_id) if self.enable_glossary else []
        total_fixed = 0

        for scene_idx, scene_items in enumerate(scenes):
            # Lấy context của scene này (Worker A đã tạo)
            # Tính pos dựa trên thứ tự: items đã sort theo sub_index
            try:
                first_sub = scene_items[0]['sub_index']
                # Tìm pos = chỉ số item trong all_items
                pos = next(
                    i for i, it in enumerate(all_items) if it['sub_index'] == first_sub
                )
            except Exception:
                pos = 0
            ctx = self.db.get_context_for_pos(project_id, pos)
            context_summary = None
            if ctx and ctx.get("status") == "completed":
                context_summary = {
                    "speakers": _json.loads(ctx.get("speakers_json") or "[]"),
                    "pronouns_map": _json.loads(ctx.get("pronouns_map") or "{}"),
                    "tone": ctx.get("tone", "unknown"),
                    "setting": ctx.get("setting", "unknown"),
                    "summary": ctx.get("summary", ""),
                }

            # Gom source + translation (giữ nguyên index, không filter empty strings)
            sources = [it['original_text'] for it in scene_items if 'translated_text' in it and it.get('translated_text') is not None]
            translations = [it['translated_text'] for it in scene_items if 'translated_text' in it and it.get('translated_text') is not None]
            if not sources:
                continue
            if len(sources) != len(translations):
                continue

            # Gọi QA pass (Worker C dùng lại ja_translator.qa_review)
            try:
                qa = self.ja_translator.qa_review(
                    sources, translations, glossary_terms=glossary,
                    context_summary=context_summary,
                )
            except Exception as e:
                logger.warning(f"Worker C scene {scene_idx} QA failed: {e}")
                continue

            # Auto-fix: tìm các dòng ok=false có suggestion → update DB
            fixed_in_scene = 0
            for item in qa:
                if item.get("ok", True):
                    continue
                idx = item.get("index", 0) - 1
                suggestion = (item.get("suggestion") or "").strip()
                if not (0 <= idx < len(scene_items)) or not suggestion:
                    continue
                target_item = scene_items[idx]
                if not target_item.get('translated_text'):
                    continue
                # Validate suggestion không rỗng / quá khác
                old = target_item['translated_text']
                if not self.validator.validate_window_content(
                    [target_item['original_text']], [suggestion]
                ):
                    logger.debug(
                        f"Worker C scene {scene_idx} idx {idx+1}: "
                        f"suggestion failed validation, skipping"
                    )
                    continue
                # Update DB
                with self.db._get_connection() as conn:
                    conn.execute(
                        "UPDATE subtitle_items SET translated_text=? WHERE id=?",
                        (suggestion, target_item['id'])
                    )
                    conn.commit()
                fixed_in_scene += 1
                logger.info(
                    f"Worker C scene {scene_idx} auto-fix sub {target_item['sub_index']}: "
                    f"'{old[:30]}...' -> '{suggestion[:30]}...'"
                )

            if fixed_in_scene:
                total_fixed += fixed_in_scene
                logger.info(
                    f"Worker C scene {scene_idx}: fixed {fixed_in_scene} lines"
                )

        console.print(
            f"{STATUS_ICONS['success']} [green]Worker C: auto-fixed {total_fixed} lines "
            f"across {len(scenes)} scenes[/green]"
        )
