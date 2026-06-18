import yaml
import logging
import os
import sys
import argparse
from rich.console import Console
from rich.table import Table
from core.database import Database
from core.pipeline import TranslationPipeline
from core.transcriber import WhisperTranscriber

__version__ = "1.2"

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

LANGUAGES = {
    'ja': {'name': 'Japanese', 'display': 'Tieng Nhat (Japanese)', 'whisper_code': 'ja'},
    'ko': {'name': 'Korean', 'display': 'Tieng Han (Korean)', 'whisper_code': 'ko'},
    'zh': {'name': 'Chinese', 'display': 'Tieng Trung (Chinese)', 'whisper_code': 'zh'},
    'en': {'name': 'English', 'display': 'Tieng Anh (English)', 'whisper_code': 'en'},
}

TARGET_LANGUAGES = {
    'vi': {'name': 'Vietnamese', 'display': 'Tieng Viet (Vietnamese)'},
    'en': {'name': 'English', 'display': 'Tieng Anh (English)'},
    'zh': {'name': 'Chinese', 'display': 'Tieng Trung (Chinese)'},
    'ja': {'name': 'Japanese', 'display': 'Tieng Nhat (Japanese)'},
    'ko': {'name': 'Korean', 'display': 'Tieng Han (Korean)'},
}

WINDOW_PRESETS = {
    'ja': {'size': 6, 'history': 12, 'future': 4},
    'ko': {'size': 8, 'history': 8, 'future': 2},
    'zh': {'size': 10, 'history': 12, 'future': 4},
    'en': {'size': 10, 'history': 10, 'future': 3},
}

TRANSLATION_MODES = {
    'default': {
        'name': 'default',
        'display': 'Mac dinh (hunyuan-mt-abliterated)',
        'description': 'Che do dich thong thuong',
        'icon': '[STD]'
    },
    'uncen': {
        'name': 'uncen',
        'display': 'Uncen (qwen3-abliterated)',
        'description': 'Ho tro noi dung nguoi lon, kieuu dam',
        'icon': '[+18]'
    }
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('subtitle_translator.log', encoding='utf-8', mode='a'),
    ]
)
logger = logging.getLogger(__name__)


def get_lang_code(lang_name: str) -> str:
    """Lay ma ngon ngu tu ten."""
    for code, info in LANGUAGES.items():
        if info['name'].lower() == lang_name.lower():
            return code
    for code, info in TARGET_LANGUAGES.items():
        if info['name'].lower() == lang_name.lower():
            return code
    return 'en'


def get_mode_code(mode_name: str) -> str:
    """Lay ma che do dich tu ten."""
    for code, info in TRANSLATION_MODES.items():
        if info['name'].lower() == mode_name.lower():
            return code
    return 'default'


def is_video_file(filepath):
    """Kiem tra duoi file co phai video khong."""
    video_exts = ('.mp4', '.mkv', '.mov', '.avi', '.webm')
    return filepath.lower().endswith(video_exts)


def _file_picker_ask(kind: str, default_name: str = "output.srt"):
    """
    Mo file picker (kind='open' hoac 'save'). Dam bao cleanup Tk root de tranh leak.
    """
    import tkinter as tk
    from tkinter import filedialog
    root = None
    try:
        try:
            root = tk._default_root
            if root is None:
                root = tk.Tk()
                root.withdraw()
        except AttributeError:
            root = tk.Tk()
            root.withdraw()
        root.attributes('-topmost', True)
        if kind == 'open':
            file_path = filedialog.askopenfilename(
                title="Chon file Input (Video hoac SRT)",
                filetypes=[
                    ("Video files", "*.mp4 *.mkv *.mov *.avi *.webm"),
                    ("SRT files", "*.srt"),
                    ("All files", "*.*")
                ]
            )
        else:
            file_path = filedialog.asksaveasfilename(
                title="Chon duong dan file Output",
                defaultextension=".srt",
                filetypes=[("SRT files", "*.srt"), ("All files", "*.*")],
                initialfile=default_name
            )
        return file_path if file_path else None
    except Exception as e:
        logger.error(f"GUI file picker failed: {e}")
        return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def select_input_file_gui():
    """Mo file picker cho input."""
    return _file_picker_ask('open')


def select_output_file_gui(default_name="output.srt"):
    """Mo file picker cho output."""
    return _file_picker_ask('save', default_name)


def load_config():
    """Doc cau hinh tu config.yaml."""
    if os.path.exists('config.yaml'):
        with open('config.yaml', 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}


def save_config(config):
    """Luu cau hinh vao config.yaml."""
    with open('config.yaml', 'w', encoding='utf-8') as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def print_header():
    """In header cua chuong trinh."""
    console.print("\n[bold cyan]" + "=" * 58 + "[/bold cyan]")
    console.print(f"[bold cyan]        SUBTITLE TRANSLATOR v{__version__}[/bold cyan]")
    console.print("[bold cyan]" + "=" * 58 + "[/bold cyan]\n")


def get_source_lang_key(config) -> str:
    """Lay ma ngon ngu nguon hien tai."""
    src = config.get('project', {}).get('source_lang', 'Japanese')
    return get_lang_code(src)


def get_target_lang_key(config) -> str:
    """Lay ma ngon ngu dich hien tai."""
    tgt = config.get('project', {}).get('target_lang', 'Vietnamese')
    return get_lang_code(tgt)


def get_mode_key(config) -> str:
    """Lay ma che do dich hien tai."""
    mode = config.get('translation', {}).get('mode', 'default')
    return mode


def print_main_menu(config):
    """In menu chinh."""
    console.print("[bold]Menu chinh:[/bold]\n")

    input_path = config.get('project', {}).get('input_srt', 'Chua dat')
    output_path = config.get('project', {}).get('output_srt', 'Chua dat')
    project_name = config.get('project', {}).get('name', 'my_movie')

    src_lang = config.get('project', {}).get('source_lang', 'Japanese')
    tgt_lang = config.get('project', {}).get('target_lang', 'Vietnamese')
    win = config.get('window', {})
    win_size = win.get('size', 6)

    src_display = LANGUAGES.get(get_source_lang_key(config), {}).get('display', src_lang)
    tgt_display = TARGET_LANGUAGES.get(get_target_lang_key(config), {}).get('display', tgt_lang)

    mode_key = get_mode_key(config)
    mode_info = TRANSLATION_MODES.get(mode_key, TRANSLATION_MODES['default'])
    mode_display = f"{mode_info['icon']} {mode_info['display']}"

    console.print(f"  [1] Chon file Input          : [yellow]{truncate_path(input_path)}[/yellow]")
    console.print(f"  [2] Chon folder Output       : [yellow]{truncate_path(output_path)}[/yellow]")
    console.print(f"  [3] Ten Project              : [yellow]{project_name}[/yellow]")
    console.print(f"  [4] Chon ngon ngu           : [cyan]{src_display}[/cyan] -> [green]{tgt_display}[/green]")
    console.print(f"  [5] Che do dich             : {mode_display}")
    console.print(f"  [6] Chinh sua Glossary")
    console.print(f"  [7] [green]Bat dau dich[/green]")
    console.print(f"  [0] [red]Thoat[/red]")
    console.print()

    console.print(f"[dim]Window preset: size={win_size}, history={win.get('history', 12)}, future={win.get('future', 4)}[/dim]")


def truncate_path(path, max_len=40):
    """Cat ngan duong dan neu qua dai."""
    if not path or len(path) <= max_len:
        return path
    return "..." + path[-(max_len-3):]


def get_input_path_interactive(config):
    """Lay input path tu nguoi dung."""
    console.print(f"\n{STATUS_ICONS['start']} [cyan]Chon file Input[/cyan]")
    console.print("  [1] Mo file picker (Explorer)")
    console.print("  [2] Nhap duong dan thu cong")
    console.print("  [0] Quay lai")

    choice = input("\nLua chon: ").strip()

    if choice == '1':
        path = select_input_file_gui()
        if path:
            if not os.path.exists(path):
                console.print(f"{STATUS_ICONS['error']} [red]File not found:[/red] {path}")
            else:
                config.setdefault('project', {})['input_srt'] = path
                save_config(config)
                console.print(f"{STATUS_ICONS['success']} [green]Da chon:[/green] {path}")
        else:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Da huy[/yellow]")
    elif choice == '2':
        path = input("Nhap duong dan file: ").strip().strip('"')
        if path:
            if not os.path.exists(path):
                console.print(f"{STATUS_ICONS['error']} [red]File not found:[/red] {path}")
            else:
                config.setdefault('project', {})['input_srt'] = path
                save_config(config)
                console.print(f"{STATUS_ICONS['success']} [green]Da luu:[/green] {path}")
    console.print()


def select_output_folder_gui(default_name="output_folder"):
    """Mo folder picker cho output."""
    import tkinter as tk
    from tkinter import filedialog
    root = None
    try:
        try:
            root = tk._default_root
            if root is None:
                root = tk.Tk()
                root.withdraw()
        except AttributeError:
            root = tk.Tk()
            root.withdraw()
        root.attributes('-topmost', True)
        folder_path = filedialog.askdirectory(
            title="Chon folder Output",
            mustexist=False,
        )
        return folder_path if folder_path else None
    except Exception as e:
        logger.error(f"GUI folder picker failed: {e}")
        return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def get_output_path_interactive(config):
    """Chon folder output (ten file se duoc tu dat theo project name)."""
    console.print(f"\n{STATUS_ICONS['start']} [cyan]Chon folder Output[/cyan]")
    console.print("  [1] Mo folder picker (Explorer)")
    console.print("  [2] Nhap duong dan thu cong")
    console.print("  [0] Quay lai")

    choice = input("\nLua chon: ").strip()

    if choice == '1':
        path = select_output_folder_gui()
        if path:
            # Luu folder path; ten file se duoc pipeline gan theo project name
            config.setdefault('project', {})['output_srt'] = path
            save_config(config)
            console.print(f"{STATUS_ICONS['success']} [green]Da chon folder:[/green] {path}")
        else:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Da huy[/yellow]")
    elif choice == '2':
        path = input("Nhap duong dan folder output: ").strip().strip('"')
        if path:
            config.setdefault('project', {})['output_srt'] = path
            save_config(config)
            console.print(f"{STATUS_ICONS['success']} [green]Da luu:[/green] {path}")
    console.print()


def edit_project_name(config):
    """Sua ten project."""
    console.print(f"\n{STATUS_ICONS['start']} [cyan]Chinh sua ten Project[/cyan]")
    current = config.get('project', {}).get('name', 'my_movie')
    console.print(f"Ten hien tai: [yellow]{current}[/yellow]")
    new_name = input("Ten moi (Enter de giu nguyen): ").strip()

    if new_name:
        config.setdefault('project', {})['name'] = new_name
        save_config(config)
        console.print(f"{STATUS_ICONS['success']} [green]Da luu:[/green] {new_name}")
    console.print()


def edit_languages(config):
    """Chon ngon ngu va tu dong dat window."""
    while True:
        console.print(f"\n{STATUS_ICONS['start']} [cyan]Chon Ngon Ngu[/cyan]\n")

        current_src = config.get('project', {}).get('source_lang', 'Japanese')
        current_tgt = config.get('project', {}).get('target_lang', 'Vietnamese')
        src_key = get_source_lang_key(config)
        tgt_key = get_target_lang_key(config)

        console.print("[bold]Ngon ngu nguon (Source):[/bold]")
        for i, (code, info) in enumerate(LANGUAGES.items(), 1):
            marker = " <=" if code == src_key else ""
            console.print(f"  [{i}] {info['display']}{marker}")
        console.print("  [0] Quay lai")

        console.print(f"\n[bold]Ngon ngu dich (Target):[/bold]")
        console.print(f"  Hien tai: [green]{TARGET_LANGUAGES.get(tgt_key, {}).get('display', current_tgt)}[/green]")
        for i, (code, info) in enumerate(TARGET_LANGUAGES.items(), 1):
            if code != src_key:
                marker = " <=" if code == tgt_key else ""
                console.print(f"  [{i}] {info['display']}{marker}")
        console.print("  [0] Quay lai")

        console.print("\n[1] Chon ngon ngu nguon")
        console.print("[2] Chon ngon ngu dich")
        console.print("[0] Quay lai")

        choice = input("\nLua chon: ").strip()

        if choice == '1':
            console.print("\nChon ngon ngu nguon:")
            for i, (code, info) in enumerate(LANGUAGES.items(), 1):
                console.print(f"  [{i}] {info['display']}")
            src_choice = input("Lua chon: ").strip()
            try:
                idx = int(src_choice) - 1
                codes = list(LANGUAGES.keys())
                if 0 <= idx < len(codes):
                    code = codes[idx]
                    config['project']['source_lang'] = LANGUAGES[code]['name']
                    config['whisper'] = config.get('whisper', {})
                    config['whisper']['language'] = LANGUAGES[code]['whisper_code']
                    _apply_window_preset(config, code)
                    save_config(config)
                    console.print(f"{STATUS_ICONS['success']} [green]Da chon:[/green] {LANGUAGES[code]['display']}")
            except ValueError:
                console.print(f"{STATUS_ICONS['error']} [red]Lua chon khong hop le[/red]")
        elif choice == '2':
            console.print("\nChon ngon ngu dich:")
            for i, (code, info) in enumerate(TARGET_LANGUAGES.items(), 1):
                if code != src_key:
                    console.print(f"  [{i}] {info['display']}")
            tgt_choice = input("Lua chon: ").strip()
            try:
                idx = int(tgt_choice) - 1
                codes = [c for c in TARGET_LANGUAGES.keys() if c != src_key]
                if 0 <= idx < len(codes):
                    code = codes[idx]
                    config['project']['target_lang'] = TARGET_LANGUAGES[code]['name']
                    save_config(config)
                    console.print(f"{STATUS_ICONS['success']} [green]Da chon:[/green] {TARGET_LANGUAGES[code]['display']}")
            except ValueError:
                console.print(f"{STATUS_ICONS['error']} [red]Lua chon khong hop le[/red]")
        elif choice == '0':
            break
    console.print()


def edit_translation_mode(config):
    """Chon che do dich (default/uncen)."""
    console.print(f"\n{STATUS_ICONS['start']} [cyan]Chon Che Do Dich[/cyan]\n")

    current_mode = get_mode_key(config)

    for i, (code, info) in enumerate(TRANSLATION_MODES.items(), 1):
        marker = " <=" if code == current_mode else ""
        console.print(f"  [{i}] {info['icon']} {info['display']}")
        console.print(f"      {info['description']}{marker}")

    console.print("\n[dim]Luu y: Che do Uncen ho tro dich cac noi dung nguoi lon, kieuu dam.[/dim]")

    choice = input("\nLua chon: ").strip()
    try:
        idx = int(choice) - 1
        codes = list(TRANSLATION_MODES.keys())
        if 0 <= idx < len(codes):
            code = codes[idx]
            config.setdefault('translation', {})['mode'] = code
            save_config(config)
            console.print(f"{STATUS_ICONS['success']} [green]Da chon:[/green] {TRANSLATION_MODES[code]['display']}")
    except ValueError:
        console.print(f"{STATUS_ICONS['error']} [red]Lua chon khong hop le[/red]")
    console.print()


def _apply_window_preset(config, lang_code: str):
    """Ap dung window preset theo ngon ngu."""
    preset = WINDOW_PRESETS.get(lang_code, WINDOW_PRESETS['en'])
    config['window'] = {
        'size': preset['size'],
        'history': preset['history'],
        'future': preset['future']
    }


def edit_glossary(config):
    """Sua glossary."""
    while True:
        console.print(f"\n{STATUS_ICONS['start']} [cyan]Quan ly Glossary[/cyan]\n")

        glossary = config.get('glossary', [])
        if glossary:
            table = Table(title="Danh sach Glossary")
            table.add_column("#", style="cyan", width=4)
            table.add_column("Source", style="yellow")
            table.add_column("Target", style="green")
            table.add_column("Context", style="dim")

            for i, term in enumerate(glossary, 1):
                table.add_row(
                    str(i),
                    term.get('source', ''),
                    term.get('target', ''),
                    term.get('context', '')
                )
            console.print(table)
        else:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Chua co glossary nao[/yellow]")

        console.print("\n  [1] Them tu moi")
        console.print("  [2] Xoa tu")
        console.print("  [0] Quay lai")

        choice = input("\nLua chon: ").strip()

        if choice == '1':
            source = input("  Tu goc: ").strip()
            target = input("  Tu dich: ").strip()
            context = input("  Context (optional): ").strip()
            if source and target:
                config.setdefault('glossary', []).append({
                    'source': source,
                    'target': target,
                    'context': context
                })
                save_config(config)
                console.print(f"{STATUS_ICONS['success']} [green]Da them tu moi[/green]")
        elif choice == '2':
            if glossary:
                idx = input("  Nhap so thu tu can xoa: ").strip()
                try:
                    i = int(idx) - 1
                    if 0 <= i < len(glossary):
                        removed = glossary.pop(i)
                        save_config(config)
                        console.print(f"{STATUS_ICONS['success']} [green]Da xoa:[/green] {removed['source']}")
                except ValueError:
                    console.print(f"{STATUS_ICONS['error']} [red]Chi so khong hop le[/red]")
        elif choice == '0':
            break
    console.print()


def validate_config(config):
    """Kiem tra cau hinh hop le."""
    errors = []

    if 'project' not in config:
        errors.append("Thieu muc 'project' trong cau hinh")
    else:
        if 'input_srt' not in config['project']:
            errors.append("Thieu 'input_srt' trong cau hinh")
        if 'output_srt' not in config['project']:
            errors.append("Thieu 'output_srt' (folder output) trong cau hinh")
        if 'name' not in config['project']:
            errors.append("Thieu 'name' trong cau hinh")

    mode = config.get('translation', {}).get('mode', 'default')
    if mode not in ('default', 'uncen'):
        errors.append(f"'translation.mode' phai la 'default' hoac 'uncen', hien tai: '{mode}'")

    if 'model' not in config and 'models' not in config:
        errors.append("Thieu muc 'model' hoac 'models' trong cau hinh")
    elif 'models' in config:
        for m, v in config['models'].items():
            if 'name' not in v:
                errors.append(f"Thieu 'name' trong models.{m}")

    return errors


def get_active_model_config(config):
    """Lay cau hinh model dang su dung dua tren che do dich."""
    mode = get_mode_key(config)
    source_lang = (config.get('project', {}).get('source_lang', '') or '').strip().lower()

    # Uu tien model theo ngon ngu (neu co cau hinh models.<lang>)
    # Vi du: source_lang='japanese' -> models.ja neu co
    lang_to_key = {
        'japanese': 'ja', 'tiếng nhật': 'ja', 'tieng nhat': 'ja', 'ja': 'ja',
        'chinese': 'zh', 'tiếng trung': 'zh', 'tieng trung': 'zh', 'zh': 'zh',
        'korean': 'ko', 'tiếng hàn': 'ko', 'tieng han': 'ko', 'ko': 'ko',
        'english': 'en', 'tiếng anh': 'en', 'tieng anh': 'en', 'en': 'en',
    }
    lang_key = lang_to_key.get(source_lang)
    if lang_key and config.get('models', {}).get(lang_key):
        cfg = config['models'][lang_key]
        return {
            'name': cfg.get('name', 'huihui_ai/hunyuan-mt-abliterated:7b-chimera'),
            'ollama_url': cfg.get('ollama_url', 'http://localhost:11434/api/generate'),
            'temperature': cfg.get('temperature', 0.1),
            'repeat_penalty': cfg.get('repeat_penalty', 1.2),
            'num_ctx': cfg.get('num_ctx', 6144),
            'num_predict': cfg.get('num_predict', 1024),
            'timeout': cfg.get('timeout', 180),
        }

    if mode == 'uncen':
        uncen_cfg = config.get('models', {}).get('uncen', {})
        return {
            'name': uncen_cfg.get('name', 'huihui_ai/qwen3-abliterated:8b-v2'),
            'ollama_url': uncen_cfg.get('ollama_url', 'http://localhost:11434/api/generate'),
            'temperature': uncen_cfg.get('temperature', 0.05),
            'repeat_penalty': uncen_cfg.get('repeat_penalty', 1.15),
            'num_ctx': uncen_cfg.get('num_ctx', 6144),
            'num_predict': uncen_cfg.get('num_predict', 1024),
            'timeout': uncen_cfg.get('timeout', 180),
        }
    else:
        default_cfg = config.get('models', {}).get('default', {})
        return {
            'name': default_cfg.get('name', 'huihui_ai/hunyuan-mt-abliterated:7b-chimera'),
            'ollama_url': default_cfg.get('ollama_url', 'http://localhost:11434/api/generate'),
            'temperature': default_cfg.get('temperature', 0.05),
            'repeat_penalty': default_cfg.get('repeat_penalty', 1.15),
            'num_ctx': default_cfg.get('num_ctx', 6144),
            'num_predict': default_cfg.get('num_predict', 1024),
            'timeout': default_cfg.get('timeout', 180),
        }


def run_translation(config):
    """Chay qua trinh dich."""
    errors = validate_config(config)
    if errors:
        console.print(f"\n{STATUS_ICONS['error']} [red]Cau hinh chua day du:[/red]")
        for err in errors:
            console.print(f"  - {err}")
        return

    mode = get_mode_key(config)
    mode_info = TRANSLATION_MODES.get(mode, TRANSLATION_MODES['default'])
    model_cfg = get_active_model_config(config)

    console.print(f"\n{STATUS_ICONS['start']} [cyan]Che do dich:[/cyan] {mode_info['display']}")
    console.print(f"{STATUS_ICONS['start']} [cyan]Model:[/cyan] {model_cfg['name']}")

    db = Database()
    project_name = config['project']['name']
    input_path = config['project']['input_srt']
    output_srt = config['project']['output_srt']
    
    # Lưu đường dẫn video gốc để tạo shortcut
    original_video_path = None
    if is_video_file(input_path):
        original_video_path = input_path

    if is_video_file(input_path):
        console.print(f"\n{STATUS_ICONS['processing']} [cyan]Phat hien file video, bat dau transcribe...[/cyan]")
        whisper_cfg = config.get('whisper', {})
        transcriber = WhisperTranscriber(whisper_cfg)

        base = os.path.splitext(input_path)[0]
        generated_srt = base + "_whisper.srt"

        if not os.path.exists(generated_srt):
            console.print(f"{STATUS_ICONS['start']} [cyan]Transcribing video...[/cyan]")
            try:
                transcriber.transcribe(input_path, generated_srt, language=whisper_cfg.get('language'))
                console.print(f"{STATUS_ICONS['success']} [green]Da tao phu de:[/green] {generated_srt}")
            except Exception as e:
                console.print(f"{STATUS_ICONS['error']} [red]Loi transcribe:[/red] {e}")
                return
        else:
            console.print(f"{STATUS_ICONS['success']} [green]Da co phu de, su dung lai:[/green] {generated_srt}")

        config['project']['input_srt'] = generated_srt

    proj = db.get_project_by_name(project_name)
    if proj:
        project_id = proj['id']
        console.print(f"{STATUS_ICONS['success']} [green]Project ton tai:[/green] ID={project_id}, Status={proj['status']}")
        if proj['status'] in ('completed', 'completed_with_errors'):
            resp = input("Project da hoan thanh truong do. Chay lai? (y/n): ").strip().lower()
            if resp not in ('y', 'yes'):
                console.print(f"{STATUS_ICONS['warning']} [yellow]Da huy.[/yellow]")
                return
            db.update_project_status(project_id, 'pending')
        elif proj['status'] in ('partial', 'failed'):
            console.print(f"{STATUS_ICONS['warning']} [yellow]Project dang o trang thai '{proj['status']}', tiep tuc tu noi da dung.[/yellow]")
    else:
        project_id = db.create_project(
            name=project_name,
            source_lang=config['project'].get('source_lang', 'Japanese'),
            target_lang=config['project'].get('target_lang', 'Vietnamese'),
            input_srt=config['project']['input_srt'],
            output_srt=output_srt,
            window_size=config.get('window', {}).get('size', 6),
            history_size=config.get('window', {}).get('history', 12),
            future_size=config.get('window', {}).get('future', 4),
            original_video_path=original_video_path,
        )
        console.print(f"{STATUS_ICONS['success']} [green]Da tao project moi:[/green] ID={project_id}")

        if 'glossary' in config:
            for term in config['glossary']:
                db.add_glossary_term(
                    project_id,
                    term['source'],
                    term['target'],
                    term.get('context', '')
                )

    config['model'] = model_cfg

    console.print(f"\n{STATUS_ICONS['start']} [cyan]Bat dau dich...[/cyan]\n")
    pipeline = TranslationPipeline(config)
    pipeline.run(project_id)


def interactive_mode(config):
    """Che do tuong tac."""
    while True:
        print_header()
        print_main_menu(config)

        choice = input("Lua chon: ").strip()

        if choice == '1':
            get_input_path_interactive(config)
        elif choice == '2':
            get_output_path_interactive(config)
        elif choice == '3':
            edit_project_name(config)
        elif choice == '4':
            edit_languages(config)
        elif choice == '5':
            edit_translation_mode(config)
        elif choice == '6':
            edit_glossary(config)
        elif choice == '7':
            run_translation(config)
            input("\nNhan Enter de quay lai menu...")
        elif choice == '0':
            console.print(f"\n{STATUS_ICONS['success']} [green]Tam biet![/green]\n")
            sys.exit(0)
        else:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Lua chon khong hop le[/yellow]")
            input()


def main():
    parser = argparse.ArgumentParser(description='Subtitle Translator')
    parser.add_argument('--input', '-i', help='Input file (video or SRT)')
    parser.add_argument('--output', '-o', help='Output SRT file')
    parser.add_argument('--source-lang', '-s', help='Source language (ja, ko, zh, en)')
    parser.add_argument('--target-lang', '-t', help='Target language (vi, en, zh, ja, ko)')
    parser.add_argument('--mode', '-m', help='Translation mode (default, uncen)')
    parser.add_argument('--interactive', '-I', action='store_true', help='Interactive mode')
    parser.add_argument('--config', '-c', default='config.yaml', help='Config file path')

    args = parser.parse_args()

    config = load_config()

    if args.input:
        config.setdefault('project', {})['input_srt'] = args.input
    if args.output:
        config.setdefault('project', {})['output_srt'] = args.output
    if args.source_lang:
        code = args.source_lang.lower()
        if code in LANGUAGES:
            config.setdefault('project', {})['source_lang'] = LANGUAGES[code]['name']
            config.setdefault('whisper', {})['language'] = LANGUAGES[code]['whisper_code']
            _apply_window_preset(config, code)
        else:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Ngon ngu nguon khong hop le:[/yellow] {code}")
    if args.target_lang:
        code = args.target_lang.lower()
        if code in TARGET_LANGUAGES:
            config.setdefault('project', {})['target_lang'] = TARGET_LANGUAGES[code]['name']
        else:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Ngon ngu dich khong hop le:[/yellow] {code}")
    if args.mode:
        code = get_mode_code(args.mode)
        config.setdefault('translation', {})['mode'] = code
        console.print(f"{STATUS_ICONS['success']} [green]Che do dich:[/green] {TRANSLATION_MODES[code]['display']}")

    if args.input or args.output or args.source_lang or args.target_lang or args.mode:
        save_config(config)
        run_translation(config)
    else:
        interactive_mode(config)


if __name__ == "__main__":
    main()
