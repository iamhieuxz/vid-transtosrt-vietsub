# Force UTF-8 cho stdout/stderr de in tieng Viet co dau tren Windows console
# (mac dinh cp1252 -> charmap crash khi gap \u1eb5 v.v.) - dat SAU import sys.
import os
import sys
import argparse
import shutil
import time as _time
import subprocess
import requests
import yaml
import logging
from rich.console import Console
from rich.table import Table
from core.database import Database
from core.pipeline import TranslationPipeline
from core.transcriber import WhisperTranscriber
from core.precheck import WhisperPreCheck, run_precheck

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, OSError):
    pass  # Python < 3.7 hoac khong phai console (pipe file)

__version__ = "1.2"

# Rich console: bat buoc UTF-8 de in tieng Viet co dau (fix Windows charmap)
console = Console(legacy_windows=False)

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
    mode = config.get('translation', {}).get('mode', 'uncen')
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
    console.print(f"  [2] Xem Output folder        : [yellow]{truncate_path(output_path)}[/yellow]")
    console.print(f"  [3] Ten Project              : [yellow]{project_name}[/yellow]")
    console.print(f"  [4] Chon ngon ngu           : [cyan]{src_display}[/cyan] -> [green]{tgt_display}[/green]")
    console.print(f"  [5] Che do dich             : {mode_display}")
    console.print(f"  [6] Chinh sua Glossary")
    console.print(f"  [7] [green]Bat dau dich[/green]")
    console.print(f"  [8] [yellow]Sua file SRT bang Rule Processor[/yellow]")
    console.print(f"  [0] [red]Thoat[/red]")
    console.print()

    console.print(f"[dim]Window preset: size={win_size}, history={win.get('history', 12)}, future={win.get('future', 4)}[/dim]")


def truncate_path(path, max_len=40):
    """Cat ngan duong dan neu qua dai."""
    if not path or len(path) <= max_len:
        return path
    return "..." + path[-(max_len-3):]


def _extract_project_name_from_file(filepath: str) -> str:
    """
    Trich ten project tu file input (khong bao gom duoi mo rong).
    Vi du:
      E:/HIDE/JAV/EYAN-029.mp4 -> EYAN-029
      E:/HIDE/JAV/EYAN-029.srt -> EYAN-029
      EYAN-029.srt              -> EYAN-029
    """
    base = os.path.basename(filepath)
    name = os.path.splitext(base)[0]
    # Bo hau to _whisper neu co
    if name.endswith('_whisper'):
        name = name[:-8]
    return name


def _resolve_whisper_path(input_path: str) -> str:
    """
    Resolve duong dan file whisper output.
    Vi du:
      E:/HIDE/JAV/EYAN-029.mp4  -> E:/HIDE/JAV/EYAN-029/EYAN-029_whisper.srt
      E:/HIDE/JAV/EYAN-029.srt  -> E:/HIDE/JAV/EYAN-029/EYAN-029_whisper.srt
    """
    project_name = _extract_project_name_from_file(input_path)
    abs_path = os.path.abspath(input_path)

    if os.path.isdir(abs_path):
        parent = abs_path
    elif os.path.isfile(abs_path) or not os.path.exists(abs_path):
        parent = os.path.dirname(abs_path)
    else:
        parent = os.getcwd()

    output_dir = os.path.join(parent, project_name)
    return os.path.join(output_dir, f"{project_name}_whisper.srt")


def _resolve_translated_path(input_path: str) -> str:
    """
    Resolve duong dan file dich output.
    Vi du:
      E:/HIDE/JAV/EYAN-029.mp4  -> E:/HIDE/JAV/EYAN-029/EYAN-029.srt
    """
    project_name = _extract_project_name_from_file(input_path)
    abs_path = os.path.abspath(input_path)

    if os.path.isdir(abs_path):
        parent = abs_path
    elif os.path.isfile(abs_path) or not os.path.exists(abs_path):
        parent = os.path.dirname(abs_path)
    else:
        parent = os.getcwd()

    output_dir = os.path.join(parent, project_name)
    return os.path.join(output_dir, f"{project_name}.srt")


def _auto_setup_project_paths(config, input_path: str) -> dict:
    """
    Tu dong thiet lap:
      - project.name tu ten file input
      - project.output_srt = folder chua file input
      - project.input_srt = input_path (giu nguyen)

    Returns dict chua cac duong dan output.
    """
    project_name = _extract_project_name_from_file(input_path)
    abs_path = os.path.abspath(input_path)

    if os.path.isdir(abs_path):
        parent = abs_path
    elif os.path.isfile(abs_path) or not os.path.exists(abs_path):
        parent = os.path.dirname(abs_path)
    else:
        parent = os.getcwd()

    config.setdefault('project', {})['name'] = project_name
    config['project']['output_srt'] = parent
    config['project']['input_srt'] = input_path
    save_config(config)

    output_dir = os.path.join(parent, project_name)
    return {
        'project_name': project_name,
        'output_dir': output_dir,
        'translated_srt': os.path.join(output_dir, f"{project_name}.srt"),
        'whisper_srt': os.path.join(output_dir, f"{project_name}_whisper.srt"),
    }


def get_input_path_interactive(config):
    """
    Lay input path tu nguoi dung.
    Khi chon file -> tu dong sinh:
      - ten project = ten file (khong duoi)
      - folder output = folder chua file input
    """
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
                paths = _auto_setup_project_paths(config, path)
                console.print(f"{STATUS_ICONS['success']} [green]Da chon:[/green] {path}")
                console.print(f"  [cyan]Ten project:[/cyan] [yellow]{paths['project_name']}[/yellow]")
                console.print(f"  [cyan]Folder output:[/cyan] {paths['output_dir']}")
                console.print(f"  [cyan]File dich:[/cyan] {paths['translated_srt']}")
        else:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Da huy[/yellow]")
    elif choice == '2':
        path = input("Nhap duong dan file: ").strip().strip('"')
        if path:
            if not os.path.exists(path):
                console.print(f"{STATUS_ICONS['error']} [red]File not found:[/red] {path}")
            else:
                paths = _auto_setup_project_paths(config, path)
                console.print(f"{STATUS_ICONS['success']} [green]Da luu:[/green] {path}")
                console.print(f"  [cyan]Ten project:[/cyan] [yellow]{paths['project_name']}[/yellow]")
                console.print(f"  [cyan]Folder output:[/cyan] {paths['output_dir']}")
                console.print(f"  [cyan]File dich:[/cyan] {paths['translated_srt']}")
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
    """
    Tu dong dat output folder = folder chua file input.
    Folder dich se nam trong folder project con cung ten voi file.
    """
    input_path = config.get('project', {}).get('input_srt', '')

    if not input_path or not os.path.exists(input_path):
        console.print(f"{STATUS_ICONS['error']} [red]Chua co file input hoac file khong ton tai[/red]")
        console.print(f"  Hay chon file Input truoc (menu [1])")
        console.print()
        return

    paths = _auto_setup_project_paths(config, input_path)
    console.print(f"\n{STATUS_ICONS['start']} [cyan]Output folder:[/cyan]")
    console.print(f"  [green]Ten project:[/green] [yellow]{paths['project_name']}[/yellow]")
    console.print(f"  [green]Folder output:[/green] {paths['output_dir']}")
    console.print(f"  [green]File dich:[/green] {paths['translated_srt']}")
    console.print(f"  [green]File whisper:[/green] {paths['whisper_srt']}")
    console.print()


def edit_project_name(config):
    """Sua ten project (mac dinh = ten file input khong duoi)."""
    console.print(f"\n{STATUS_ICONS['start']} [cyan]Chinh sua ten Project[/cyan]")
    current = config.get('project', {}).get('name', 'my_movie')
    input_path = config.get('project', {}).get('input_srt', '')

    auto_name = ''
    if input_path and os.path.exists(input_path):
        auto_name = _extract_project_name_from_file(input_path)

    console.print(f"Ten hien tai: [yellow]{current}[/yellow]")
    if auto_name and auto_name != current:
        console.print(f"Ten goi y (tu file input): [green]{auto_name}[/green]")
    console.print("Nhap ten moi (Enter de giu nguyen, 'auto' de dung ten tu file input):")
    new_name = input("  Ten moi: ").strip()

    if new_name.lower() == 'auto' and auto_name:
        config.setdefault('project', {})['name'] = auto_name
        save_config(config)
        console.print(f"{STATUS_ICONS['success']} [green]Da dat:[/green] {auto_name}")
    elif new_name:
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

    mode = config.get('translation', {}).get('mode', 'uncen')
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
    """Resolve model config, with translation mode taking precedence."""
    mode = str(get_mode_key(config) or 'default').strip().lower()
    source_lang = (config.get('project', {}).get('source_lang', '') or '').strip().lower()
    models = config.get('models', {})

    # An explicit mode is an intentional model choice; do not let the language
    # preset silently override the uncen model for Japanese projects.
    if mode == 'uncen' and models.get('uncen'):
        cfg = models['uncen']
        return {
            'name': cfg.get('name', 'huihui_ai/qwen3-abliterated:8b-v2'),
            'ollama_url': cfg.get('ollama_url', 'http://localhost:11434/api/generate'),
            'temperature': cfg.get('temperature', 0.05),
            'repeat_penalty': cfg.get('repeat_penalty', 1.15),
            'num_ctx': cfg.get('num_ctx', 6144),
            # P1-fix: uncen dialogues are often long and need full output.
            # 1024 tokens truncates long JAV lines mid-sentence. 2048 is safer.
            'num_predict': cfg.get('num_predict', 2048),
            'timeout': cfg.get('timeout', 180),
        }

    # In default mode, prefer a source-language-specific model when configured.
    lang_to_key = {
        'japanese': 'ja', 'tiếng nhật': 'ja', 'tieng nhat': 'ja', 'ja': 'ja',
        'chinese': 'zh', 'tiếng trung': 'zh', 'tieng trung': 'zh', 'zh': 'zh',
        'korean': 'ko', 'tiếng hàn': 'ko', 'tieng han': 'ko', 'ko': 'ko',
        'english': 'en', 'tiếng anh': 'en', 'tieng anh': 'en', 'en': 'en',
    }
    lang_key = lang_to_key.get(source_lang)
    if lang_key and models.get(lang_key):
        cfg = models[lang_key]
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
        uncen_cfg = models.get('uncen', {})
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
        default_cfg = models.get('default', {})
        return {
            'name': default_cfg.get('name', 'huihui_ai/hunyuan-mt-abliterated:7b-chimera'),
            'ollama_url': default_cfg.get('ollama_url', 'http://localhost:11434/api/generate'),
            'temperature': default_cfg.get('temperature', 0.05),
            'repeat_penalty': default_cfg.get('repeat_penalty', 1.15),
            'num_ctx': default_cfg.get('num_ctx', 6144),
            'num_predict': default_cfg.get('num_predict', 1024),
            'timeout': default_cfg.get('timeout', 180),
        }


def _check_ollama_connection(timeout=5) -> tuple[bool, str]:
    """
    Kiem tra ket noi Ollama.
    Returns: (is_connected, message)
    """
    try:
        import requests
        response = requests.get('http://localhost:11434/api/tags', timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            models = data.get('models', [])
            if models:
                model_names = [m.get('name', 'unknown') for m in models[:3]]
                return True, f"Ollama OK - Models: {', '.join(model_names)}"
            return False, "Ollama running but no models installed"
        return False, f"Ollama returned status {response.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "Ollama not running (connection refused)"
    except requests.exceptions.Timeout:
        return False, "Ollama timeout"
    except Exception as e:
        return False, f"Ollama check failed: {e}"


def _whisper_file_changed(project_name: str, new_whisper_path: str) -> tuple[bool, str]:
    """
    Kiem tra xem whisper file co thay doi so voi lan chay truoc.
    Returns: (changed, message)
    """
    import hashlib
    import json
    import os

    cache_file = os.path.join(os.getcwd(), '.whisper_cache.json')
    whisper_dir = os.path.dirname(new_whisper_path)

    # Doc cache
    cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except:
            pass

    old_entry = cache.get(project_name, {})
    old_path = old_entry.get('path')
    old_mtime = old_entry.get('mtime', 0)

    # File khong ton tai trong cache -> chua chay bao gio
    if not old_path:
        return False, "First run for this project"

    # File da ton tai nhung khong con ton tai -> xoa
    if not os.path.exists(new_whisper_path):
        return True, "Whisper file deleted"

    # So sanh path va modified time
    new_mtime = os.path.getmtime(new_whisper_path)

    if old_path != new_whisper_path:
        return True, f"Whisper path changed: {old_path} -> {new_whisper_path}"

    if new_mtime > old_mtime + 1:  # 1 second tolerance
        return True, f"Whisper file modified (mtime: {old_mtime:.0f} -> {new_mtime:.0f})"

    return False, "Whisper file unchanged"


def _update_whisper_cache(project_name: str, whisper_path: str):
    """Cap nhat cache whisper file."""
    import json
    import os

    cache_file = os.path.join(os.getcwd(), '.whisper_cache.json')

    cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except:
            pass

    cache[project_name] = {
        'path': whisper_path,
        'mtime': os.path.getmtime(whisper_path),
    }

    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2)


def _rotate_db_backups(db_path: str, keep: int = 5):
    """Xoay vòng backup: chỉ giữ `keep` file `.bak.*` mới nhất."""
    import glob as _glob
    pattern = f"{db_path}.bak.*"
    backups = sorted(_glob.glob(pattern), key=os.path.getmtime, reverse=True)
    for old in backups[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass


def _clear_project_db(project_id: int, db, clear_files=False, output_srt=None, delete_project=False, new_input_srt=None):
    """Xoa tat ca du lieu cua project de chay lai tu dau.
    
    Args:
        project_id: ID cua project
        db: Database instance
        clear_files: Neu True, xoa ca file SRT da dich
        output_srt: Duong dan file SRT de xoa (neu clear_files=True)
        delete_project: Neu True, xoa luon project record (de tao moi voi input_srt moi)
        new_input_srt: Neu delete_project=False, update input_srt moi vao project
    """
    # Auto-backup DB trước khi clear (bảo vệ data khỏi mất vĩnh viễn nếu user lỡ chạy lại).
    # Giữ 5 bản backup gần nhất, xoay vòng để không đầy đĩa.
    try:
        db_path = getattr(db, 'db_path', None)
        if db_path and os.path.exists(db_path):
            backup_path = f"{db_path}.bak.{int(_time.time())}"
            shutil.copy2(db_path, backup_path)
            _rotate_db_backups(db_path, keep=5)
            logger.info(f"[BACKUP] DB snapshot: {backup_path}")
    except Exception as e:
        logger.warning(f"[BACKUP] DB backup failed: {e}, continuing without backup")

    conn = db._get_connection()
    cursor = conn.cursor()

    # Xoa tat ca window contexts
    cursor.execute("DELETE FROM window_contexts WHERE project_id = ?", (project_id,))

    # Xoa tat ca windows
    cursor.execute("DELETE FROM windows WHERE project_id = ?", (project_id,))

    # Xoa dead letter queue
    cursor.execute("DELETE FROM dead_letter_queue WHERE project_id = ?", (project_id,))

    # Xoa subtitle items
    cursor.execute("DELETE FROM subtitle_items WHERE project_id = ?", (project_id,))

    if delete_project:
        # Xoa luon project record (de tao moi)
        cursor.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
        logger.info(f"[CLEAR] Deleted project record id={project_id}")
    elif new_input_srt:
        # Update input_srt moi vao project hien tai
        cursor.execute("UPDATE projects SET input_srt = ?, status = 'pending', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_input_srt, project_id))
        conn.commit()
        logger.info(f"[CLEAR] Updated project {project_id} input_srt to: {new_input_srt}")
    else:
        # Reset project status
        cursor.execute("UPDATE projects SET status = 'pending', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (project_id,))

    conn.commit()
    conn.close()

    logger.info(f"[CLEAR] DB cleared for project_id={project_id}")

    # Xoa file SRT cu neu co
    if clear_files and output_srt:
        import os
        if os.path.exists(output_srt):
            try:
                os.remove(output_srt)
                logger.info(f"[CLEAR] Deleted old SRT: {output_srt}")
                print(f"  {STATUS_ICONS['success']} Da xoa file SRT cu: {output_srt}")
            except Exception as e:
                logger.warning(f"[CLEAR] Could not delete SRT: {e}")

    print(f"  {STATUS_ICONS['success']} Da clear DB: windows, contexts, dead letters, subtitles")


def run_translation(config, non_interactive: bool = False):
    """Chay qua trinh dich.

    non_interactive=True khi chay tu GUI (stdin khong co nguoi dung):
    moi diem hoi input() se co dap an mac dinh thay vi treo/EOFError.
    """
    errors = validate_config(config)
    if errors:
        console.print(f"\n{STATUS_ICONS['error']} [red]Cau hinh chua day du:[/red]")
        for err in errors:
            console.print(f"  - {err}")
        return

    # Pre-flight check: Ollama connection
    console.print(f"\n{STATUS_ICONS['start']} [cyan]Kiem tra ket noi Ollama...[/cyan]")
    ollama_ok, ollama_msg = _check_ollama_connection()
    if ollama_ok:
        console.print(f"{STATUS_ICONS['success']} [green]{ollama_msg}[/green]")
    else:
        console.print(f"{STATUS_ICONS['error']} [red]Ollama loi:[/red] {ollama_msg}")
        console.print(f"{STATUS_ICONS['warning']} [yellow]Vui long bat Ollama truoc:[/yellow] ollama serve")
        if non_interactive:
            console.print(f"{STATUS_ICONS['error']} [red]Thoat (che do non-interactive).[/red]")
            return
        resp = input("Thu lai? (y/n): ").strip().lower()
        if resp == 'y':
            # Thu lai
            ollama_ok, ollama_msg = _check_ollama_connection()
            if not ollama_ok:
                console.print(f"{STATUS_ICONS['error']} [red]Ollama van loi. Thoat.[/red]")
                return
            console.print(f"{STATUS_ICONS['success']} [green]{ollama_msg}[/green]")
        else:
            return

    mode = get_mode_key(config)
    mode_info = TRANSLATION_MODES.get(mode, TRANSLATION_MODES['default'])
    model_cfg = get_active_model_config(config)
    config['model'] = model_cfg

    console.print(f"\n{STATUS_ICONS['start']} [cyan]Che do dich:[/cyan] {mode_info['display']}")
    console.print(
        f"{STATUS_ICONS['start']} [cyan]Model:[/cyan] {model_cfg['name']} "
        f"([dim]source={get_source_lang_key(config)}, mode={mode}[/dim])"
    )

    db = Database()
    project_name = config['project']['name']
    input_path = config['project']['input_srt']

    # Check Ollama/model and video/audio before allocating the Whisper model.
    if not _precheck_pipeline(config, input_path):
        console.print(f"{STATUS_ICONS['error']} [red]Precheck fail. Kiem tra loi ben tren.[/red]")
        return

    # Set original_video_path chi khi la file video (de luu vao DB)
    original_video_path = input_path if is_video_file(input_path) else None

    # Dam bao project name duoc trich tu ten file input
    if is_video_file(input_path):
        project_name = _extract_project_name_from_file(input_path)
        config['project']['name'] = project_name
        save_config(config)

    # Tinh output_srt = folder chua file input
    if os.path.isfile(input_path):
        parent = os.path.dirname(os.path.abspath(input_path))
    else:
        parent = os.getcwd()
    output_srt = parent
    config['project']['output_srt'] = output_srt

    # Xử lý video -> whisper SRT
    whisper_srt_path = None
    if is_video_file(input_path):
        console.print(f"\n{STATUS_ICONS['processing']} [cyan]Phat hien file video, bat dau transcribe...[/cyan]")
        whisper_cfg = config.get('whisper', {})

        # File whisper: <folder project>/<project_name>_whisper.srt
        whisper_srt_path = _resolve_whisper_path(input_path)

        # Dam bao folder project ton tai
        project_folder = os.path.join(parent, project_name)
        os.makedirs(project_folder, exist_ok=True)

        # Kiem tra whisper file co thay doi khong
        whisper_changed, change_msg = _whisper_file_changed(project_name, whisper_srt_path)

        if not os.path.exists(whisper_srt_path) or whisper_changed:
            if whisper_changed and os.path.exists(whisper_srt_path):
                console.print(f"{STATUS_ICONS['warning']} [yellow]Whisper file changed: {change_msg}[/yellow]")
            console.print(f"{STATUS_ICONS['start']} [cyan]Transcribing video...[/cyan]")
            transcriber = None
            try:
                # The model is loaded exactly once, and this call verifies/downloads
                # weights as needed before transcribing with the same instance.
                transcriber = WhisperTranscriber(whisper_cfg)
                transcriber.transcribe(input_path, whisper_srt_path, language=whisper_cfg.get('language'))
                # Validate output: file phai co noi dung, neu 0 byte -> abort som
                if os.path.exists(whisper_srt_path) and os.path.getsize(whisper_srt_path) == 0:
                    raise ValueError(
                        "Whisper tra ve file SRT 0 bytes (video co the im lang hoac "
                        "khong co audio track). Kiem tra lai file video."
                    )
                console.print(f"{STATUS_ICONS['success']} [green]Da tao phu de:[/green] {whisper_srt_path}")
                # Cap nhat cache sau khi tao moi
                _update_whisper_cache(project_name, whisper_srt_path)
            except Exception as e:
                console.print(f"{STATUS_ICONS['error']} [red]Loi transcribe:[/red] {e}")
                return
            finally:
                if transcriber is not None:
                    transcriber.clear_vram()
        else:
            console.print(f"{STATUS_ICONS['success']} [green]Da co phu de, su dung lai:[/green] {whisper_srt_path}")

        config['project']['input_srt'] = whisper_srt_path

    # Kiem tra project trong DB
    proj = db.get_project_by_name(project_name)
    if proj:
        project_id = proj['id']
        console.print(f"{STATUS_ICONS['success']} [green]Project ton tai:[/green] ID={project_id}, Status={proj['status']}")

        # Neu whisper file thay doi -> xoa DB + file SRT cu de chay lai
        if whisper_srt_path and whisper_changed:
            console.print(f"{STATUS_ICONS['warning']} [yellow]Xoa DB cu + file SRT de chay lai voi whisper moi...[/yellow]")
            # Keep the project row so its ID remains valid; clear dependent data
            # and point the existing project at the refreshed Whisper transcript.
            _clear_project_db(
                project_id, db, clear_files=True,
                output_srt=proj.get('output_srt'),
                new_input_srt=whisper_srt_path,
            )
            proj['status'] = 'pending'

        if proj:
            if proj['status'] in ('completed', 'completed_with_errors'):
                # GUI khong co stdin de hoi -> xem nhu user da duyet chay lai.
                resp = 'y' if non_interactive else input(
                    "Project da hoan thanh truong do. Chay lai? (y/n): ").strip().lower()
                if resp not in ('y', 'yes'):
                    console.print(f"{STATUS_ICONS['warning']} [yellow]Da huy.[/yellow]")
                    return
                # Clear toan bo DB + file SRT cu de chay lai tu dau
                console.print(f"{STATUS_ICONS['warning']} [yellow]Clear toan bo du lieu cu (DB + file SRT)...[/yellow]")
                # Update input_srt moi neu co
                if whisper_srt_path:
                    _clear_project_db(project_id, db, clear_files=True, output_srt=proj.get('output_srt'), new_input_srt=whisper_srt_path)
                else:
                    _clear_project_db(project_id, db, clear_files=True, output_srt=proj.get('output_srt'))
            elif proj['status'] in ('partial', 'failed'):
                console.print(f"{STATUS_ICONS['warning']} [yellow]Project dang o trang thai '{proj['status']}'[/yellow]")
                # Mac dinh clear de chay lai tu dau (user yeu cau)
                console.print(f"{STATUS_ICONS['start']} [cyan]Clear toan bo du lieu cu de chay lai tu dau...[/cyan]")
                if whisper_srt_path:
                    _clear_project_db(project_id, db, clear_files=True, output_srt=proj.get('output_srt'), new_input_srt=whisper_srt_path)
                else:
                    _clear_project_db(project_id, db, clear_files=True, output_srt=proj.get('output_srt'))
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
            # Dùng _normalize_glossary_entry để nhận cả schema cũ
            # (source/target/context) lẫn mới (source_term/target_term/context_hint).
            # Trước đây dùng term['source'] → KeyError nếu config dùng schema mới.
            from core.pipeline import _normalize_glossary_entry
            for term in config['glossary']:
                norm = _normalize_glossary_entry(term)
                if not norm:
                    continue
                db.add_glossary_term(
                    project_id,
                    norm['source_term'],
                    norm['target_term'],
                    norm['context_hint']
                )

    # PRE-CHECK + AUTO-FIX: Phát hiện và tự động sửa lỗi Whisper trước khi dịch
    final_input_srt = config['project']['input_srt']
    if final_input_srt and os.path.exists(final_input_srt) and final_input_srt.lower().endswith('.srt'):
        source_lang_key = get_source_lang_key(config)
        console.print(f"\n{STATUS_ICONS['start']} [cyan]Pre-check + Auto-fix file SRT (Whisper quality)...[/cyan]")
        try:
            # Auto-fix các lỗi rõ ràng trước
            from core.precheck import WhisperPreCheck, run_precheck
            checker = WhisperPreCheck(source_lang=source_lang_key)

            # Auto-fix nếu có thể
            fixed_path = checker.auto_fix_simple(final_input_srt)

            # Nếu là tiếng Nhật, áp dụng thêm auto-fix thông minh
            if source_lang_key == 'ja':
                # Parse và áp dụng các sửa lỗi Whisper thông minh
                import re
                with open(fixed_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Các từ cần auto-fix cho JA
                JA_AUTO_FIX = {
                    # Tên riêng
                    'かよこ': 'Kachiko',
                    'たけお': 'Takao',
                    'タケホー': 'Takesho',
                    '竹尾': 'Takeo',
                    'あゆみ': 'Ayumi',
                    '高田': 'Takada',
                    '高田さん': 'Takada-san',
                    # Whisper artifacts
                    'żeliさん': 'Kachiko',
                    'ビールある indic': 'Bia đây rồi',
                    'ビールある': 'Bia đây rồi',
                    '家計測': 'quản lý chi tiêu gia đình',
                    # Từ Nhật nghe sai
                    '生態室': '治療室',
                    '生態師': '整体師',
                    '腹見': 'お腹見せ',
                }

                ja_fixes = 0
                for old, new in JA_AUTO_FIX.items():
                    count = content.count(old)
                    if count > 0:
                        content = content.replace(old, new)
                        ja_fixes += count

                if ja_fixes > 0:
                    # Lưu file _fixed
                    base, ext = os.path.splitext(fixed_path)
                    ja_fixed_path = f"{base}_ja{ext}"
                    with open(ja_fixed_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    console.print(f"{STATUS_ICONS['success']} [green]Auto-fix JA: {ja_fixes} cho[/green]")
                    console.print(f"  [cyan]File da fix:[/cyan] {ja_fixed_path}")
                    # Cập nhật input_srt để dùng file đã fix
                    config['project']['input_srt'] = ja_fixed_path
                    final_input_srt = ja_fixed_path

            # Pre-check trên file đã fix
            precheck_result = run_precheck(final_input_srt, source_lang=source_lang_key, auto_fix=False)
            critical_count = len(precheck_result.get('critical', []))
            if critical_count > 0:
                console.print(f"{STATUS_ICONS['warning']} [yellow]Phat hien {critical_count} loi nghiem trong con lai.[/yellow]")
                console.print(f"  [yellow]→ Nen review file SRT goc truoc khi dich de chat luong tot hon.[/yellow]")
                console.print(f"  [dim](Pipeline van se tiep tuc, nhung ket qua co the kem)[/dim]")
        except Exception as e:
            logger.warning(f"Pre-check failed (non-blocking): {e}")
            console.print(f"{STATUS_ICONS['warning']} [yellow]Pre-check loi (bo qua): {e}[/yellow]")

    console.print(f"\n{STATUS_ICONS['start']} [cyan]Bat dau dich...[/cyan]\n")
    pipeline = TranslationPipeline(config)
    pipeline.run(project_id)


def _precheck_pipeline(config: dict, input_path: str) -> bool:
    """Check input/audio, Ollama model, and available VRAM without loading Whisper."""
    import requests as _req
    from rich.console import Console as _C
    from rich.table import Table as _T
    _console = _C()
    table = _T(title="[bold cyan]Precheck Pipeline[/bold cyan]", show_header=True, header_style="bold magenta")
    table.add_column("Step", style="cyan", width=24)
    table.add_column("Status", width=12)
    table.add_column("Detail", style="dim")
    errors = []
    is_video = is_video_file(input_path)

    # Check input and audio before any model work.
    if not os.path.isfile(input_path):
        errors.append(f"Input file not found: {input_path}")
        table.add_row("Input file", "[red]FAIL[/red]", input_path)
    else:
        table.add_row("Input file", "[green]OK[/green]", input_path)

    if is_video and os.path.isfile(input_path):
        try:
            probe = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_streams', '-select_streams', 'a',
                 '-of', 'default=nw=1', input_path],
                capture_output=True, text=True, timeout=10,
            )
            if probe.returncode == 0 and probe.stdout.strip():
                table.add_row("Video audio track", "[green]OK[/green]", "found")
            else:
                errors.append(f"Video has no readable audio track: {probe.stderr.strip()[:120]}")
                table.add_row("Video audio track", "[red]FAIL[/red]", "no audio stream")
        except FileNotFoundError:
            table.add_row("ffprobe", "[yellow]SKIP[/yellow]", "not in PATH; ffmpeg will validate during extraction")
        except Exception as e:
            errors.append(f"Audio precheck failed: {e}")
            table.add_row("Video audio track", "[red]FAIL[/red]", str(e)[:80])

    if errors:
        _console.print(table)
        for error in errors:
            _console.print(f"[red]Precheck: {error}[/red]")
        return False

    # --- 1. Ollama ping ---
    ollama_url = config.get('model', {}).get('ollama_url', 'http://localhost:11434')
    base_url = ollama_url.replace('/api/generate', '').replace('/api/chat', '').rstrip('/')
    try:
        r = _req.get(f"{base_url}/api/tags", timeout=5)
        if r.status_code == 200:
            available_models = {m['name'] for m in r.json().get('models', [])}
            table.add_row("Ollama server", "[green]OK[/green]", f"{base_url} ({len(available_models)} models)")
        else:
            errors.append(f"Ollama returned {r.status_code}")
            table.add_row("Ollama server", "[red]FAIL[/red]", f"HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"Ollama unreachable: {e}")
        table.add_row("Ollama server", "[red]FAIL[/red]", f"{base_url} - {e}")
        _console.print(table)
        return False

    # --- 2. Check + auto-pull the exact model selected for this run ---
    needed_models = {config['model']['name']} if config.get('model', {}).get('name') else set()

    for model_name in sorted(needed_models):
        if model_name in available_models:
            table.add_row(f"Model: {model_name}", "[green]OK[/green]", "available")
            continue
        # Auto-pull
        table.add_row(f"Model: {model_name}", "[yellow]PULL[/yellow]", "downloading...")
        _console.print(table)
        try:
            pull_resp = _req.post(
                f"{base_url}/api/pull",
                json={"name": model_name, "stream": True},
                timeout=600, stream=True,
            )
            pull_resp.raise_for_status()
            last_status = ""
            for line in pull_resp.iter_lines():
                if not line:
                    continue
                try:
                    import json
                    chunk = json.loads(line)
                    status = chunk.get('status', '')
                    if status and status != last_status:
                        _console.print(f"  [dim]{model_name}: {status}[/dim]")
                        last_status = status
                except Exception:
                    pass
            # Verify
            r2 = _req.get(f"{base_url}/api/tags", timeout=5)
            available_models = {m['name'] for m in r2.json().get('models', [])}
            if model_name in available_models:
                table.add_row(f"Model: {model_name}", "[green]OK[/green]", "pulled")
            else:
                errors.append(f"Pull {model_name} completed but not in /api/tags")
                table.add_row(f"Model: {model_name}", "[red]FAIL[/red]", "pulled but missing")
        except Exception as e:
            errors.append(f"Pull {model_name} failed: {e}")
            table.add_row(f"Model: {model_name}", "[red]FAIL[/red]", str(e)[:50])

    # --- 3. Whisper cache/VRAM inspection (no model instantiation) ---
    whisper_cfg = config.get('whisper', {})
    if is_video:
        from core.transcriber import WhisperTranscriber
        whisper_model = whisper_cfg.get('model_size', 'large-v3')
        cache = WhisperTranscriber._check_model_cache(whisper_model)
        cache_status = f"cached ({cache['total_mb']}MB)" if cache['cached'] else "not cached; will download during transcription"
        table.add_row(f"Whisper: {whisper_model}", "[green]OK[/green]" if cache['cached'] else "[yellow]INFO[/yellow]", cache_status)

    # --- 4. Pre-load VRAM check ---
    vram_total = vram_free = None
    try:
        probe = subprocess.run(
            ['nvidia-smi', '--query-gpu=memory.total,memory.free',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            line = probe.stdout.strip().split('\n')[0]
            vram_total, vram_free = [int(x.strip()) for x in line.split(',')[:2]]
            if is_video:
                whisper_model = whisper_cfg.get('model_size', 'large-v3')
                need = WhisperTranscriber._estimate_min_vram_mb(whisper_model)
            else:
                need = 0
            if need and vram_free < need:
                table.add_row("VRAM", "[yellow]WARN[/yellow]",
                              f"before load free={vram_free}MB, estimated need~{need}MB; Whisper may fallback to CPU")
            else:
                table.add_row("VRAM", "[green]OK[/green]", f"before load total={vram_total}MB, free={vram_free}MB")
    except (FileNotFoundError, Exception):
        table.add_row("VRAM", "[dim]SKIP[/dim]", "nvidia-smi not available")

    _console.print(table)
    if errors:
        _console.print(f"\n[red][FAIL] Precheck có {len(errors)} lỗi:[/red]")
        for e in errors:
            _console.print(f"  - {e}")
        return False
    _console.print(f"\n[green][OK] Precheck pass - sẵn sàng chạy pipeline.[/green]")
    return True


def fix_srt_with_rules(input_path: str, output_path: str = None):
    """Áp dụng RuleProcessor để sửa file SRT gốc trước khi dịch.
    Sửa: tên nhân vật nhất quán, lỗi chính tả 'vâng'->'hai', Whisper artifacts, từ tiếng Anh gốc.
    """
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_fixed{ext}"

    console.print(f"\n{STATUS_ICONS['start']} [cyan]Ap dung Rule Processor...[/cyan]")
    console.print(f"  Input: {input_path}")
    console.print(f"  Output: {output_path}")

    with open(input_path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    # Parse SRT
    import re
    blocks = []
    raw_blocks = re.split(r'\n\s*\n', content.strip())
    for raw in raw_blocks:
        lines = raw.strip().split('\n')
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
            time_line = lines[1].strip()
            text = '\n'.join(l.strip() for l in lines[2:] if l.strip())
            blocks.append({'id': idx, 'time': time_line, 'text': text})
        except ValueError:
            continue

    # Apply RuleProcessor
    from core.rules import get_rule_processor
    processor = get_rule_processor('config.yaml')
    fixed_count = 0
    all_violations = []

    for b in blocks:
        new_text, violations = processor.process_translation(b['text'])
        from core.post_processor import RuleBasedPostProcessor
        new_text = RuleBasedPostProcessor().process_line(new_text)
        if new_text != b['text']:
            b['text'] = new_text
            fixed_count += 1
            all_violations.extend([(b['id'], v) for v in violations])

    # Ghi file mới
    new_content_parts = []
    for b in blocks:
        new_content_parts.append(f"{b['id']}\n{b['time']}\n{b['text']}")
    new_content = '\n\n'.join(new_content_parts) + '\n'

    with open(output_path, 'w', encoding='utf-8-sig') as f:
        f.write(new_content)

    console.print(f"\n{STATUS_ICONS['success']} [green]Da xu ly {len(blocks)} dong, sua {fixed_count} dong[/green]")
    if all_violations:
        console.print(f"[green]Vi pham da sua: {len(all_violations)}[/green]")
    console.print(f"[cyan]File output: {output_path}[/cyan]")

    return output_path, fixed_count, all_violations


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
        elif choice == '8':
            # Sửa file SRT bằng Rule Processor (không cần dịch)
            input_srt = config.get('project', {}).get('input_srt', '')
            if input_srt and os.path.exists(input_srt):
                try:
                    fix_srt_with_rules(input_srt)
                except Exception as e:
                    console.print(f"{STATUS_ICONS['error']} Loi: {e}")
            else:
                console.print(f"{STATUS_ICONS['warning']} [yellow]Chua co file input SRT[/yellow]")
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
    parser.add_argument('--non-interactive', action='store_true',
                        help='Khong hoi input() (dung khi chay tu GUI, stdin khong co nguoi dung)')

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
        # shortcut: tự derive project name từ input path để tránh
        # config còn name cũ (VD: JUL-409) ghi đè project ID sai
        if args.input:
            _auto_setup_project_paths(config, args.input)
            config = load_config()  # reload sau save
        run_translation(config, non_interactive=args.non_interactive)
    else:
        interactive_mode(config)


if __name__ == "__main__":
    main()
