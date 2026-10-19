"""
Subtitle Translator GUI — PySide6 (Qt) dark theme.
Native desktop app. Reads/writes config.yaml. Launches translation pipeline.
Replaces the Rich terminal interactive_mode() in main.py.

Design DNA: docs/design-dna.json
- Dark theme, flat solid colors (NO gradients)
- System emoji icons (no asset files)
- QSS-based styling
- Sidebar nav + stacked pages (Workspace / Settings / Glossary / Log)

Run: python gui.py
"""

import os
import sys
import queue
import re
import subprocess
import threading
from pathlib import Path

import yaml
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import (
    QFont, QIcon, QPixmap, QPainter, QColor, QAction, QStandardItemModel, QStandardItem
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QStackedWidget, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox,
    QFileDialog, QMessageBox, QPlainTextEdit, QFrame, QScrollArea, QSizePolicy,
    QStatusBar, QSpacerItem, QToolButton, QButtonGroup, QProgressBar
)

# ---------------------------------------------------------------------------
# Design tokens — flat solid colors, no gradients
# ---------------------------------------------------------------------------
THEME = {
    "bg":            "#0B0F19",
    "panel":         "#111827",
    "card":          "#1F2937",
    "card_hover":    "#283548",
    "border":        "#374151",
    "border_subtle": "#1F2937",
    "text":          "#F9FAFB",
    "text_body":     "#D1D5DB",
    "text_muted":    "#9CA3AF",
    "primary":       "#3B82F6",
    "primary_hover": "#2563EB",
    "primary_press": "#1D4ED8",
    "secondary":     "#8B5CF6",
    "accent":        "#22D3EE",
    "success":       "#10B981",
    "warning":       "#F59E0B",
    "error":         "#EF4444",
    "info":          "#3B82F6",
}

# QSS — Qt Style Sheets, NO gradients
QSS = f"""
QWidget {{
    background-color: {THEME['bg']};
    color: {THEME['text_body']};
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}}

/* ---------- Main window ---------- */
QMainWindow {{
    background-color: {THEME['bg']};
}}

/* ---------- Top bar ---------- */
QFrame#topbar {{
    background-color: {THEME['panel']};
    border-bottom: 1px solid {THEME['border_subtle']};
}}

QLabel#appTitle {{
    color: {THEME['text']};
    font-size: 18px;
    font-weight: 700;
    background: transparent;
}}

QLabel#projectTitle {{
    color: {THEME['text']};
    font-size: 14px;
    font-weight: 600;
    background: transparent;
}}

QLabel#projectMeta {{
    color: {THEME['text_muted']};
    font-size: 11px;
    background: transparent;
}}

/* Status pill */
QFrame#statusPill {{
    background-color: {THEME['success']};
    border-radius: 10px;
    padding: 2px 4px;
}}
QFrame#statusPill QLabel {{
    color: white;
    font-size: 11px;
    font-weight: 600;
    background: transparent;
}}
QFrame#statusPill[status="warning"] {{ background-color: {THEME['warning']}; }}
QFrame#statusPill[status="error"]   {{ background-color: {THEME['error']}; }}
QFrame#statusPill[status="info"]    {{ background-color: {THEME['info']}; }}

/* ---------- Sidebar ---------- */
QFrame#sidebar {{
    background-color: {THEME['panel']};
    border-right: 1px solid {THEME['border_subtle']};
}}

QPushButton#navButton {{
    background-color: transparent;
    color: {THEME['text_body']};
    border: none;
    border-radius: 6px;
    padding: 10px 14px;
    text-align: left;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton#navButton:hover {{
    background-color: {THEME['card']};
}}
QPushButton#navButton:checked {{
    background-color: {THEME['card']};
    color: {THEME['text']};
    border-left: 3px solid {THEME['text_muted']};
}}

QPushButton#exitButton {{
    background-color: {THEME['card']};
    color: {THEME['error']};
    border: 1px solid {THEME['border']};
    border-radius: 6px;
    padding: 10px 14px;
    text-align: left;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#exitButton:hover {{
    background-color: {THEME['error']};
    color: white;
    border-color: {THEME['error']};
}}
QPushButton#stopButton {{
    background-color: {THEME['card']};
    color: {THEME['warning']};
    border: 1px solid {THEME['border']};
    border-radius: 6px;
    padding: 10px 14px;
    text-align: left;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton#stopButton:hover {{
    background-color: {THEME['warning']};
    color: {THEME['bg']};
    border-color: {THEME['warning']};
}}
QPushButton#stopButton:disabled {{
    color: {THEME['text_muted']};
    border-color: {THEME['border']};
    background-color: {THEME['bg']};
}}

/* ---------- Card / Panel ---------- */
QFrame#card {{
    background-color: {THEME['card']};
    border: 1px solid {THEME['border_subtle']};
    border-radius: 10px;
}}

QFrame#separator {{
    background-color: {THEME['border_subtle']};
    max-height: 1px;
    min-height: 1px;
}}

/* ---------- Section heading ---------- */
QLabel#sectionTitle {{
    color: {THEME['text']};
    font-size: 16px;
    font-weight: 700;
    background: transparent;
}}

QLabel#fieldLabel {{
    color: {THEME['text_muted']};
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    background: transparent;
}}

QLabel#valueLabel {{
    color: {THEME['text']};
    font-size: 13px;
    background: transparent;
}}

QLabel#valueLabelMono {{
    color: {THEME['text_body']};
    font-family: "Consolas", "JetBrains Mono", monospace;
    font-size: 12px;
    background: transparent;
}}

/* ---------- Input ---------- */
QLineEdit {{
    background-color: {THEME['panel']};
    color: {THEME['text']};
    border: 1px solid {THEME['border']};
    border-radius: 6px;
    padding: 8px 10px;
    selection-background-color: {THEME['border']};
    selection-color: {THEME['text']};
}}
QLineEdit:focus {{
    border: 1px solid {THEME['text_muted']};
}}
QLineEdit:disabled {{
    color: {THEME['text_muted']};
    background-color: {THEME['bg']};
}}

/* ---------- Combobox ---------- */
QComboBox {{
    background-color: {THEME['panel']};
    color: {THEME['text']};
    border: 1px solid {THEME['border']};
    border-radius: 6px;
    padding: 0 10px;
    min-height: 32px;
    max-height: 32px;
}}
QComboBox:hover {{
    border: 1px solid {THEME['text_muted']};
}}
QComboBox:focus {{
    border: 1px solid {THEME['text_muted']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
    subcontrol-origin: padding;
    subcontrol-position: right center;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {THEME['text_muted']};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {THEME['card']};
    color: {THEME['text']};
    border: 1px solid {THEME['border']};
    selection-background-color: {THEME['border']};
    selection-color: {THEME['text']};
    outline: none;
    padding: 4px;
}}
QComboBox QAbstractItemView::item {{
    background-color: {THEME['card']};
    color: {THEME['text']};
    padding: 6px 10px;
    min-height: 20px;
    border: none;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {THEME['card_hover']};
    color: {THEME['text']};
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {THEME['border']};
    color: {THEME['text']};
}}

/* ---------- Checkbox ---------- */
QCheckBox {{
    color: {THEME['text_body']};
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1.5px solid {THEME['border']};
    border-radius: 3px;
    background-color: {THEME['panel']};
}}
QCheckBox::indicator:hover {{
    border: 1.5px solid {THEME['primary']};
}}
QCheckBox::indicator:checked {{
    background-color: {THEME['primary']};
    border: 1.5px solid {THEME['primary']};
    image: none;
}}

/* ---------- Button ---------- */
QPushButton {{
    background-color: {THEME['card']};
    color: {THEME['text']};
    border: 1px solid {THEME['border']};
    border-radius: 6px;
    padding: 9px 14px;
    font-size: 13px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton:hover {{
    background-color: {THEME['card_hover']};
    border-color: {THEME['text_muted']};
}}
QPushButton:pressed {{
    background-color: {THEME['border']};
}}
QPushButton:disabled {{
    color: {THEME['text_muted']};
    background-color: {THEME['bg']};
    border-color: {THEME['border_subtle']};
}}

QPushButton[variant="primary"] {{
    background-color: {THEME['primary']};
    color: white;
    border: 1px solid {THEME['primary']};
}}
QPushButton[variant="primary"]:hover {{
    background-color: {THEME['primary_hover']};
    border-color: {THEME['primary_hover']};
}}
QPushButton[variant="primary"]:pressed {{
    background-color: {THEME['primary_press']};
}}
QPushButton[variant="primary"]:disabled {{
    background-color: {THEME['border']};
    border-color: {THEME['border']};
    color: {THEME['text_muted']};
}}

QPushButton[variant="danger"] {{
    background-color: {THEME['card']};
    color: {THEME['error']};
    border: 1px solid {THEME['border']};
}}
QPushButton[variant="danger"]:hover {{
    background-color: {THEME['error']};
    color: white;
    border-color: {THEME['error']};
}}

QPushButton[variant="ghost"] {{
    background-color: transparent;
    color: {THEME['text_muted']};
    border: 1px solid transparent;
}}
QPushButton[variant="ghost"]:hover {{
    background-color: {THEME['card']};
    color: {THEME['text']};
    border-color: {THEME['border_subtle']};
}}

/* Icon-only button (square) */
QPushButton[iconOnly="true"] {{
    min-width: 36px;
    max-width: 36px;
    padding: 6px;
}}

/* ---------- Progress bar ---------- */
QProgressBar {{
    background-color: {THEME['panel']};
    border: 1px solid {THEME['border_subtle']};
    border-radius: 4px;
    text-align: center;
    color: {THEME['text']};
    min-height: 8px;
    max-height: 8px;
}}
QProgressBar::chunk {{
    background-color: {THEME['text_muted']};
    border-radius: 3px;
}}

/* ---------- Scroll area ---------- */
QScrollArea {{
    background-color: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: {THEME['bg']};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {THEME['border']};
    border-radius: 5px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {THEME['text_muted']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* ---------- Log / PlainTextEdit ---------- */
QPlainTextEdit {{
    background-color: #0a0e16;
    color: {THEME['text_body']};
    border: 1px solid {THEME['border_subtle']};
    border-radius: 8px;
    padding: 10px;
    font-family: "Cascadia Mono", "Consolas", "Segoe UI", sans-serif;
    font-size: 12px;
    selection-background-color: {THEME['border']};
    selection-color: {THEME['text']};
}}

/* ---------- Glossary table-like rows ---------- */
QFrame#glossRow {{
    background-color: {THEME['card']};
    border: 1px solid {THEME['border_subtle']};
    border-radius: 6px;
}}
QFrame#glossHeader {{
    background-color: {THEME['card']};
    border: none;
    border-bottom: 1px solid {THEME['border']};
    border-radius: 0;
}}
QFrame#glossHeader QLabel {{
    color: {THEME['text_muted']};
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    background: transparent;
}}

/* ---------- Status bar ---------- */
QStatusBar {{
    background-color: {THEME['panel']};
    color: {THEME['text_muted']};
    border-top: 1px solid {THEME['border_subtle']};
}}
"""

# ---------------------------------------------------------------------------
# Static config
# ---------------------------------------------------------------------------
LANGUAGES = {
    "ja": "Tiếng Nhật (Japanese)",
    "ko": "Tiếng Hàn (Korean)",
    "zh": "Tiếng Trung (Chinese)",
    "en": "Tiếng Anh (English)",
}

TARGET_LANGUAGES = {
    "vi": "Tiếng Việt (Vietnamese)",
    "en": "Tiếng Anh (English)",
    "zh": "Tiếng Trung (Chinese)",
    "ja": "Tiếng Nhật (Japanese)",
    "ko": "Tiếng Hàn (Korean)",
}

TRANSLATION_MODES = {
    "default": "Mặc định (hunyuan-mt-abliterated)",
    "uncen":   "Uncen (qwen3-abliterated) [+18]",
}

# Emoji-based icons (no asset files needed)
ICON = {
    "app":       "🎬",
    "home":      "🏠",
    "settings":  "⚙",
    "book":      "📚",
    "log":       "📋",
    "exit":      "✕",
    "stop":      "■",
    "folder":    "📁",
    "save":      "💾",
    "play":      "▶",
    "tool":      "🔧",
    "plus":      "➕",
    "trash":     "🗑",
    "open":      "📂",
    "input":     "📥",
    "output":    "📤",
    "lang":      "🌐",
    "mode":      "🔀",
    "mic":       "🎙",
    "zap":       "⚡",
    "check":     "✓",
    "warn":      "⚠",
    "err":       "✗",
    "info":      "ℹ",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_config(path="config.yaml"):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(config, path="config.yaml"):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def truncate(s, n=50):
    if not s or len(s) <= n:
        return s or ""
    return "..." + s[-(n - 3):]


def make_icon(emoji: str, size: int = 16, color: str = "#F9FAFB") -> QIcon:
    """Render emoji to QIcon as pixmap (no asset file)."""
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(color))
    font = QFont("Segoe UI Emoji", int(size * 1.1))
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignCenter, emoji)
    p.end()
    return QIcon(pix)


# ---------------------------------------------------------------------------
# Signal bridge for thread-safe Qt updates
# ---------------------------------------------------------------------------
class LogBridge(QObject):
    message = Signal(str, str)  # tag, text
    finished = Signal(int)      # exit code


_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))")


def _clean_log_text(text: str) -> str:
    """Remove terminal control sequences before displaying subprocess output."""
    text = _ANSI_ESCAPE_RE.sub("", text)
    return "".join(char for char in text if char in "\n\t" or ord(char) >= 32)


# ---------------------------------------------------------------------------
# Reusable widgets
# ---------------------------------------------------------------------------
class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.NoFrame)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 20, 20, 20)
        self._layout.setSpacing(12)

    def addLayout(self, layout):
        self._layout.addLayout(layout)

    def addWidget(self, w):
        self._layout.addWidget(w)

    def addStretch(self):
        self._layout.addStretch()


class FieldRow(QWidget):
    """Label + widget row with consistent spacing."""
    def __init__(self, label_text: str, widget: QWidget, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        lbl = QLabel(label_text)
        lbl.setObjectName("fieldLabel")
        v.addWidget(lbl)
        v.addWidget(widget)


class FileInputRow(QWidget):
    """Line edit + browse button for file paths. Khi chọn file sẽ emit signal để App tự tạo output folder."""
    on_input_changed = Signal(str)

    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.line = QLineEdit()
        self.line.setPlaceholderText(placeholder)
        h.addWidget(self.line, 1)
        self.btn = QPushButton(ICON["folder"])
        self.btn.setProperty("iconOnly", True)
        self.btn.setToolTip("Chọn file…")
        h.addWidget(self.btn, 0)
        self.btn.clicked.connect(self._browse)
        self.line.editingFinished.connect(lambda: self.on_input_changed.emit(self.text()))

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file",
            os.path.dirname(self.line.text()) or os.getcwd(),
            "Media files (*.mp4 *.mkv *.mov *.avi *.webm *.srt);;All files (*.*)"
        )
        if path:
            self.line.setText(path)
            self.on_input_changed.emit(path)

    def text(self) -> str:
        return self.line.text().strip()

    def setText(self, s: str):
        self.line.setText(s)


class OutputPathDisplay(QWidget):
    """Read-only hiển thị output folder. Không cho sửa, không có nút browse.
    Output folder tự sinh từ input theo logic main.py._auto_setup_project_paths.
    """
    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.line = QLineEdit()
        self.line.setPlaceholderText(placeholder)
        self.line.setReadOnly(True)
        self.line.setObjectName("outputPathDisplay")
        h.addWidget(self.line, 1)

    def text(self) -> str:
        return self.line.text().strip()

    def setText(self, s: str):
        self.line.setText(s)


class StatusPill(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusPill")
        self.setProperty("status", "success")
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 3, 10, 3)
        h.setSpacing(4)
        self.label = QLabel("●  Sẵn sàng")
        h.addWidget(self.label)

    def set_status(self, text: str, kind: str = "success"):
        self.setProperty("status", kind)
        # Force re-evaluation of dynamic property
        self.style().unpolish(self)
        self.style().polish(self)
        self.label.setText("●  " + text)


class NavButton(QPushButton):
    def __init__(self, icon: str, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("navButton")
        self.setCheckable(True)
        self.setText(f"  {icon}   {text}")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(40)


class IconButton(QPushButton):
    """Square button with icon only — for row actions."""
    def __init__(self, icon: str, tooltip: str = "", variant: str = "ghost", parent=None):
        super().__init__(parent)
        self.setText(icon)
        self.setProperty("iconOnly", True)
        self.setProperty("variant", variant)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(36, 32)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
class WorkspacePage(QWidget):
    def __init__(self, app: "App", parent=None):
        super().__init__(parent)
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)

        # Project header card
        header = Card()
        header.addWidget(self._make_section_title(f"{ICON['app']}  Project hiện tại"))
        self.project_name_lbl = QLabel("—")
        self.project_name_lbl.setObjectName("projectTitle")
        self.project_name_lbl.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(self.project_name_lbl)
        self.project_meta_lbl = QLabel("")
        self.project_meta_lbl.setObjectName("projectMeta")
        header.addWidget(self.project_meta_lbl)
        v.addWidget(header)

        # Two-column body
        body = QHBoxLayout()
        body.setSpacing(16)
        body.addLayout(self._build_files_col(), 3)
        body.addLayout(self._build_side_col(), 2)
        v.addLayout(body)

    def _make_section_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("sectionTitle")
        return lbl

    def _build_files_col(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(0)
        files_card = Card()
        files_card.addWidget(self._make_section_title(f"{ICON['folder']}  Files"))
        # Input
        self.input_row = FileInputRow("Chọn file video hoặc SRT…")
        files_card.addWidget(FieldRow(f"{ICON['input']}  Input (video hoặc SRT)", self.input_row))
        # Recent projects dropdown
        recent_row = QHBoxLayout()
        recent_label = QLabel(f"  {ICON['clock']}  Recent:")
        recent_label.setObjectName("muted")
        recent_row.addWidget(recent_label)
        self.recent_combo = QComboBox()
        self.recent_combo.setMinimumWidth(300)
        self.recent_combo.setPlaceholderText("(trống)")
        recent_row.addWidget(self.recent_combo, 1)
        files_card.addLayout(recent_row)
        # Output - read-only, tự sinh từ input
        self.output_row = OutputPathDisplay("Output folder sẽ tự tạo cùng tên project…")
        files_card.addWidget(FieldRow(f"{ICON['output']}  Output folder (tự động)", self.output_row))
        col.addWidget(files_card)
        col.addStretch()
        return col

    def _build_side_col(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(0)
        # Settings card
        settings_card = Card()
        settings_card.addWidget(self._make_section_title(f"{ICON['settings']}  Cấu hình nhanh"))
        self.src_combo = QComboBox()
        for v in LANGUAGES.values():
            self.src_combo.addItem(v)
        settings_card.addWidget(FieldRow(f"{ICON['lang']}  Ngôn ngữ nguồn", self.src_combo))
        self.tgt_combo = QComboBox()
        for v in TARGET_LANGUAGES.values():
            self.tgt_combo.addItem(v)
        settings_card.addWidget(FieldRow(f"{ICON['lang']}  Ngôn ngữ đích", self.tgt_combo))
        self.mode_combo = QComboBox()
        for v in TRANSLATION_MODES.values():
            self.mode_combo.addItem(v)
        settings_card.addWidget(FieldRow(f"{ICON['mode']}  Chế độ dịch", self.mode_combo))
        col.addWidget(settings_card)

        # Actions card
        actions_card = Card()
        actions_card.addWidget(self._make_section_title(f"{ICON['zap']}  Hành động"))
        self.start_btn = QPushButton(f"{ICON['play']}   Bắt đầu dịch")
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        actions_card.addWidget(self.start_btn)
        self.fix_btn = QPushButton(f"{ICON['tool']}   Sửa SRT bằng Rule Processor")
        self.fix_btn.setCursor(Qt.PointingHandCursor)
        actions_card.addWidget(self.fix_btn)
        self.save_btn = QPushButton(f"{ICON['save']}   Lưu cấu hình")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        actions_card.addWidget(self.save_btn)

        # Progress
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("valueLabel")
        actions_card.addWidget(self.progress_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setVisible(False)
        actions_card.addWidget(self.progress)

        col.addWidget(actions_card)
        col.addStretch()
        return col

    def set_status(self, text: str):
        self.project_meta_lbl.setText(text)


class SettingsPage(QWidget):
    def __init__(self, app: "App", parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)

        # Whisper card
        whisper_card = Card()
        title = QLabel(f"{ICON['mic']}  Whisper")
        title.setObjectName("sectionTitle")
        whisper_card.addWidget(title)

        self.model_combo = QComboBox()
        self.model_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        whisper_card.addWidget(FieldRow("Model size", self.model_combo))

        self.device_combo = QComboBox()
        self.device_combo.addItems(["cuda", "cpu"])
        whisper_card.addWidget(FieldRow("Device", self.device_combo))
        v.addWidget(whisper_card)

        # Pipeline card
        pipe_card = Card()
        title2 = QLabel(f"{ICON['zap']}  Pipeline")
        title2.setObjectName("sectionTitle")
        pipe_card.addWidget(title2)

        self.workers_edit = QLineEdit()
        self.workers_edit.setPlaceholderText("1")
        pipe_card.addWidget(FieldRow("Số worker song song", self.workers_edit))

        self.split_cb = QCheckBox("Tách sub dài (>6s)")
        pipe_card.addWidget(self.split_cb)
        self.glossary_cb = QCheckBox("Bật glossary")
        pipe_card.addWidget(self.glossary_cb)
        self.literal_cb = QCheckBox("Literal mode (uncen: không polish)")
        pipe_card.addWidget(self.literal_cb)

        # LLM card (advanced)
        llm_card = Card()
        llm_title = QLabel(f"{ICON['zap']}  LLM (Advanced)")
        llm_title.setObjectName("sectionTitle")
        llm_card.addWidget(llm_title)

        self.num_predict_edit = QLineEdit()
        self.num_predict_edit.setPlaceholderText("2048")
        llm_card.addWidget(FieldRow("num_predict (token tối đa)", self.num_predict_edit))

        self.temperature_edit = QLineEdit()
        self.temperature_edit.setPlaceholderText("0.05")
        llm_card.addWidget(FieldRow("temperature", self.temperature_edit))

        self.timeout_edit = QLineEdit()
        self.timeout_edit.setPlaceholderText("180 (giây)")
        llm_card.addWidget(FieldRow("timeout", self.timeout_edit))

        self.ollama_url_edit = QLineEdit()
        self.ollama_url_edit.setPlaceholderText("http://localhost:11434/api/generate")
        llm_card.addWidget(FieldRow("Ollama URL", self.ollama_url_edit))

        # Pipeline card 2 - advanced
        pipe2_card = Card()
        pipe2_title = QLabel(f"{ICON['zap']}  Pipeline (Advanced)")
        pipe2_title.setObjectName("sectionTitle")
        pipe2_card.addWidget(pipe2_title)

        self.max_retries_edit = QLineEdit()
        self.max_retries_edit.setPlaceholderText("5")
        pipe2_card.addWidget(FieldRow("Max retries", self.max_retries_edit))

        self.checkpoint_edit = QLineEdit()
        self.checkpoint_edit.setPlaceholderText("20")
        pipe2_card.addWidget(FieldRow("Checkpoint interval", self.checkpoint_edit))

        v.addWidget(pipe_card)
        v.addWidget(llm_card)
        v.addWidget(pipe2_card)
        v.addStretch()


class GlossaryPage(QWidget):
    def __init__(self, app: "App", parent=None):
        super().__init__(parent)
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # Toolbar
        toolbar = QHBoxLayout()
        title = QLabel(f"{ICON['book']}  Glossary (từ điển dịch thuật)")
        title.setObjectName("sectionTitle")
        toolbar.addWidget(title)
        toolbar.addStretch()
        self.add_btn = QPushButton(f"{ICON['plus']}  Thêm")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.add_btn)

        # Import/Export CSV
        self.import_btn = QPushButton(f"{ICON['open']}  Import CSV")
        self.import_btn.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.import_btn)
        self.export_btn = QPushButton(f"{ICON['save']}  Export CSV")
        self.export_btn.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.export_btn)

        self.save_btn = QPushButton(f"{ICON['save']}  Lưu")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.save_btn)
        v.addLayout(toolbar)

        # Header row
        header = QFrame()
        header.setObjectName("glossHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(8)
        for txt, w in [("SOURCE (JA)", 3), ("TARGET (VI)", 3), ("CONTEXT", 4), ("", 0)]:
            lbl = QLabel(txt)
            h.addWidget(lbl, w)
        v.addWidget(header)

        # Scroll area for rows
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_inner = QWidget()
        self.scroll_inner.setStyleSheet("background: transparent;")
        self.rows_layout = QVBoxLayout(self.scroll_inner)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(4)
        self.rows_layout.addStretch()
        self.scroll.setWidget(self.scroll_inner)
        v.addWidget(self.scroll, 1)

        self.rows = []  # list of dicts: {frame, src, tgt, ctx}
        self.add_btn.clicked.connect(lambda: self._add_row())
        self.save_btn.clicked.connect(self._save)
        self.import_btn.clicked.connect(self._import_csv)
        self.export_btn.clicked.connect(self._export_csv)

    def _import_csv(self):
        """Import glossary từ file CSV (header: source,target,context)."""
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Import CSV", "", "CSV (*.csv)")
        if not path:
            return
        try:
            import csv
            with open(path, encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows_added = 0
                for row in reader:
                    src = row.get('source', '').strip()
                    tgt = row.get('target', '').strip()
                    ctx = row.get('context', '').strip()
                    if src or tgt:
                        self._add_row(src, tgt, ctx)
                        rows_added += 1
            if hasattr(self, 'app') and self.app:
                self.app.set_status(f"Đã import {rows_added} dòng", "success")
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Lỗi import", str(e))

    def _export_csv(self):
        """Export glossary hiện tại ra file CSV."""
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "glossary.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            import csv
            with open(path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['source', 'target', 'context'])
                writer.writeheader()
                for r in self.rows:
                    s = r["src"].text().strip()
                    t = r["tgt"].text().strip()
                    c = r["ctx"].text().strip()
                    if s or t:
                        writer.writerow({'source': s, 'target': t, 'context': c})
            if hasattr(self, 'app') and self.app:
                self.app.set_status(f"Đã export glossary → {path}", "success")
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Lỗi export", str(e))

    def _add_row(self, src="", tgt="", ctx=""):
        # Insert before the stretch
        idx = self.rows_layout.count() - 1
        row = QFrame()
        row.setObjectName("glossRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(6)

        src_edit = QLineEdit(src)
        src_edit.setPlaceholderText("おちんちん")
        h.addWidget(src_edit, 3)
        tgt_edit = QLineEdit(tgt)
        tgt_edit.setPlaceholderText("dương vật")
        h.addWidget(tgt_edit, 3)
        ctx_edit = QLineEdit(ctx)
        ctx_edit.setPlaceholderText("context (optional)")
        h.addWidget(ctx_edit, 4)
        del_btn = IconButton(ICON["trash"], "Xoá dòng")
        h.addWidget(del_btn, 0)

        # Insert before stretch
        self.rows_layout.insertWidget(idx, row)
        data = {"frame": row, "src": src_edit, "tgt": tgt_edit, "ctx": ctx_edit}
        del_btn.clicked.connect(lambda: self._remove_row(data))
        self.rows.append(data)

    def _remove_row(self, data):
        data["frame"].deleteLater()
        self.rows.remove(data)

    def load(self, glossary: list):
        # Clear existing
        for r in self.rows:
            r["frame"].deleteLater()
        self.rows.clear()
        if not glossary:
            self._add_row()
            return
        for entry in glossary:
            self._add_row(entry.get("source", ""), entry.get("target", ""), entry.get("context", ""))

    def _save(self):
        new_gloss = []
        for r in self.rows:
            s = r["src"].text().strip()
            t = r["tgt"].text().strip()
            c = r["ctx"].text().strip()
            if s and t:
                entry = {"source": s, "target": t}
                if c:
                    entry["context"] = c
                new_gloss.append(entry)
        self.app.config["glossary"] = new_gloss
        save_config(self.app.config, self.app.config_path)
        self.app.set_status(f"Đã lưu {len(new_gloss)} glossary", "success")
        self.app.log_message("OK", f"Đã lưu {len(new_gloss)} mục glossary")


class LogPage(QWidget):
    def __init__(self, app: "App", parent=None):
        super().__init__(parent)
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # Toolbar
        toolbar = QHBoxLayout()
        title = QLabel(f"{ICON['log']}  Log")
        title.setObjectName("sectionTitle")
        toolbar.addWidget(title)
        toolbar.addStretch()

        # Tag filter
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["ALL", "INFO", "OK", "WARN", "ERR"])
        self.filter_combo.setMinimumWidth(80)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(self.filter_combo)

        # Auto-scroll toggle
        self.autoscroll_cb = QCheckBox("Auto-scroll")
        self.autoscroll_cb.setChecked(True)
        toolbar.addWidget(self.autoscroll_cb)

        self.clear_btn = QPushButton(f"{ICON['trash']}  Xoá log")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.clear_btn)
        self.open_btn = QPushButton(f"{ICON['open']}  Mở folder log")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.open_btn)
        v.addLayout(toolbar)

        # Log text
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("Log sẵn sàng. Bấm 'Bắt đầu dịch' ở Workspace để chạy.")
        v.addWidget(self.text, 1)

        self.clear_btn.clicked.connect(self.text.clear)
        self.open_btn.clicked.connect(self._open_folder)

    def _open_folder(self):
        folder = os.path.abspath(".")
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder])
        else:
            subprocess.run(["xdg-open", folder])


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Subtitle Translator — Dark Pro")
        self.setMinimumSize(1000, 640)
        self.resize(1100, 720)

        # Enable drag-drop
        self.setAcceptDrops(True)

        self.config = load_config()
        self.config_path = "config.yaml"
        self.is_running = False
        self.process = None
        self.log_queue = queue.Queue()
        self.log_bridge = LogBridge()
        self.log_bridge.message.connect(self._on_log_message)
        self.log_bridge.finished.connect(self._on_pipeline_finished)

        self.setStyleSheet(QSS)
        self._build_ui()
        self._refresh_from_config()
        self._poll_log_queue()
        """Terminate main.py subprocess + đợi tối đa 5s. An toàn khi process đã chết."""
        proc = getattr(self, 'process', None)
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except Exception as e:
            self.log_bridge.message.emit("WARN", f"Kill process lỗi: {e}")

    def closeEvent(self, event):
        """Dam bao subprocess main.py bi kill khi user dong GUI."""
        if getattr(self, 'is_running', False):
            self.log_bridge.message.emit("WARN", "Đóng GUI → đang dừng pipeline...")
            self._kill_running_process()
        event.accept()

    # ---- Drag & drop --------------------------------------------------
    def dragEnterEvent(self, event):
        """Accept file drops on main window."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """Handle dropped file → auto-fill input field."""
        urls = event.mimeData().urls()
        if not urls:
            return
        # Take first file with .srt/.mp4/.mkv extension
        for url in urls:
            path = url.toLocalFile()
            if path and os.path.splitext(path)[1].lower() in ('.srt', '.mp4', '.mkv'):
                self.page_workspace.input_row.setText(path)
                self.set_status(f"Đã nhận file: {os.path.basename(path)}", "success")
                event.acceptProposedAction()
                return
        event.ignore()

    # ---- UI build -----------------------------------------------------
    def _build_ui(self):
        # Top bar
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(56)
        th = QHBoxLayout(topbar)
        th.setContentsMargins(20, 0, 20, 0)
        th.setSpacing(12)
        title = QLabel(f"{ICON['app']}  Subtitle Translator")
        title.setObjectName("appTitle")
        th.addWidget(title)
        th.addStretch()
        self.project_title = QLabel("")
        self.project_title.setObjectName("projectTitle")
        th.addWidget(self.project_title)
        self.status_pill = StatusPill()
        th.addWidget(self.status_pill)

        # Body
        body = QWidget()
        bh = QHBoxLayout(body)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(0)

        # Sidebar
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(220)
        sl = QVBoxLayout(self.sidebar)
        sl.setContentsMargins(12, 16, 12, 12)
        sl.setSpacing(4)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        nav_items = [
            ("workspace", ICON["home"],     "Workspace"),
            ("settings",  ICON["settings"], "Cài đặt"),
            ("glossary",  ICON["book"],     "Glossary"),
            ("log",       ICON["log"],      "Log"),
        ]
        self.nav_buttons = {}
        for i, (key, icon, label) in enumerate(nav_items):
            btn = NavButton(icon, label)
            self.nav_group.addButton(btn, i)
            sl.addWidget(btn)
            self.nav_buttons[key] = btn
            btn.clicked.connect(lambda checked, k=key: self._switch_section(k))

        sl.addStretch()
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        sl.addWidget(sep)
        self.exit_btn = QPushButton(f"   {ICON['exit']}    Thoát")
        self.exit_btn.setObjectName("exitButton")
        self.exit_btn.setCursor(Qt.PointingHandCursor)
        self.exit_btn.clicked.connect(self._on_close)
        sl.addWidget(self.exit_btn)

        self.stop_btn = QPushButton(f"   {ICON['stop']}    Dừng")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        sl.addWidget(self.stop_btn)

        # Pages
        self.stack = QStackedWidget()
        self.page_workspace = WorkspacePage(self)
        self.page_settings = SettingsPage(self)
        self.page_glossary = GlossaryPage(self)
        self.page_log = LogPage(self)
        self.stack.addWidget(self.page_workspace)
        self.stack.addWidget(self.page_settings)
        self.stack.addWidget(self.page_glossary)
        self.stack.addWidget(self.page_log)

        # Content frame (with margins)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 24, 24, 24)
        cl.setSpacing(0)
        cl.addWidget(self.stack)

        bh.addWidget(self.sidebar)
        bh.addWidget(content, 1)

        # Central widget
        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(topbar)
        cv.addWidget(body, 1)
        self.setCentralWidget(central)

        # Status bar
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Sẵn sàng")

        # Wire actions
        self.page_workspace.start_btn.clicked.connect(self._start_translation)
        self.page_workspace.fix_btn.clicked.connect(self._fix_srt_rules)
        self.page_workspace.save_btn.clicked.connect(self._save_config)
        self.page_workspace.input_row.on_input_changed.connect(self._on_input_changed)
        self.page_workspace.recent_combo.currentIndexChanged.connect(self._on_recent_selected)

        # Default selection
        self.nav_buttons["workspace"].setChecked(True)
        self._switch_section("workspace")

    def _switch_section(self, key: str):
        pages = {
            "workspace": self.page_workspace,
            "settings":  self.page_settings,
            "glossary":  self.page_glossary,
            "log":       self.page_log,
        }
        page = pages.get(key)
        if page:
            self.stack.setCurrentWidget(page)
        self.nav_buttons[key].setChecked(True)

    # ---- Config --------------------------------------------------------
    def _refresh_from_config(self):
        p = self.config.get("project", {})
        self.page_workspace.input_row.setText(p.get("input_srt", ""))
        self.page_workspace.output_row.setText(p.get("output_srt", ""))

        src = p.get("source_lang", "Japanese")
        for code, name in LANGUAGES.items():
            if name == src:
                self.page_workspace.src_combo.setCurrentText(name)
                break
        else:
            self.page_workspace.src_combo.setCurrentIndex(0)

        tgt = p.get("target_lang", "Vietnamese")
        for code, name in TARGET_LANGUAGES.items():
            if name == tgt:
                self.page_workspace.tgt_combo.setCurrentText(name)
                break
        else:
            self.page_workspace.tgt_combo.setCurrentIndex(0)

        mode = self.config.get("translation", {}).get("mode", "uncen")
        self.page_workspace.mode_combo.setCurrentText(TRANSLATION_MODES.get(mode, list(TRANSLATION_MODES.values())[0]))

        w = self.config.get("whisper", {})
        self.page_settings.model_combo.setCurrentText(w.get("model_size", "large-v3"))
        self.page_settings.device_combo.setCurrentText(w.get("device", "cuda"))

        pipe = self.config.get("pipeline", {})
        self.page_settings.workers_edit.setText(str(pipe.get("num_workers", 1)))
        self.page_settings.split_cb.setChecked(pipe.get("split_long_subs", True))
        self.page_settings.glossary_cb.setChecked(pipe.get("enable_glossary", True))
        self.page_settings.literal_cb.setChecked(self.config.get("translation", {}).get("literal_mode", False))

        # LLM settings (from models.uncen - the active mode)
        mode = self.config.get("translation", {}).get("mode", "uncen")
        llm_cfg = self.config.get("models", {}).get(mode, {})
        self.page_settings.num_predict_edit.setText(str(llm_cfg.get("num_predict", 2048)))
        self.page_settings.temperature_edit.setText(str(llm_cfg.get("temperature", 0.05)))
        self.page_settings.timeout_edit.setText(str(llm_cfg.get("timeout", 180)))
        self.page_settings.ollama_url_edit.setText(llm_cfg.get("ollama_url", "http://localhost:11434/api/generate"))

        self.page_settings.max_retries_edit.setText(str(pipe.get("max_retries", 5)))
        self.page_settings.checkpoint_edit.setText(str(pipe.get("checkpoint_interval", 20)))

        self.page_glossary.load(self.config.get("glossary", []) or [])

        # Populate recent projects dropdown
        self.page_workspace.recent_combo.clear()
        recents = self._load_recent_projects()
        for r in recents:
            self.page_workspace.recent_combo.addItem(r)
        if recents:
            self.page_workspace.recent_combo.setCurrentIndex(0)

        # Project header
        proj_name = p.get("name", "—")
        self.page_workspace.project_name_lbl.setText(proj_name)
        meta = f"{src}  →  {tgt}   ·   mode: {mode}"
        self.page_workspace.project_meta_lbl.setText(meta)
        self.project_title.setText(f"{truncate(p.get('input_srt', ''), 30)}")

    def _save_config(self):
        p = self.config.setdefault("project", {})
        p["input_srt"] = self.page_workspace.input_row.text()
        p["output_srt"] = self.page_workspace.output_row.text()
        p["name"] = os.path.splitext(os.path.basename(p["input_srt"] or "project"))[0] or "my_project"
        for code, name in LANGUAGES.items():
            if name == self.page_workspace.src_combo.currentText():
                p["source_lang"] = name
                self.config.setdefault("whisper", {})["language"] = code
                break
        for code, name in TARGET_LANGUAGES.items():
            if name == self.page_workspace.tgt_combo.currentText():
                p["target_lang"] = name
                break
        for code, display in TRANSLATION_MODES.items():
            if display == self.page_workspace.mode_combo.currentText():
                self.config.setdefault("translation", {})["mode"] = code
                break

        w = self.config.setdefault("whisper", {})
        w["model_size"] = self.page_settings.model_combo.currentText()
        w["device"] = self.page_settings.device_combo.currentText()

        pipe = self.config.setdefault("pipeline", {})
        try:
            pipe["num_workers"] = int(self.page_settings.workers_edit.text() or 1)
        except ValueError:
            pipe["num_workers"] = 1
        pipe["split_long_subs"] = self.page_settings.split_cb.isChecked()
        pipe["enable_glossary"] = self.page_settings.glossary_cb.isChecked()
        self.config["translation"]["literal_mode"] = self.page_settings.literal_cb.isChecked()

        # LLM settings (save to models.{active_mode})
        mode = self.config.get("translation", {}).get("mode", "uncen")
        llm_cfg = self.config.setdefault("models", {}).setdefault(mode, {})
        try:
            llm_cfg["num_predict"] = int(self.page_settings.num_predict_edit.text() or 2048)
        except ValueError:
            llm_cfg["num_predict"] = 2048
        try:
            llm_cfg["temperature"] = float(self.page_settings.temperature_edit.text() or 0.05)
        except ValueError:
            llm_cfg["temperature"] = 0.05
        try:
            llm_cfg["timeout"] = int(self.page_settings.timeout_edit.text() or 180)
        except ValueError:
            llm_cfg["timeout"] = 180
        llm_cfg["ollama_url"] = self.page_settings.ollama_url_edit.text().strip() or "http://localhost:11434/api/generate"

        try:
            pipe["max_retries"] = int(self.page_settings.max_retries_edit.text() or 5)
        except ValueError:
            pipe["max_retries"] = 5
        try:
            pipe["checkpoint_interval"] = int(self.page_settings.checkpoint_edit.text() or 20)
        except ValueError:
            pipe["checkpoint_interval"] = 20

        save_config(self.config, self.config_path)
        self.set_status("Đã lưu cấu hình", "success")
        self.log_message("OK", f"Đã lưu cấu hình vào {self.config_path}")
        self._refresh_from_config()

    def _save_recent_project(self, input_path: str):
        """Lưu input path vào recent projects (max 5)."""
        if not input_path or not os.path.exists(input_path):
            return
        recents = self.config.setdefault("recent_projects", [])
        # Remove duplicate
        recents = [r for r in recents if r != input_path]
        # Insert at top
        recents.insert(0, input_path)
        # Keep max 5
        self.config["recent_projects"] = recents[:5]
        save_config(self.config, self.config_path)

    def _load_recent_projects(self) -> list:
        """Load recent projects từ config, filter ra file không tồn tại."""
        recents = self.config.get("recent_projects", []) or []
        return [r for r in recents if os.path.exists(r)]

    # ---- Actions -------------------------------------------------------
    def _on_recent_selected(self, index: int):
        """Khi user chọn 1 recent project → set input."""
        if index < 0:
            return
        path = self.page_workspace.recent_combo.itemText(index)
        if path and os.path.exists(path):
            self.page_workspace.input_row.setText(path)
            self.set_status(f"Đã chọn: {os.path.basename(path)}", "success")

    def _on_input_changed(self, path: str):
        """Khi user chọn input mới -> tự tạo folder output cùng tên project, giống main.py."""
        if not path or not os.path.exists(path):
            return
        try:
            from main import _auto_setup_project_paths
        except Exception:
            return  # main.py không import được, bỏ qua (chỉ lúc dev)
        paths = _auto_setup_project_paths(self.config, path)
        self.page_workspace.output_row.setText(paths["output_dir"])
        # Cập nhật project_name trong config (đã làm trong _auto_setup_project_paths)
        # Refresh title bar nếu có
        if hasattr(self, 'project_title') and hasattr(self, 'project_name_lbl'):
            self.project_title.setText(self.config.get('project', {}).get('name', ''))
            self.project_name_lbl.setText(self.config.get('project', {}).get('name', ''))
        # Lưu vào recent projects
        self._save_recent_project(path)

    def _start_translation(self):
        if self.is_running:
            QMessageBox.warning(self, "Đang chạy", "Pipeline đang chạy, vui lòng đợi hoàn tất.")
            return

        self._save_config()
        self.is_running = True
        self.page_workspace.start_btn.setEnabled(False)
        self.page_workspace.start_btn.setText("⏳  Đang chạy…")
        self.stop_btn.setEnabled(True)
        self.page_workspace.progress.setVisible(True)
        self.page_workspace.progress_label.setText("Đang khởi động pipeline…")
        self.set_status("Đang chạy", "warning")
        self._switch_section("log")

        t = threading.Thread(target=self._run_pipeline, daemon=True)
        t.start()

    def _run_pipeline(self):
        try:
            cmd = [sys.executable, "main.py",
                   "--input", self.page_workspace.input_row.text()]
            self.log_bridge.message.emit("INFO", f"Chạy: {' '.join(cmd)}")
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, universal_newlines=True, encoding="utf-8",
                errors="replace"
            )
            for line in self.process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tag = "INFO"
                low = line.lower()
                if "error" in low or "[-]" in line:
                    tag = "ERR"
                elif "warning" in low or "[!]" in line:
                    tag = "WARN"
                elif "[+]" in line or "[ok]" in low:
                    tag = "OK"

                # shortcut: parse progress from log line
                # Pipeline emits: "Window 14: JA 3-pass OK (6 lines, ...)"
                # or "[*] Window 5/120 processed"
                # or "[*] Processing translation queue..."
                m = re.search(r"[Ww]indow\s+(\d+)/(\d+)", line)
                if m:
                    try:
                        cur = int(m.group(1))
                        total = int(m.group(2))
                        if total > 0:
                            self._update_progress(cur, total)
                    except (ValueError, AttributeError):
                        pass

                self.log_bridge.message.emit(tag, line)
            self.process.wait()
            self.log_bridge.finished.emit(self.process.returncode)
        except Exception as e:
            self.log_bridge.message.emit("ERR", f"Lỗi: {e}")
            self.log_bridge.finished.emit(-1)

    def _fix_srt_rules(self):
        input_srt = self.page_workspace.input_row.text()
        if not input_srt or not os.path.exists(input_srt):
            QMessageBox.warning(self, "Thiếu file", "Chọn file SRT input trước.")
            return
        try:
            from core.post_processor import RuleBasedPostProcessor
            processor = RuleBasedPostProcessor()
            processor.set_character_names(self.config.get("character_names", []))
            with open(input_srt, "r", encoding="utf-8") as f:
                lines = f.read().split("\n")
            fixed = processor.process_lines(lines)
            with open(input_srt + ".fixed", "w", encoding="utf-8") as f:
                f.write("\n".join(fixed))
            self.set_status("Đã sửa", "success")
            self.log_message("OK", f"Đã sửa SRT → {input_srt}.fixed")
        except Exception as e:
            self.set_status("Lỗi", "error")
            self.log_message("ERR", f"Sửa SRT thất bại: {e}")

    # ---- Logging -------------------------------------------------------
    def log_message(self, tag: str, text: str):
        self.log_bridge.message.emit(tag, text)

    def _on_log_message(self, tag: str, text: str):
        current_filter = self.page_log.filter_combo.currentText()
        if current_filter != "ALL":
            # Tag mapping: INFO/WARN/ERR vs OK
            tag_normalized = tag.upper()
            if tag == "[OK]" or tag == "OK":
                tag_normalized = "OK"
            if tag_normalized != current_filter:
                return
        self.page_log.text.appendPlainText(_clean_log_text(text))
        # Auto-scroll to bottom
        if self.page_log.autoscroll_cb.isChecked():
            bar = self.page_log.text.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _update_progress(self, cur: int, total: int):
        """Update progress bar từ log parsing. Chạy trên UI thread."""
        bar = self.page_workspace.progress
        if bar.maximum() != total:
            bar.setRange(0, total)
        bar.setValue(cur)

    def _on_pipeline_finished(self, rc: int):
        self.is_running = False
        self.page_workspace.start_btn.setEnabled(True)
        self.page_workspace.start_btn.setText(f"{ICON['play']}   Bắt đầu dịch")
        self.stop_btn.setEnabled(False)
        self.page_workspace.progress.setVisible(False)
        if rc == 0:
            self.set_status("Hoàn tất", "success")
        else:
            self.set_status(f"Lỗi (exit {rc})", "error")

    def _poll_log_queue(self):
        # Reserved for future: log queue polling. Currently we use Qt signals directly.
        QTimer.singleShot(500, self._poll_log_queue)

    # ---- Status --------------------------------------------------------
    def set_status(self, text: str, kind: str = "info"):
        self.status_pill.set_status(text, kind)
        self.statusBar.showMessage(text)

    # ---- Close ---------------------------------------------------------
    def _on_stop_clicked(self):
        """Nút 'Dừng' tren sidebar: kill subprocess main.py, giu GUI chay."""
        if not self.is_running:
            return
        self.log_bridge.message.emit("WARN", "Yêu cầu dừng pipeline...")
        self.stop_btn.setEnabled(False)  # tranh bam nhieu lan
        self._kill_running_process()
        # finished signal tu thread se tu dong set is_running=False + UI

    def _on_close(self):
        if self.is_running:
            if QMessageBox.question(self, "Đang chạy", "Pipeline đang chạy. Thoát?",
                                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        self.close()


def main():
    # Qt6: AA_EnableHighDpiScaling đã deprecated (always on).
    # Warning khong phai exception nen try/except khong bat duoc - phai loc bang warnings filter.
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
        except Exception:
            pass
    if hasattr(Qt, 'HighDpiScaleFactorRoundingPolicy'):
        try:
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Subtitle Translator")
    window = App()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
