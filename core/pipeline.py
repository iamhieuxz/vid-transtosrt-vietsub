import logging
import re
import time
import os
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, SpinnerColumn
from rich.table import Table
from .database import Database
from .translator import TranslatorService
from .exporter import Exporter
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


class TranslationPipeline:
    def __init__(self, config: dict):
        self.config = config
        self.db = Database()
        self.translator = TranslatorService(
            model_name=config['model']['name'],
            ollama_url=config['model']['ollama_url'],
            temperature=config['model'].get('temperature', 0.1),
            repeat_penalty=config['model'].get('repeat_penalty', 1.2),
            num_ctx=config['model'].get('num_ctx', 4096),
            num_predict=config['model'].get('num_predict', 1024),
            timeout=config['model'].get('timeout', 120),
            circuit_breaker_threshold=config['pipeline'].get('circuit_breaker_threshold', 5),
            circuit_breaker_cooldown=config['pipeline'].get('circuit_breaker_cooldown', 60)
        )
        self.exporter = Exporter(self.db)
        self.validator = Validator()
        self.enable_glossary = config['pipeline'].get('enable_glossary', False)
        self.num_workers = config['pipeline'].get('num_workers', 1)
        self.checkpoint_interval = config['pipeline'].get('checkpoint_interval', 20)
        self.heartbeat_timeout = config['pipeline'].get('heartbeat_timeout', 600)

    def run(self, project_id: int):
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

        console.print(f"{STATUS_ICONS['processing']} [cyan]Processing translation queue...[/cyan]")
        if self.num_workers > 1:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Parallel mode may reduce consistency[/yellow]")
            self._process_parallel(project_id, project)
        else:
            self._process_sequential(project_id, project)

        pending = self.db.count_pending_windows(project_id)
        dead = self.db.count_dead_letter(project_id)
        if dead > 0:
            self.db.update_project_status(project_id, 'completed_with_errors')
        elif pending == 0:
            self.db.update_project_status(project_id, 'completed')
        else:
            self.db.update_project_status(project_id, 'partial')

        self.exporter.export(project_id, project['output_srt'])
        self._print_summary(project_id, pending, dead)
        console.print(f"\n{STATUS_ICONS['complete']} [green]Pipeline finished successfully![/green]")

    def _print_summary(self, project_id: int, pending: int, dead: int):
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
        console.print(table)

    def _parse_srt(self, project_id, input_path):
        import pysrt
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"{STATUS_ICONS['error']} SRT file not found: {input_path}")
        subs = pysrt.open(input_path, encoding='utf-8')
        if not subs:
            raise ValueError(f"{STATUS_ICONS['error']} SRT file is empty: {input_path}")
        items = [{'index': s.index, 'start': str(s.start), 'end': str(s.end), 'text': s.text.replace('\n', ' ').strip()} for s in subs]
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

        completed = 0
        checkpoint_cnt = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Translating...", total=total_windows)

            while True:
                task_info = self.db.claim_task(project_id)
                if not task_info:
                    break
                success = self._process_single_task(project_id, task_info, project, use_translated_history=True)
                if success:
                    completed += 1
                    checkpoint_cnt += 1
                    if checkpoint_cnt % self.checkpoint_interval == 0:
                        self.exporter.export_incremental(project_id, project['output_srt'])
                        logger.info(f"{STATUS_ICONS['success']} Checkpoint at {checkpoint_cnt} windows")
                progress.update(task, advance=1, completed=completed)
                time.sleep(0.05)

    def _process_parallel(self, project_id, project):
        total_items = self.db.get_all_items(project_id)
        total_windows = len(total_items) // project['window_size'] + (1 if len(total_items) % project['window_size'] else 0)

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
                while True:
                    if len(futures) < self.num_workers:
                        task_info = self.db.claim_task(project_id)
                        if task_info:
                            future = executor.submit(self._process_single_task, project_id, task_info, project, False)
                            futures[future] = task_info
                        elif not futures:
                            break
                    done = [f for f in futures if f.done()]
                    for f in done:
                        if f.result():
                            checkpoint_cnt += 1
                            if checkpoint_cnt % self.checkpoint_interval == 0:
                                self.exporter.export_incremental(project_id, project['output_srt'])
                        progress.update(task, advance=1, completed=progress.completed + 1)
                        del futures[f]
                    time.sleep(0.1)

    def _process_single_task(self, project_id, task, project, use_translated_history):
        win_id = task['id']
        win_idx = task['window_index']
        start_sub = task['start_sub_index']
        end_sub = task['end_sub_index']

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

        prompt = self._build_prompt(project, task, context)
        try:
            raw = self.translator.generate(prompt, temperature=0.1)
            json_data = self.translator.extract_json(raw)
            if json_data is None:
                return None
            # Pass list to preserve order
            if not self.validator.validate_json_translation(json_data, sub_indices):
                return None
            mapping = {item['id']: item['text'] for item in json_data}
            translations = [mapping.get(i, '') for i in sub_indices]
            return translations, source_lines
        except Exception as e:
            logger.error(f"{STATUS_ICONS['error']} LLM call failed: {e}")
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

    def _build_prompt(self, project, task, context):
        glossary = ""
        if self.enable_glossary:
            terms = self.db.get_glossary(project['id'])
            if terms:
                glossary_lines = []
                for t in terms:
                    term_line = f"- {t['source_term']} -> {t['target_term']}"
                    if t.get('context_hint'):
                        term_line += f" (context: {t['context_hint']})"
                    glossary_lines.append(term_line)
                glossary = "THUẬT NGỮ BẮT BUỘC:\n" + "\n".join(glossary_lines) + "\n"
        return f"""Bạn là dịch giả chuyên nghiệp dịch phụ đề phim từ {self.config['project']['source_lang']} sang {self.config['project']['target_lang']}.

{glossary}
CÁC DÒNG TRƯỚC (history):
{context['history']}

CÁC DÒNG HIỆN TẠI (current):
{task['original_text']}

CÁC DÒNG TIẾP THEO (future):
{context['future']}

Yêu cầu:
- Dịch chỉ các dòng trong phần "current".
- Trả về dưới dạng JSON array: [{{"id": sub_index, "text": "bản dịch"}}, ...]
- Giữ nguyên số lượng dòng và đúng sub_index.
- Không thêm bất kỳ văn bản nào khác ngoài JSON.

Output (JSON):
"""

    def _save_to_tm(self, project, source_lines, translations):
        src_lang = self.config['project']['source_lang']
        tgt_lang = self.config['project']['target_lang']
        domain = project['name']
        for s, t in zip(source_lines, translations):
            if s.strip() and t.strip():
                self.db.save_translation_memory(src_lang, tgt_lang, s, t, domain=domain, confidence=0.95, min_char_count=4)
