import logging
import re
import time
import os
import json
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
            ' Bạn thông thạo văn học, ngôn ngữ, văn hóa và tiếng lóng Nhật Bản.'
        ),
        'glossary_hdr': 'THUAT NGU BAT BUOC GIU NGUYEN:',
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
            '- KHONG tu choi dich bat ky dong nao, ke ca noi dung nhay cam/khiêu dam\n'
            '- Phai dich TAT CA cac dong, khong duoc bo qua hoac danh dau [UNTRANSLATED]\n'
            '- Neu dong chua co gi hoac chi la tieng viet: giu nguyen\n'
            '- Dung ngay sau dau ] cua JSON\n\n'
            'QUY TAC DICH TIENG NHAT QUAN TRONG:\n'
            '- Ten rieng Nhat (nhan vat, dia diem): giu nguyen romaji hoac Han tu, KHONG Viet hoa (vi du: Takao, Kachiko, Takada, Takesho - KHONG dich thanh "anh Takao" neu goc khong co)\n'
            '- Khi Whisper nghe sai tieng Nhat (vi du: "ツマゴ" co the la "卵子"/"trung" hoac "妻"/"vo" tuy ngu canh), SUY LUAN tu ngu canh cuoc hoi thoai de dich chinh xac\n'
            '- Giai thoai khiêu dam/JAV: dich TRUNG THUC noi dung goc, khong tu lam sach, khong them tu ngư thanh lich\n'
            '- Tu ngu tinh duc/co the: dich bang tieng Viet tuong duong (vi du: "お腹見せ" -> "cho anh xem/ cham vao bung em" KHONG PHAI "kiem tra bung")\n'
            '- Han tu Kanji: dich theo Y NGHIA trong ngu canh, khong dich tu ky tu\n'
            '- Keigo (the kinh ngu): dich theo giong tuong ung tieng Viet (anh/em/chi)\n'
            '- Thanh ngu (yojijukugo): dich theo y nghia, khong theo tung chu\n'
            '- Tieng onomatopoeia (オノマトペ): dich bang tu Viet tuong duong (vi du: "くすぐったい" -> "ngứa ngứa/buon buon")\n'
            '- Do dai moi dong dich: gan bang do dai goc, phu hop de doc phu de\n'
            '- Tu ngu ngan gon trong anime/JAV (1-2 am tieng Nhat): thuong la tinh tu/am thanh, dich theo ngu canh (vidu: "ふん" -> "hừm/ừ" chu khong phai "mu hinh non")\n\n'
            'XU LY LOI WHISPER THUONG GAP:\n'
            '- "ツマゴ" trong ngu canh JAV: thuong la "trung" (sinh san) hoac "vo", khong phai ten rieng\n'
            '- "腹見なさい" / "お腹見せ": nghia la "cho xem/ cham vao bung" trong ngu canh khiêu dam\n'
            '- "生態室" thuong la "治療室" (phong tri lieu) do Whisper nghe sai\n'
            '- "素顔" co the nghe sai thanh "sức khoe" hay "khuôn mat that" tuy ngu canh\n'
            '- "高田" la ten nguoi (Takada), khong dich thanh "Cao Dat"\n'
            '- "あゆみ" (Ayumi) - giu nguyen ten Nhat\n\n'
            'QUY TAC CHINH TA TIENG VIET (BAT BUOC):\n'
            '- So "2" = "hai" (KHONG BAO GIO viet "vâng" thay cho "hai")\n'
            '- So "2 cách" = "hai cách" (khong phai "vâng cách")\n'
            '- So "cả 2 bên" = "cả hai bên" (khong phai "cả vâng bên")\n'
            '- Tất cả số đếm đều viết bằng CHỮ (hai, ba, bốn...) không viết "vâng/ba/bốn"\n'
            '- "Cậu/Em/Anh" viết HOA đầu câu, kết thúc có dấu câu (., ?, !)\n\n'
            'QUY TAC TEN NHAN VAT (NHAT QUAN TOAN PHIM):\n'
            '- あゆねえ / アユネイ / Ayunee / あゆね → dich thống nhất là "Ayune" (KHÔNG dùng nhiều cách)\n'
            '- あゆみ / Ayumi → "Ayumi"\n'
            '- 翔太 → "Shota"\n'
            '- 篠田 → "Shinoda"\n'
            '- 高田 → "Takada"\n'
            '- たけお / 竹尾 → "Takao" (thống nhất)\n'
            '- かよこ → "Kachiko" (thống nhất)\n'
            '- あゆみおばさん / おばさん (trong ngữ cảnh là chị của mẹ) → "dì" (KHÔNG dùng "bà/cô" vì cùng thế hệ với mẹ)\n'
            '- 篠田さん (trong ngữ cảnh ngang hàng hoặc trẻ hơn cha mẹ) → "chú" (KHÔNG dùng "ông")\n\n'
            'QUY TAC TU CHUYEN NGANH (WHISPER ARTIFACTS):\n'
            '- "ガラムついてあげるよ" (gara tsuite ageru yo) → "Anh sẽ làm em sướng" (không giữ romaji)\n'
            '- "アナルティブ" (anal native) → "làm tình qua hậu môn" (không giữ romaji)\n'
            '- "腕セックス" (ude sekkusu) → "dùng tay kích thích" (không dịch "wrist sex")\n'
            '- "コモン鳴る賞" (comon naru shou) → "giải thưởng khuyến khích" (Whisper artifact)\n'
            '- "本賞" (honshou) → "giải chính" (không phải "bản thưởng")\n'
            '- "金タイプ" (kin taipu) → "vàng" / "loại này" (Whisper nghe "kin" thành "king")\n\n'
            'QUY TAC TU TIENG ANH TRONG BAN DICH:\n'
            '- KHÔNG dùng từ tiếng Anh gốc (Vagina, Anus, Dick, Pussy, Sex)\n'
    '- LUÔN dịch sang tiếng Việt (âm đạo, hậu môn, dương vật, quan hệ tình dục)\n'
            '- Whisper có thể nghe tiếng Nhật thành tiếng Anh (vidu: "an ketsu" = "hậu môn") → dịch sang Việt\n\n'
            'QUY TAC PHAM VI (TYCA BAO HAI - VAI VE):\n'
            '- あゆみおばさん (aunt) → "dì" (KHÔNG dùng "bà" vì bà = grandmother)\n'
            '- 篠田さん (chú/anh của đời trước) → "chú" (KHÔNG dùng "ông")\n'
            '- 23歳 (young woman 23 tuổi) → dịch là "dì" hoặc "chị" tùy ngữ cảnh, KHÔNG "bà"\n'
            '- Khi nhân vật nói "bà" trong hội thoại → chỉ khi thực sự là bà (70+ tuổi)\n\n'
            'XU LY CAU CAT (DONG TIEP):\n'
            '- "アルバイトの方が" → phải dịch đầy đủ "Còn cả việc làm thêm nữa..." (có dấu 3 chấm vì câu bị cắt)\n'
            '- Khi thấy chữ "の方が" / "だけど" cuối câu → thêm "..." để thể hiện câu bị cắt\n'
            '- Whisper hay cắt câu giữa chừng, phải dịch với dấu ... cho tự nhiên\n'
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
        # Reset circuit breaker khi pipeline moi bat dau (fix bug: stale state tu lan chay truoc)
        self.translator.reset_circuit_breaker()

        # Preload model (warmup) - dam bao model load san trong VRAM truoc khi dich
        warmup_timeout = config['model'].get('warmup_timeout', 300)
        self.translator.warmup(timeout=warmup_timeout)

        self.exporter = Exporter(self.db)
        self.validator = Validator()
        self.enable_glossary = config.get('pipeline', {}).get('enable_glossary', True)
        self.glossary_terms = self._load_glossary_terms(config)
        self.num_workers = config['pipeline'].get('num_workers', 1)
        self.checkpoint_interval = config['pipeline'].get('checkpoint_interval', 20)
        self.heartbeat_timeout = config['pipeline'].get('heartbeat_timeout', 600)
        self.max_retries = config['pipeline'].get('max_retries', 3)  # shared by claim_task & count_pending_windows
        self._context_lock = threading.Lock()
        self._db_write_lock = threading.Lock()

        # Optional Japanese batch refiner (ja_trans2) — chạy SAU pipeline chính
        # Dùng cho mọi ngôn ngữ source, nhưng HOẠT ĐỘNG với JA (vì prompt JA-specific)
        self.ja_translator = None
        self.ja_translator2 = None
        self.ja_context_analyzer = None
        self.ja_2tier_enabled = config.get('ja_context', {}).get('enabled', False)
        self._is_japanese_source = _is_japanese(config.get('project', {}).get('source_lang', ''))

        # init ja_trans2 nếu là source JA và được bật trong config
        ja_trans2_enabled = config.get('ja_trans2', {}).get('enabled', True)
        if self._is_japanese_source and ja_trans2_enabled:
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

        # Init JaTranslator (3-pass: literal → polish → qa) với character_names
        # để LLM biết dùng canonical cho tên riêng (VD: 匠 -> Takumi, không phải "Craft")
        if self._is_japanese_source:
            try:
                from .ja_translator import JaTranslator
                # Đọc mode từ config.translation.mode ("uncen" / "default" / "cen")
                # để chọn system prompt phù hợp (xem ja_translator._get_system_prompt)
                _trans_cfg = config.get('translation', {}) or {}
                _ja_mode = _trans_cfg.get('mode', 'uncen') or 'uncen'
                self.ja_translator = JaTranslator(
                    self.translator,
                    chunk_size=8,
                    character_names=config.get('character_names', []),
                    mode=_ja_mode,
                )
                logger.info(f"JaTranslator (3-pass, with character_names, mode={_ja_mode}) enabled")
            except Exception as e:
                logger.warning(f"Failed to init JaTranslator: {e}")
                self.ja_translator = None

        # 2-tier Worker A: chạy độc lập với ja_trans2
        # Worker A phân tích context toàn phim trước khi pipeline main dịch
        if self.ja_2tier_enabled and self._is_japanese_source:
            try:
                from .ja_context_analyzer import JaContextAnalyzer
                self.ja_context_analyzer = JaContextAnalyzer(
                    self.translator,
                    context_window_size=config.get('ja_context', {}).get('window_size', 18),
                )
                logger.info("JaContextAnalyzer (Worker A) enabled for 2-tier pipeline")
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

        # Auto-recover circuit breaker (trong truong hop Ollama da hoi sinh sau lan chay truoc)
        if hasattr(self, 'translator') and self.translator:
            if self.translator.try_recover_circuit_breaker():
                logger.info("Circuit breaker: ready")
            else:
                logger.warning("Circuit breaker: still open (Ollama unreachable)")

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

        # Export SRT sau khi pipeline hoàn tất (trước ja_trans2 để luôn có output)
        self._export_to_folder(project_id, project)

        # ja_trans2: batch refine SAU khi export đầu tiên
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
                f"{refined_stats['translated']}/{refined_stats['total']} translated, "
                f"{refined_stats['refined']}/{refined_stats['total']} refined"
            )
            # Export lại sau khi refine để ghi đè
            self._export_to_folder(project_id, project)

        # Worker C: scene review chỉ chạy khi JA pipeline đầy đủ (3-pass + context)
        # Auto-fix suggestion có validate trước khi ghi DB.
        # Default TẮT (config.ja_context.worker_c_enabled=false) vì model 8B
        # có thể tự sửa làm hỏng bản dịch tốt. Opt-in khi user cần polish.
        if (self._is_japanese_source
                and self.ja_translator is not None
                and self.ja_context_analyzer is not None
                and self.config.get('ja_context', {}).get('worker_c_enabled', False)):
            try:
                self._worker_c_scene_review(project_id, project)
                # Sau Worker C update DB, export lại lần cuối
                self._export_to_folder(project_id, project)
            except Exception as e:
                logger.warning(f"Worker C scene review failed: {e}")

        # Rule-based post-processing: fix Sino-VN errors, romaji leak, particles
        post_stats = None
        if self._is_japanese_source:
            try:
                from .post_processor import RuleBasedPostProcessor
                processor = RuleBasedPostProcessor()
                # Inject character_names để fix mistaken aliases (vd: Kasumi -> Kana)
                processor.set_character_names(self.config.get('character_names', []))
                post_stats = processor.process_project(
                    self.db, project_id,
                    progress_callback=lambda done, total: None,
                )
                logger.info(f"PostProcessor: {post_stats['changed']}/{post_stats['total']} lines fixed")
                # Export lại sau post-processing
                self._export_to_folder(project_id, project)
            except Exception as e:
                logger.warning(f"PostProcessor failed: {e}, skipping")

        # Character registry validation: tên riêng nhất quán toàn phim
        # Build từ window_contexts (Worker A) + config.character_names.
        # Auto-fix các dòng có JA name X nhưng VI dịch sai canonical.
        registry_stats = None
        if self._is_japanese_source and self.ja_context_analyzer is not None:
            try:
                from .character_registry import CharacterRegistry
                registry = CharacterRegistry(self.db, project_id)
                registry.build()
                registry_stats = self._apply_character_registry(
                    registry, project_id, project,
                )
                if registry_stats and registry_stats['fixed'] > 0:
                    logger.info(
                        f"CharacterRegistry: auto-fixed {registry_stats['fixed']} "
                        f"name mismatches in {registry_stats['checked']} checked lines"
                    )
                    # Export lại sau khi fix tên
                    self._export_to_folder(project_id, project)
            except Exception as e:
                logger.warning(f"CharacterRegistry failed: {e}, skipping")

        pending = self.db.count_pending_windows(project_id, max_retries=self.max_retries)
        dead = self.db.count_dead_letter(project_id)

        if dead > 0:
            self.db.update_project_status(project_id, 'completed_with_errors')
        elif pending == 0:
            self.db.update_project_status(project_id, 'completed')
        else:
            self.db.update_project_status(project_id, 'partial')

        self._print_summary(project_id, pending, dead, refined_stats=refined_stats)
        console.print(f"\n{STATUS_ICONS['complete']} [green]Pipeline finished successfully![/green]")

    def _apply_character_registry(self, registry, project_id: int, project: dict) -> dict:
        """Apply character registry auto-fix cho toàn bộ project.

        Với mỗi subtitle item, so sánh source JA có mention tên X không,
        và target VI có dùng canonical của X không. Nếu sai → auto-fix.
        """
        items = self.db.get_all_items(project_id)
        total = len(items)
        checked = 0
        fixed = 0
        for item in items:
            source = item.get('original_text', '')
            target = item.get('translated_text', '')
            if not source or not target:
                continue
            checked += 1
            new_target = registry.validate_translation(source, target)
            if new_target and new_target != target:
                with self.db._get_connection() as conn:
                    conn.execute(
                        "UPDATE subtitle_items SET translated_text=? WHERE id=?",
                        (new_target, item['id'])
                    )
                    conn.commit()
                fixed += 1
                logger.debug(
                    f"CharacterRegistry: sub {item['sub_index']} fixed: "
                    f"'{target[:50]}...' -> '{new_target[:50]}...'"
                )
        return {'checked': checked, 'fixed': fixed, 'total': total}

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
            if refined_stats.get('translated', 0) > 0:
                table.add_row(
                    "Batch ja_trans2",
                    f"{refined_stats['translated']} translated, "
                    f"{refined_stats['refined']} refined"
                )
            else:
                table.add_row(
                    "Batch ja_trans2 refined",
                    f"{refined_stats['refined']}/{refined_stats['total']}"
                )
        console.print(table)

    def _parse_srt(self, project_id, input_path):
        import pysrt
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"{STATUS_ICONS['error']} SRT file not found: {input_path}")
        subs = pysrt.open(input_path, encoding='utf-8')
        if not subs:
            # shortcut: phân biệt file 0 byte (Whisper fail/im lặng) vs file có header rỗng
            # để gợi ý nguyên nhân đúng thay vì chỉ báo "empty"
            size = os.path.getsize(input_path) if os.path.exists(input_path) else 0
            if size == 0:
                raise ValueError(
                    f"{STATUS_ICONS['error']} SRT file is empty (0 bytes): {input_path}\n"
                    f"  → Nguyên nhân thường gặp:\n"
                    f"    1. Whisper transcribe thất bại im lặng (model chưa tải, OOM, hoặc video không có audio track)\n"
                    f"    2. Video file hỏng/không đọc được audio stream\n"
                    f"  → Thử: ffprobe \"<video>\" để kiểm tra có audio track không"
                )
            raise ValueError(
                f"{STATUS_ICONS['error']} SRT file has no subtitle entries ({size} bytes): {input_path}\n"
                f"  → Whisper đã chạy nhưng không nhận diện được lời nói (có thể video im lặng / nhạc nền thuần)"
            )
        # Giữ newline giữa các dòng trong cùng 1 sub (Aegisub/VLC hiển thị đúng),
        # chỉ collapse space thừa trong từng dòng (không gộp các sub khác nhau).
        items = [{'index': str(s.index).lstrip('\ufeff\u200b'), 'start': str(s.start), 'end': str(s.end),
                  'text': '\n'.join(' '.join(line.split()) for line in s.text.split('\n'))}
                 for s in subs]
        if not items:
            raise ValueError(f"{STATUS_ICONS['error']} No subtitle items found in: {input_path}")

        # Whisper artifact normalizer: thay romaji/kana đơn lẻ (うん, ありがとう, 塩)
        # bằng tiếng Việt TRƯỚC khi dịch → giảm lỗi "chưa dịch" ở output.
        # Chỉ áp dụng khi source là JA (Whisper artifact là vấn đề riêng của JA).
        if self._is_japanese_source:
            try:
                from .whisper_normalizer import normalize_items
                items = normalize_items(items)
            except Exception as e:
                logger.warning(f"WhisperNormalizer failed (non-blocking): {e}")

        # Sub merger: gộp sub rỗng/dài bất thường (vd: 70s sub 塩? lặp 3 lần ở JUR-859)
        # trước khi splitter chia sub — tránh sub "chờ" kéo dài 30s+
        if self._is_japanese_source:
            try:
                from .sub_merger import merge_srt_items
                pre_merge_count = len(items)
                items = merge_srt_items(items)
                if len(items) != pre_merge_count:
                    console.print(
                        f"{STATUS_ICONS['success']} [green]Merged "
                        f"{pre_merge_count} → {len(items)} subs (removed {pre_merge_count - len(items)} empty/long)[/green]"
                    )
            except Exception as e:
                logger.warning(f"SubMerger failed (non-blocking): {e}")

        # Long cues cannot be split accurately without speech alignment. Keep
        # source timestamps and flag suspicious durations for targeted review.
        if self._is_japanese_source:
            try:
                from .sub_splitter import find_long_subs, validate_subtitle_timings
                review_threshold = self.config.get('pipeline', {}).get(
                    'timestamp_review_threshold_ms', 15000,
                )
                long_subs = find_long_subs(items, review_threshold)
                for item in long_subs:
                    logger.warning(
                        "Timestamp review: cue %s lasts %.1fs (%s --> %s): %s",
                        item.get('index'), item['duration_ms'] / 1000,
                        item['start'], item['end'], (item.get('text') or '')[:100],
                    )
                if long_subs:
                    report_path = f"{input_path}.timestamp_review.txt"
                    with open(report_path, 'w', encoding='utf-8') as report:
                        for item in long_subs:
                            report.write(
                                f"Cue {item.get('index')}: {item['start']} --> {item['end']} "
                                f"({item['duration_ms'] / 1000:.1f}s)\n"
                                f"{item.get('text', '').strip()}\n\n"
                            )
                    logger.info("Timestamp review report written: %s", report_path)
                timing_issues = validate_subtitle_timings(items)
                for issue in timing_issues:
                    logger.warning("Timestamp validation issue: %s", issue)
                if timing_issues:
                    console.print(
                        f"{STATUS_ICONS['warning']} [yellow]Found {len(timing_issues)} "
                        "invalid/overlapping timestamp issues; see log for details[/yellow]"
                    )
                if long_subs:
                    console.print(
                        f"{STATUS_ICONS['warning']} [yellow]Found {len(long_subs)} "
                        f"subtitle cues over {review_threshold / 1000:.0f}s; "
                        "preserving timestamps and logging for review[/yellow]"
                    )
            except Exception as e:
                logger.warning(f"Long timestamp cue check failed (non-blocking): {e}")

        # Compatibility normalization only: punctuation cannot reveal exact
        # speech timestamps, so the splitter preserves original cue timings.
        split_enabled = self.config.get('pipeline', {}).get('split_long_subs', True)
        if split_enabled:
            from .sub_splitter import split_srt_items
            max_dur = self.config.get('pipeline', {}).get('sub_max_duration_ms', 6000)
            original_count = len(items)
            items = split_srt_items(items, max_duration_ms=max_dur)
            if len(items) != original_count:
                console.print(
                    f"{STATUS_ICONS['success']} [green]Split "
                    f"{original_count} → {len(items)} subs "
                    f"(max {max_dur}ms)[/green]"
                )

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
            # shortcut: replace newlines trong original_text bằng space
            # để format [N] text luôn là 1 dòng, parser không bị "malformed line"
            lines = [f"[{it['sub_index']}] {(it.get('original_text') or '').replace(chr(10), ' ').replace(chr(13), ' ')}" for it in chunk]
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

            # Thu recover circuit breaker truoc khi bat dau (trong truong hop Ollama vua hoi sinh)
            if hasattr(self, 'translator') and self.translator:
                if self.translator.try_recover_circuit_breaker():
                    logger.info("Circuit breaker state: ready")

            consecutive_circuit_open = 0
            while True:
                task_info = self.db.claim_task(project_id, max_retries=self.max_retries)
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
                        self.exporter.export_incremental(project_id, project['output_srt'], project['name'])
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
                        task_info = self.db.claim_task(project_id, max_retries=self.max_retries)
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
                                self.exporter.export_incremental(project_id, project['output_srt'], project['name'])
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

        # Thu recover circuit breaker truoc (trong truong hop Ollama vua hoi sinh)
        if hasattr(self, 'translator') and self.translator:
            self.translator.try_recover_circuit_breaker()

        # Lock context reads and DB writes to prevent race conditions in parallel mode
        with self._context_lock:
            # shortcut: load items once per task (was N+1: each window re-queried full table)
            # Fix P1-4: get_all_items is expensive for long projects; reuse within a task.
            try:
                _all_items_cache = self.db.get_all_items(project_id)
            except Exception as e:
                logger.warning(f"get_all_items failed: {e}, falling back to per-call query")
                _all_items_cache = None
            context = self._get_context(
                project_id, task['start_pos'], task['end_pos'], project,
                use_translated=use_translated_history, _items=_all_items_cache,
            )
            
            # Retry loop: cho đến khi có kết quả hợp lệ hoặc hết retries
            max_window_retries = 5
            result = None
            for attempt in range(max_window_retries):
                result = self._translate_window(project_id, task, context, project)
                if result is not None:
                    break  # Thành công, thoát retry loop
                if attempt < max_window_retries - 1:
                    logger.warning(f"{STATUS_ICONS['warning']} Window {win_idx}: translation returned None, retrying ({attempt+1}/{max_window_retries})...")
                    time.sleep(1)  # Đợi 1s trước khi retry

            # Parse source lines từ task để dùng khi fail (giữ nguyên source thay vì [UNTRANSLATED])
            import re as _re
            _orig_lines = [l.strip() for l in task['original_text'].split('\n') if l.strip()]
            source_lines_fallback = []
            for line in _orig_lines:
                _m = _re.match(r'^\[(\d+)\]\s*(.*)', line)
                if _m:
                    source_lines_fallback.append(_m.group(2).strip())
                else:
                    # Strip invisible chars and retry
                    _cleaned = line.lstrip('\ufeff\u200b\u200c\u200d\u2060')
                    _m = _re.match(r'^\[(\d+)\]\s*(.*)', _cleaned)
                    if _m:
                        source_lines_fallback.append(_m.group(2).strip())
                    else:
                        source_lines_fallback.append(line)  # không drop

            if result:
                translations, source_lines = result

                # Check for empty translations - retry per-line nếu có
                if any(not t or not t.strip() for t in translations):
                    empty_indices = [i for i, t in enumerate(translations) if not t or not t.strip()]
                    logger.warning(f"{STATUS_ICONS['warning']} Window {win_idx}: {len(empty_indices)} empty translations, retrying per-line...")
                    retry_translations = self._retry_per_line(
                        [source_lines[i] for i in empty_indices],
                        project.get('source_lang', 'ja'),
                        project.get('target_lang', 'vi'),
                        self._get_glossary_for_window(task, project)
                    )
                    if retry_translations:
                        for idx, trans in zip(empty_indices, retry_translations):
                            if trans and trans.strip():
                                translations[idx] = trans

                if self.validator.validate_window_content(source_lines, translations):
                    try:
                        # ÁP DỤNG RULE PROCESSOR (character names + anti-artifacts)
                        try:
                            from .rules import get_rule_processor
                            processor = get_rule_processor('config.yaml')
                            translations, violations = processor.process_window(translations)
                            if violations:
                                err_violations = [v for v in violations if v['severity'] == 'error']
                                if err_violations:
                                    logger.info(f"Window {win_idx}: RuleProcessor fixed {len(violations)} issues ({len(err_violations)} errors)")
                        except Exception as e:
                            logger.warning(f"RuleProcessor failed (non-blocking): {e}")

                        self.db.commit_window(project_id, win_id, start_sub, end_sub, translations)
                        self._save_to_tm(project, source_lines, translations)
                        return True
                    except Exception as e:
                        logger.error(f"{STATUS_ICONS['error']} commit_window failed: {e}")
                        self.db.mark_task_failed(win_id, str(e), retry_count_increment=True)
                        return False
                else:
                    # Validation fail nhưng LLM đã trả output hợp lệ
                    # Commit trực tiếp — KHÔNG retry (model + validator cùng fail → retry vô hạn)
                    logger.warning(f"{STATUS_ICONS['warning']} Window {win_idx} failed validation, committing as-is")
                    try:
                        # Van ap dung RuleProcessor truoc khi commit
                        try:
                            from .rules import get_rule_processor
                            processor = get_rule_processor('config.yaml')
                            translations, violations = processor.process_window(translations)
                            if violations:
                                logger.info(f"Window {win_idx}: RuleProcessor fixed {len(violations)} issues (after validation fail)")
                        except Exception as e:
                            logger.warning(f"RuleProcessor failed (non-blocking): {e}")

                        self.db.commit_window(project_id, win_id, start_sub, end_sub, translations)
                        self._save_to_tm(project, source_lines, translations)
                        return True
                    except Exception as e:
                        logger.error(f"{STATUS_ICONS['error']} commit_window (validation-fallback) failed: {e}")
                        return False
            else:
                # LLM fail hoàn toàn sau khi đã retry 3 lần → commit original text
                logger.warning(f"{STATUS_ICONS['warning']} Window {win_idx}: LLM returned None after {max_window_retries} retries, committing original text")
                try:
                    self.db.commit_window(project_id, win_id, start_sub, end_sub, source_lines_fallback)
                    return True
                except Exception as e:
                    logger.error(f"{STATUS_ICONS['error']} commit original text failed: {e}")
                    return False

    def _translate_window(self, project_id, task, context, project):
        orig_lines_with_ids = [l.strip() for l in task['original_text'].split('\n') if l.strip()]
        sub_indices = []
        source_lines = []
        for line in orig_lines_with_ids:
            # Strip BOM and other invisible chars that cause regex to fail
            cleaned = line.lstrip('\ufeff\u200b\u200c\u200d\u2060')
            m = re.match(r'^\[(\d+)\]\s*(.*)', cleaned)
            if m:
                sub_indices.append(int(m.group(1)))
                source_lines.append(m.group(2).strip())
            else:
                # Second try: strip ALL invisible chars and retry once
                fully_cleaned = cleaned.strip('\ufeff\u200b\u200c\u200d\u2060\u00a0\t')
                m = re.match(r'^\[(\d+)\]\s*(.*)', fully_cleaned)
                if m:
                    sub_indices.append(int(m.group(1)))
                    source_lines.append(m.group(2).strip())
                else:
                    # Fail only if truly unparseable after cleanup
                    logger.error(
                        f"{STATUS_ICONS['error']} Window {task['window_index']}: "
                        f"malformed line, cannot parse: {line[:80]!r}"
                    )
                    return None

        if not sub_indices:
            logger.error(f"Window {task['window_index']}: no valid lines after parse")
            return None

        # shortcut: store parsed lines back to task so _build_prompt can use them
        # instead of re-parsing (also fixes P0-1: count real subtitle count, not raw lines)
        task['_sub_indices'] = sub_indices
        task['_source_lines'] = source_lines

        # P0-fix uncen literal: uncen mode in default (auto) skips polish + qa.
        # Rationale: polish pass may soften explicit content which violates uncen contract.
        # User opt-in via config.translation.literal_mode can still force 3-pass.
        _trans_mode = (self.config.get('translation', {}) or {}).get('mode', 'uncen')
        is_uncen = (_trans_mode or '').lower() == 'uncen'
        _literal_mode_cfg = self.config.get('translation', {}).get('literal_mode', None)
        if _literal_mode_cfg is None:
            # Auto: uncen → True (keep raw), others → False (full 3-pass)
            skip_polish_for_uncen = is_uncen
        else:
            skip_polish_for_uncen = bool(_literal_mode_cfg)
        if is_uncen and skip_polish_for_uncen:
            logger.debug(f"Window {task['window_index']}: uncen literal mode → skip polish+qa")

        # shortcut: dòng ngắn (<=15 chars, không có kanji) → dịch per-line thẳng
        # Giảm tải LLM và [UNTRANSLATED] cho greeting/filler/short kana utterances
        def _is_short_simple_line(s):
            s = s.strip()
            if len(s) > 15:
                return False
            # Có kanji (CJK) HOẶC hiragana/katakana → không đơn giản
            # Bug fix: trước đây chỉ check \u4e00-\u9fff (CJK ideographs),
            # nhưng bỏ sót hiragana (\u3040-\u309f) và katakana (\u30a0-\u30ff).
            # Hầu hết JA text đều có kana → false positive, trigger _retry_per_line
            # và output rỗng / raw prompt.
            if any('\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff'
                   or '\u4e00' <= c <= '\u9fff' for c in s):
                return False
            # Whisper noise đã filter
            return True

        if all(_is_short_simple_line(s) for s in source_lines if s):
            src_lang = self._source_lang if hasattr(self, '_source_lang') else 'ja'
            tgt_lang = 'vi'
            glossary = self._get_glossary(project_id) if self.enable_glossary else []
            logger.debug(f"Window {task['window_index']}: short lines → per-line translation")
            result = self._retry_per_line(source_lines, src_lang, tgt_lang, glossary)
            if result and any(r.strip() for r in result):
                return result, source_lines

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
            if translations is None or not self.validator.validate_window_content(source_lines, translations):
                logger.warning(f"Window {task['window_index']}: fallback validation failed, trying per-line retry...")
                # Pass 3: retry từng dòng riêng biệt với prompt đơn giản nhất
                translations = self._retry_per_line(source_lines, src_lang, tgt_lang, glossary)
                if translations is None:
                    logger.error(f"Window {task['window_index']}: all passes failed")
                    return None
                if not self.validator.validate_window_content(source_lines, translations):
                    logger.warning(f"Window {task['window_index']}: per-line retry also failed validation, using as-is")
            logger.info(f"Window {task['window_index']}: fallback pass OK")
            return translations, source_lines
        except Exception as e:
            logger.error(f"Window {task['window_index']}: fallback exception: {e}, trying per-line retry...")
            translations = self._retry_per_line(source_lines, src_lang, tgt_lang, glossary)
            if translations is None:
                return None
            return translations, source_lines

    def _retry_per_line(self, source_lines, src_lang, tgt_lang, glossary):
        """Retry dịch từng dòng riêng biệt với prompt cực kỳ đơn giản.
        Retry tối đa 3 lần cho mỗi dòng, nếu vẫn fail thì trả về empty string.
        """
        translations = []
        glossary_block = ""
        if glossary:
            lines = [f"- {t['source_term']} -> {t['target_term']}" for t in glossary]
            glossary_block = "Use these terms:\n" + "\n".join(lines) + "\n"

        for line in source_lines:
            success = False
            for attempt in range(3):
                prompt = (
                    f"Translate to Vietnamese. Output only the translation, no explanation.\n"
                    f"{glossary_block}"
                    f"Japanese: {line}\n"
                    f"Vietnamese:"
                )
                try:
                    raw = self.translator.generate(prompt, temperature=0.1, num_predict=256)
                    trans = raw.strip().split('\n')[0].strip()
                    # Loại bỏ các marker không mong muốn
                    trans = trans.replace('[UNTRANSLATED]', '').strip()
                    if trans and len(trans) > 0 and trans != line:
                        translations.append(trans)
                        success = True
                        break
                except Exception as e:
                    logger.warning(f"Per-line retry {attempt+1} failed for '{line[:30]}...': {e}")
                    time.sleep(1)

            if not success:
                logger.warning(f"All retries failed for line: {line[:30]}...")
                translations.append('')  # để pipeline xử lý tiếp

        return translations if translations else None

    def _get_glossary_for_window(self, task, project):
        """Lấy glossary terms cho window hiện tại (dùng cho retry per-line)."""
        # Trả về glossary terms dạng list of dicts
        # Tìm trong project config nếu có
        if hasattr(project, 'get') and project.get('glossary'):
            return project['glossary']
        return None

    def _translate_window_ja(self, task, source_lines, sub_indices, glossary, project_id):
        """Luồng dịch riêng cho tiếng Nhật: 3-pass (literal → polish → qa)
        + 2-tier context (inject từ Worker A) + Worker C scene-review.

        Trả về (translations, source_lines) giống _translate_window thường.

        Config flag (config.translation.literal_mode):
          - False (mặc định): chạy 3-pass đầy đủ
          - True: chỉ chạy literal pass (giữ sát nghĩa gốc, dùng cho JAV cần dịch
            trung thực hoặc khi user muốn kiểm tra chất lượng dịch raw)
        """
        from .ja_translator import JaTranslator  # noqa: F401
        import json

        # Literal mode flag: skip polish + qa, chỉ giữ literal
        # Mặc định: True cho uncen, False cho mode khác
        # (config.translation.literal_mode override nếu user set)
        _trans_mode = (self.config.get('translation', {}) or {}).get('mode', 'uncen')
        literal_mode_cfg = self.config.get('translation', {}).get('literal_mode', None)
        if literal_mode_cfg is None:
            # Auto: uncen → True (giữ nghĩa gốc, không polish làm mượt)
            literal_mode = (_trans_mode or '').lower() == 'uncen'
        else:
            literal_mode = bool(literal_mode_cfg)

        # Lấy context cho window hiện tại (2-tier)
        context_summary = self._get_ja_context_for_window(task, project_id)

        # Pass 1: literal
        rough = self.ja_translator.literal_translate(
            source_lines, glossary_terms=glossary,
        )
        # DEBUG: log raw rough to isolate mapping issues
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"Window {task['window_index']}: rough translations:\n" +
                "\n".join(f"  [{j}] {source_lines[j][:30]!r} -> {(rough[j] or '')[:40]!r}"
                          for j in range(len(source_lines)))
            )
        # Detect raw JA in rough output (off-by-one shift symptom)
        if rough:
            for j, (orig, trans) in enumerate(zip(source_lines, rough)):
                if orig and trans and orig.strip() == trans.strip():
                    logger.warning(
                        f"Window {task['window_index']}[{j}]: RAW_JA_ECHO "
                        f"source={orig[:40]!r} == rough={trans[:40]!r}"
                    )
        if rough is None:
            logger.warning(f"Window {task['window_index']}: JA literal pass failed")
            return None
        if len(rough) != len(source_lines) or any(r is None for r in rough):
            # shortcut: không pad bằng source Nhật, fail cả window để retry.
            # <5s overhead, tránh bug "giữ nguyên bản gốc chưa dịch"
            logger.error(
                f"Window {task['window_index']}: JA literal bad output "
                f"(len={len(rough)}/{len(source_lines)}, none={sum(1 for r in rough if r is None)}), failing"
            )
            return None

        # Literal mode: return rough ngay, skip polish + qa
        if literal_mode:
            logger.info(
                f"Window {task['window_index']}: JA literal-mode (1-pass, no polish)"
            )
            return rough, source_lines

        # Pass 2: polish (có inject context nếu có)
        polished = self.ja_translator.polish_translate(
            source_lines, rough, glossary_terms=glossary,
            context_summary=context_summary,
        )
        if len(polished) != len(source_lines):
            logger.error(
                f"Window {task['window_index']}: JA polish length mismatch "
                f"({len(polished)} vs {len(source_lines)}), falling back to literal"
            )
            polished = rough
            if len(polished) != len(source_lines):
                logger.error(
                    f"Window {task['window_index']}: JA literal also length mismatched"
                )
                return None

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

    def _get_context(self, project_id, start_pos, end_pos, project, use_translated=False, _items=None):
        # shortcut: accept pre-loaded items to avoid N+1 full-table queries (P1-4)
        items = _items if _items is not None else self.db.get_all_items(project_id)
        hist_size = project['history_size']
        fut_size = project['future_size']
        hist_start = max(0, start_pos - hist_size)
        hist_items = items[hist_start:start_pos]
        hist_lines = []
        # P1-fix: when using translated history, also include source JA in parens
        # so LLM can cross-reference. Format: "[5] {translation} (JA: {original})".
        # For untranslated rows fall back to source-only. This reduces drift
        # because LLM sees what the JA original said, not just the (potentially
        # hallucinated) translation.
        for it in hist_items:
            translated = (it.get('translated_text') or '').strip()
            original = (it.get('original_text') or '').strip()
            if use_translated and translated and original and translated != original:
                hist_lines.append(f"[{it['sub_index']}] {translated} (JA: {original})")
            elif use_translated and translated:
                hist_lines.append(f"[{it['sub_index']}] {translated}")
            else:
                hist_lines.append(f"[{it['sub_index']}] {original}")
        history = "\n".join(hist_lines) if hist_lines else ""

        fut_end = min(len(items), end_pos + fut_size + 1)
        fut_items = items[end_pos+1:fut_end]
        future = "\n".join([f"[{it['sub_index']}] {(it.get('original_text') or '').replace(chr(10), ' ').replace(chr(13), ' ')}" for it in fut_items]) if fut_items else ""

        # 2-tier: Inject context từ Worker A (JaContextAnalyzer)
        ctx_block = self._get_context_block(project_id, start_pos, end_pos)

        return {"history": history, "future": future, "context_block": ctx_block}

    def _get_context_block(self, project_id, start_pos, end_pos) -> str:
        """Lấy context block (nhan vat, pronouns, setting) tu JaContextAnalyzer.
        Worker A da chay truoc va luu vao DB.

        Query NHIEU context-windows gan nhau (1 hoac 2) de cover translation-window
        co the span 2 context-windows lien ke.
        """
        if not self.ja_2tier_enabled or self.ja_context_analyzer is None:
            return ""

        try:
            ctx_size = self.ja_context_analyzer.context_window_size
            first_idx = start_pos // ctx_size
            last_idx = end_pos // ctx_size

            all_block_parts = []
            for ctx_idx in range(first_idx, last_idx + 1):
                context = self.db.get_context_window(project_id, ctx_idx)
                if not context or context.get('status') != 'completed':
                    continue
                parts = self._format_context(context)
                if parts:
                    all_block_parts.append(f"--- Context phan {ctx_idx + 1} ---")
                    all_block_parts.extend(parts)

            if not all_block_parts:
                return ""
            return "THONG TIN NHAN VAT (tu Worker A - phan tich ngu canh):\n" + "\n".join(all_block_parts) + "\n"
        except Exception as e:
            logger.debug(f"Context block lookup failed (non-blocking): {e}")
            return ""

    def _format_context(self, context) -> list:
        """Helper: format 1 context dict thanh cac block_parts."""
        block_parts = []
        try:
            speakers = json.loads(context.get('speakers_json', '[]') or '[]')
        except Exception:
            speakers = []
        try:
            pronouns_map = json.loads(context.get('pronouns_map', '{}') or '{}')
        except Exception:
            pronouns_map = {}
        tone = context.get('tone', 'unknown') or 'unknown'
        setting = context.get('setting', 'unknown') or 'unknown'
        summary = context.get('summary', '') or ''

        if speakers:
            block_parts.append(f"- Nhan vat: {', '.join(speakers)}")
        if pronouns_map:
            lines_pp = []
            for pair, p in pronouns_map.items():
                if isinstance(p, dict):
                    s = p.get('self', '?')
                    o = p.get('other', '?')
                    if s and o:
                        lines_pp.append(f"  {pair}: {s} goi {o}")
            if lines_pp:
                block_parts.append("- Xung ho (giua cac nhan vat):")
                block_parts.extend(lines_pp)
        if tone and tone != 'unknown':
            tone_vn = {
                'formal': 'trang trong (anh/chi)',
                'casual': 'than mat (toi/ban)',
                'intimate': 'than mat (anh/em)',
                'mixed': 'hon hop',
            }.get(tone, tone)
            block_parts.append(f"- Giong noi: {tone_vn}")
        if setting and setting != 'unknown':
            block_parts.append(f"- Boi canh: {setting}")
        if summary:
            block_parts.append(f"- Tom tat: {summary}")
        return block_parts

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

        # shortcut: chỉ inject glossary terms LIÊN QUAN tới window hiện tại
        # Source lines có trong `task['_source_lines']` (đã parse ở _translate_window).
        # Match bằng substring: term JA có mặt trong bất kỳ source line → relevant
        # Giảm prompt size khi glossary có 100+ terms (mỗi term ~50 chars → 5KB/term).
        source_lines = task.get('_source_lines') or []
        glossary_block = ""
        if glossary and source_lines:
            relevant = self._filter_glossary_for_window(glossary, source_lines)
            if relevant:
                glossary_lines = []
                for t in relevant:
                    term_line = f"- {t['source_term']} -> {t['target_term']}"
                    if t.get('context_hint'):
                        term_line += f" (context: {t['context_hint']})"
                    glossary_lines.append(term_line)
                glossary_block = p['glossary_hdr'] + "\n" + "\n".join(glossary_lines) + "\n"
        elif glossary:
            # fallback: không có source_lines → dùng tất cả (giữ behavior cũ)
            glossary_lines = []
            for t in glossary:
                term_line = f"- {t['source_term']} -> {t['target_term']}"
                if t.get('context_hint'):
                    term_line += f" (context: {t['context_hint']})"
                glossary_lines.append(term_line)
            glossary_block = p['glossary_hdr'] + "\n" + "\n".join(glossary_lines) + "\n"

        # Inject character_names (tên riêng) vào prompt để LLM biết canonical
        # VD: 匠 = Takumi (KHÔNG phải "Craft"), あゆみ = Ayumi (KHÔNG phải "Aya")
        # Đây là cách duy nhất để LLM dùng tên đúng, vì glossary section chỉ cover
        # các từ vựng chung.
        character_block = self._build_character_names_block()

        # shortcut: count parsed subtitle lines, not raw lines (fix P0-1)
        # raw 'original_text' may have multi-line text or empty lines; only sub_indices
        # accurately represent the number of translations the LLM must produce.
        if task.get('_sub_indices'):
            num_lines = len(task['_sub_indices'])
        else:
            num_lines = len([l for l in task['original_text'].split('\n') if l.strip()])

        # 2-tier: Inject context block tu Worker A (neu co)
        context_block = context.get('context_block', '') or ''

        return f"""{p['system'].format(src=src_lang, tgt=tgt_lang)}

{glossary_block}{character_block}{context_block}{p['history_hdr']}
{context['history']}

{p['current_hdr']}
{task['original_text']}

{p['next_hdr']}
{context['future']}

{p['rules'].format(n=num_lines)}

### {p['output']}"""

    def _filter_glossary_for_window(self, glossary, source_lines):
        """Lọc glossary chỉ giữ terms xuất hiện trong source_lines của window.

        Returns list of term dicts giữ nguyên shape để truyền vào prompt.
        Empty list nếu không match term nào (caller sẽ không tạo glossary_block).

        P0-fix: use word-boundary regex match instead of raw `in` substring.
        Substring match produces false positives like `masu` matching `emasculate`
        or `summarize`. We must also handle JA characters (no \b in Python regex
        with Unicode), so we wrap terms with explicit non-word guards.
        """
        if not glossary or not source_lines:
            return glossary or []

        # Gộp source_lines thành 1 string để match nhanh
        joined = '\n'.join(source_lines)

        # P0-fix: helper to test presence with word boundary that works for both
        # Latin (\w) and CJK (we treat CJK char boundaries by requiring the
        # surrounding char to NOT be a CJK ideograph). Japanese particles like
        # ね, よ, は are SINGLE CJK char — need explicit boundary check.
        def _is_cjk(ch: str) -> bool:
            return bool(ch) and (
                ch.isalnum()
                or '\u3040' <= ch <= '\u309f'
                or '\u30a0' <= ch <= '\u30ff'
                or '\u4e00' <= ch <= '\u9fff'
            )

        def _term_present(term: str) -> bool:
            if not term:
                return False
            # Term thuần CJK/kana: tiếng Nhật viết liền không có word delimiter,
            # nên char trước CJK là bình thường (その一部 → 一部 vẫn là match đúng).
            # Áp word-boundary ở đây chỉ khiến glossary tiếng Nhật không bao giờ match.
            if _is_cjk(term[0]):
                return term in joined
            idx = joined.find(term)
            if idx < 0:
                return False
            # Check preceding char to avoid substring match like "masu" in "summarize"
            if idx > 0 and _is_cjk(joined[idx - 1]):
                # Try next occurrence
                idx = joined.find(term, idx + 1)
                if idx < 0:
                    return False
                if idx > 0 and _is_cjk(joined[idx - 1]):
                    return False
            return True

        relevant = []
        for term in glossary:
            src_term = term.get('source_term', '').strip()
            if src_term and _term_present(src_term):
                relevant.append(term)

        return relevant

    def _build_character_names_block(self) -> str:
        """Build block 'TEN RIENG' từ config.character_names để inject vào prompt.

        Format mỗi entry:
          - 匠 -> Takumi (tên riêng nam, KHÔNG dịch)
        LLM đọc block này sẽ biết dùng canonical thay vì "sáng tạo" tên (Craft/Aya...).
        """
        char_names = self.config.get('character_names', [])
        if not char_names:
            return ""
        lines = []
        for entry in char_names:
            canonical = entry.get('canonical', '').strip()
            aliases = entry.get('aliases', []) or []
            if not canonical or not aliases:
                continue
            # Dùng alias JA đầu tiên làm source_term (nếu có), nếu không thì canonical
            ja_aliases = [a for a in aliases
                          if re.match(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]', a)]
            source = ja_aliases[0] if ja_aliases else canonical
            context = entry.get('context', '').strip()
            line = f"- {source} -> {canonical}"
            if context:
                line += f" ({context})"
            lines.append(line)
        if not lines:
            return ""
        return "TEN RIENG BAT BUOC (giu nguyen, KHONG tu y dich/sang tao):\n" + "\n".join(lines) + "\n"

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
                    f"[{it['sub_index']}] {(it.get('original_text') or '').replace(chr(10), ' ').replace(chr(13), ' ')}"
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
                    # P1-7 fix: fail-fast on length mismatch (was padding with JA source,
                    # which mixed Japanese into Vietnamese subtitles). Same fail-fast
                    # principle as literal_translate on line 1500.
                    if polished is None or len(polished) != len(source_lines):
                        logger.warning(
                            f"Recovery[{dw['window_index']}]: JA polish invalid "
                            f"({None if polished is None else len(polished)} vs "
                            f"{len(source_lines)}), falling back to rough"
                        )
                        polished = rough if (rough and len(rough) == len(source_lines)) else None
                        if polished is None:
                            logger.error(f"Recovery[{dw['window_index']}]: no valid translation, skipping")
                            continue
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
                        glossary_terms=glossary,
                    )
                    if polished is None:
                        polished = raw_trans  # fallback giữ nguyên rough

                # Validate & commit
                raw_fallback = raw_trans if not self.ja_translator else rough
                if not self.validator.validate_window_content(source_lines, polished):
                    logger.warning(f"Recovery[{dw['window_index']}]: polished content failed validation, trying raw")
                    if not self.validator.validate_window_content(source_lines, raw_fallback):
                        # Validation liên tục fail → commit raw translation để không mất dữ liệu
                        logger.warning(f"Recovery[{dw['window_index']}]: all validation failed, committing raw translation")
                        polished = raw_fallback
                        if not self.validator.validate_window_content(source_lines, polished):
                            # Raw cũng fail validation nghiêm trọng, vẫn commit để có output
                            logger.error(f"Recovery[{dw['window_index']}]: raw also failed validation, committing as-is")

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
