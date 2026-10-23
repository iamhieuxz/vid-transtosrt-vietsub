"""
Subtitle Translator GUI — PySide6 (Qt) Studio Obsidian & Electric Indigo Edition.
Native desktop app. Reads/writes config.yaml. Launches translation pipeline.
Replaces the Rich terminal interactive_mode() in main.py.

Design DNA:
- Luxury Dark Studio aesthetic (Obsidian #080B12 + Electric Indigo #6366F1)
- Tactile animated buttons (QVariantAnimation), custom QPainter vector icons
- iOS/Studio animated ToggleSwitch (QCheckBox subclass)
- Smooth page fade transitions, live KPI strip, interactive dropzone
- Searchable Glossary with one-click Glossary Miner & Color-coded Log viewer

Run: python gui.py
"""

import os
import sys
import queue
import re
import html
import datetime
import subprocess
import threading
import urllib.request
from pathlib import Path

import yaml
from PySide6.QtCore import (
    Qt, QTimer, Signal, QObject, QSize, QRectF, QPointF,
    QVariantAnimation, QPropertyAnimation, QEasingCurve
)
from PySide6.QtGui import (
    QFont, QIcon, QPixmap, QPainter, QPainterPath, QColor, QPen, QBrush,
    QAction, QStandardItemModel, QStandardItem
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QStackedWidget, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox,
    QFileDialog, QMessageBox, QPlainTextEdit, QFrame, QScrollArea, QSizePolicy,
    QStatusBar, QSpacerItem, QToolButton, QButtonGroup, QProgressBar,
    QGraphicsOpacityEffect
)

# ---------------------------------------------------------------------------
# Design tokens — Luxury Studio Dark (Obsidian & Champagne Gold)
# ---------------------------------------------------------------------------
THEME = {
    "bg":             "#0A0A0D",
    "panel":          "#101015",
    "card":           "#16161D",
    "card_alt":       "#1D1D26",
    "card_hover":     "#252430",
    "input_bg":       "#0D0D11",
    "border":         "#2E2D3A",
    "border_subtle":  "#201F29",
    "border_focus":   "#D4AF37",
    "text":           "#FAF8F5",
    "text_body":      "#D6D3CD",
    "text_muted":     "#8A8680",
    "primary":        "#D4AF37",
    "primary_hover":  "#E5C158",
    "primary_press":  "#B59024",
    "primary_soft":   "#262012",
    "secondary":      "#F59E0B",
    "accent":         "#EAB308",
    "success":        "#10B981",
    "success_soft":   "#063E2E",
    "warning":        "#F59E0B",
    "warning_soft":   "#3B2106",
    "error":          "#F43F5E",
    "error_soft":     "#420818",
    "info":           "#D4AF37",
    "info_soft":      "#282111",
}

QSS = f"""
QWidget {{
    background-color: {THEME['bg']};
    color: {THEME['text_body']};
    font-family: "Segoe UI Variable Display", "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
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
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.4px;
    background: transparent;
}}

QLabel#appBadge {{
    background-color: {THEME['primary_soft']};
    color: #FDE68A;
    border: 1px solid #856B24;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}

QLabel#projectTitle {{
    color: {THEME['text']};
    font-size: 13px;
    font-weight: 600;
    background: transparent;
}}

QLabel#projectMeta {{
    color: {THEME['text_muted']};
    font-size: 12px;
    background: transparent;
}}

/* Status pill */
QFrame#statusPill {{
    background-color: {THEME['success_soft']};
    border: 1px solid {THEME['success']};
    border-radius: 12px;
    padding: 2px 6px;
}}
QFrame#statusPill QLabel {{
    color: #6EE7B7;
    font-size: 11px;
    font-weight: 700;
    background: transparent;
}}
QFrame#statusPill[status="warning"] {{
    background-color: {THEME['warning_soft']};
    border-color: {THEME['warning']};
}}
QFrame#statusPill[status="warning"] QLabel {{
    color: #FCD34D;
}}
QFrame#statusPill[status="error"] {{
    background-color: {THEME['error_soft']};
    border-color: {THEME['error']};
}}
QFrame#statusPill[status="error"] QLabel {{
    color: #FDA4AF;
}}
QFrame#statusPill[status="info"] {{
    background-color: {THEME['info_soft']};
    border-color: {THEME['info']};
}}
QFrame#statusPill[status="info"] QLabel {{
    color: #FDE68A;
}}

/* ---------- Sidebar ---------- */
QFrame#sidebar {{
    background-color: {THEME['panel']};
    border-right: 1px solid {THEME['border_subtle']};
}}

QLabel#sidebarSection {{
    color: {THEME['text_muted']};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.2px;
    padding: 4px 12px;
    background: transparent;
}}

QPushButton#navButton {{
    background-color: transparent;
    color: {THEME['text_body']};
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 10px 14px;
    text-align: left;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#navButton:hover {{
    background-color: {THEME['card']};
    color: {THEME['text']};
    border-color: {THEME['border_subtle']};
}}
QPushButton#navButton:checked {{
    background-color: {THEME['primary_soft']};
    color: #FEF08A;
    border: 1px solid #786221;
    border-left: 3px solid {THEME['primary']};
}}

/* ---------- Card / Panel ---------- */
QFrame#card {{
    background-color: {THEME['card']};
    border: 1px solid {THEME['border_subtle']};
    border-radius: 12px;
}}

QFrame#statCard {{
    background-color: {THEME['card_alt']};
    border: 1px solid {THEME['border_subtle']};
    border-radius: 10px;
}}

QFrame#dropZone {{
    background-color: {THEME['input_bg']};
    border: 1.5px dashed {THEME['border']};
    border-radius: 10px;
}}
QFrame#dropZone[dragActive="true"] {{
    background-color: {THEME['primary_soft']};
    border: 1.5px dashed {THEME['primary']};
}}

QFrame#separator {{
    background-color: {THEME['border_subtle']};
    max-height: 1px;
    min-height: 1px;
}}

/* ---------- Section heading ---------- */
QLabel#sectionTitle {{
    color: {THEME['text']};
    font-size: 15px;
    font-weight: 700;
    padding: 2px 0;
    background: transparent;
    min-height: 22px;
}}

QLabel#fieldLabel {{
    color: #A8A29E;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.7px;
    background: transparent;
    padding: 2px 0;
}}

QLabel#muted {{
    color: {THEME['text_muted']};
    font-size: 12px;
    background: transparent;
}}

QLabel#valueLabel {{
    color: {THEME['text']};
    font-size: 13px;
    font-weight: 600;
    background: transparent;
}}

QLabel#valueLabelMono {{
    color: {THEME['text_body']};
    font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
    font-size: 12px;
    background: transparent;
}}

QLabel#pillBadge {{
    background-color: {THEME['primary_soft']};
    color: #FDE68A;
    border: 1px solid #786221;
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}}

/* ---------- Input ---------- */
QLineEdit {{
    background-color: {THEME['input_bg']};
    color: {THEME['text']};
    border: 1px solid {THEME['border']};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    selection-background-color: {THEME['primary']};
    selection-color: #0A0A0D;
}}
QLineEdit:hover {{
    border: 1px solid #4A4758;
}}
QLineEdit:focus {{
    border: 1.5px solid {THEME['primary']};
    background-color: #13131A;
}}
QLineEdit:disabled, QLineEdit:read-only {{
    color: {THEME['text_muted']};
    background-color: #09090C;
    border-color: {THEME['border_subtle']};
}}

/* ---------- Combobox ---------- */
QComboBox {{
    background-color: {THEME['input_bg']};
    color: {THEME['text']};
    border: 1px solid {THEME['border']};
    border-radius: 8px;
    padding: 7px 12px;
    min-height: 24px;
    font-weight: 500;
}}
QComboBox:hover {{
    border: 1px solid #4A4758;
    background-color: #14141C;
}}
QComboBox:focus {{
    border: 1.5px solid {THEME['primary']};
}}
QComboBox:disabled {{
    color: {THEME['text_muted']};
    background-color: {THEME['bg']};
}}
QComboBox::drop-down {{
    border: none;
    width: 30px;
    subcontrol-origin: padding;
    subcontrol-position: top right;
}}
QComboBox::down-arrow {{
    image: none;
    width: 10px;
    height: 10px;
    margin-right: 8px;
}}
QComboBox::drop-down:open {{
    border-left: 1px solid {THEME['border']};
}}
QComboBox QAbstractItemView {{
    background-color: {THEME['card']};
    color: {THEME['text']};
    border: 1px solid {THEME['border']};
    border-radius: 8px;
    selection-background-color: {THEME['primary']};
    selection-color: #0A0A0D;
    outline: none;
    padding: 6px;
}}
QComboBox QAbstractItemView::item {{
    background-color: transparent;
    color: {THEME['text']};
    padding: 8px 12px;
    min-height: 26px;
    border-radius: 6px;
    border: none;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {THEME['card_hover']};
    color: {THEME['text']};
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {THEME['primary']};
    color: #0A0A0D;
}}

/* ---------- Fallback QCheckBox ---------- */
QCheckBox {{
    color: {THEME['text_body']};
    spacing: 10px;
    background: transparent;
    font-weight: 500;
}}

/* ---------- Standard QPushButton fallback ---------- */
QPushButton {{
    background-color: {THEME['card_alt']};
    color: {THEME['text']};
    border: 1px solid {THEME['border']};
    border-radius: 9px;
    padding: 9px 16px;
    font-size: 13px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton:hover {{
    background-color: {THEME['card_hover']};
    border-color: #524E60;
}}
QPushButton:pressed {{
    background-color: {THEME['border']};
}}
QPushButton:disabled {{
    color: {THEME['text_muted']};
    background-color: #0C0C10;
    border-color: {THEME['border_subtle']};
}}

/* ---------- Progress bar ---------- */
QProgressBar {{
    background-color: {THEME['input_bg']};
    border: 1px solid {THEME['border']};
    border-radius: 6px;
    text-align: center;
    color: {THEME['text']};
    font-size: 10px;
    font-weight: 700;
    min-height: 10px;
    max-height: 10px;
}}
QProgressBar::chunk {{
    background-color: {THEME['primary']};
    border-radius: 5px;
}}

/* ---------- Scroll area ---------- */
QScrollArea {{
    background-color: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {THEME['border']};
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {THEME['primary']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* ---------- Log / PlainTextEdit ---------- */
QPlainTextEdit {{
    background-color: #070709;
    color: {THEME['text_body']};
    border: 1px solid {THEME['border_subtle']};
    border-radius: 10px;
    padding: 12px;
    font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
    font-size: 12px;
    selection-background-color: {THEME['primary']};
    selection-color: #0A0A0D;
}}

/* ---------- Glossary table-like rows ---------- */
QFrame#glossRow {{
    background-color: {THEME['card']};
    border: 1px solid {THEME['border_subtle']};
    border-radius: 8px;
}}
QFrame#glossRow:hover {{
    border-color: {THEME['border']};
    background-color: {THEME['card_alt']};
}}
QFrame#glossHeader {{
    background-color: {THEME['card_alt']};
    border: 1px solid {THEME['border']};
    border-radius: 8px;
}}
QFrame#glossHeader QLabel {{
    color: #A8A29E;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    background: transparent;
}}

/* ---------- Status bar ---------- */
QStatusBar {{
    background-color: {THEME['panel']};
    color: {THEME['text_muted']};
    border-top: 1px solid {THEME['border_subtle']};
    font-size: 12px;
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
    "clock":     "🕐",
    "search":    "🔍",
    "sparkle":   "✨",
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


def resolved_model_config(config: dict) -> dict:
    """Giá trị model SẼ THỰC SỰ CHẠY, do main.py resolve.

    GUI không hardcode default riêng: get_active_model_config() đã chứa default
    theo từng nhánh (uncen 2048, còn lại 1024) và đọc key trong config trước.
    Hardcode lệch ở GUI khiến ô nhập hiển thị sai so với giá trị chạy thật.
    """
    from main import get_active_model_config
    return get_active_model_config(config)


def glossary_file_for(project_name: str) -> str:
    """Đường dẫn file glossary riêng của phim (khớp pipeline._load_glossary_terms)."""
    safe = re.sub(r"[^\w\-_]", "_", project_name) if project_name else ""
    return f"glossary_{safe}.yaml" if safe else ""


def load_glossary_files(project_name: str) -> list:
    """Đọc term từ các file glossary mà pipeline thực sự nạp (cùng thứ tự ưu tiên).

    Pipeline ưu tiên file sau ghi đè file trước:
      glossary_learned.yaml → glossary_uncen.yaml → glossary_common.yaml → glossary_<project>.yaml
    """
    from core.pipeline import _merge_glossary_files

    cfg = load_config()
    mode = str((cfg.get("translation", {}) or {}).get("mode", "uncen") or "uncen")
    learn = bool((cfg.get("pipeline", {}) or {}).get("learn_glossary", False))
    paths = []
    if mode.lower() == "uncen":
        if learn and os.path.exists("glossary_learned.yaml"):
            paths.append("glossary_learned.yaml")
        paths.append("glossary_uncen.yaml")
    paths.append("glossary_common.yaml")
    per_film = glossary_file_for(project_name)
    if per_film:
        paths.append(per_film)
    return _merge_glossary_files(*paths)


def save_glossary_terms(project_name: str, terms: list) -> str:
    """Ghi danh sách term vào glossary_<project>.yaml (schema source_term/target_term)."""
    path = glossary_file_for(project_name)
    if not path:
        return ""
    rows = []
    for t in terms:
        src = (t.get("source_term") or t.get("source") or "").strip()
        tgt = (t.get("target_term") or t.get("target") or "").strip()
        if not src or not tgt:
            continue
        hint = (t.get("context_hint") or t.get("context") or "").strip()
        row = {"source_term": src, "target_term": tgt}
        if hint:
            row["context_hint"] = hint
        rows.append(row)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(rows, f, allow_unicode=True, sort_keys=False)
    return path


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


class StudioComboBox(QComboBox):
    """QComboBox with a crisp vector chevron arrow drawn on the right."""
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        c = QColor(THEME["text_body"] if self.isEnabled() else THEME["text_muted"])
        pen = QPen(c, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        cx = self.width() - 16.0
        cy = self.height() / 2.0
        p.drawLine(QPointF(cx - 4.0, cy - 2.0), QPointF(cx, cy + 2.5))
        p.drawLine(QPointF(cx, cy + 2.5), QPointF(cx + 4.0, cy - 2.0))
        p.end()


def make_vector_icon(kind: str, size: int = 16, color: str = "#F8FAFC") -> QIcon:
    """Draw crisp geometric studio vector icons via QPainter (zero external assets)."""
    scale = 2
    px = size * scale
    pix = QPixmap(px, px)
    pix.fill(Qt.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    pen = QPen(c, 1.75 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    m = 3.0 * scale
    w = px - 2 * m
    h = px - 2 * m

    if kind == "play":
        path = QPainterPath()
        path.moveTo(m + w * 0.22, m + h * 0.14)
        path.lineTo(m + w * 0.86, m + h * 0.5)
        path.lineTo(m + w * 0.22, m + h * 0.86)
        path.closeSubpath()
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPath(path)
    elif kind == "stop":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        r = QRectF(m + w * 0.2, m + h * 0.2, w * 0.6, h * 0.6)
        p.drawRoundedRect(r, 1.2 * scale, 1.2 * scale)
    elif kind == "plus":
        p.drawLine(QPointF(px / 2, m + 1), QPointF(px / 2, px - m - 1))
        p.drawLine(QPointF(m + 1, px / 2), QPointF(px - m - 1, px / 2))
    elif kind == "trash":
        p.drawLine(QPointF(m + 1, m + h * 0.22), QPointF(px - m - 1, m + h * 0.22))
        p.drawRoundedRect(QRectF(m + w * 0.2, m + h * 0.22, w * 0.6, h * 0.72), 2 * scale, 2 * scale)
        p.drawLine(QPointF(m + w * 0.35, m + h * 0.08), QPointF(m + w * 0.65, m + h * 0.08))
    elif kind == "folder" or kind == "open":
        path = QPainterPath()
        path.moveTo(m, m + h * 0.2)
        path.lineTo(m + w * 0.38, m + h * 0.2)
        path.lineTo(m + w * 0.48, m + h * 0.34)
        path.lineTo(m + w, m + h * 0.34)
        path.lineTo(m + w, m + h * 0.86)
        path.lineTo(m, m + h * 0.86)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "save":
        # Sleek checkmark inside rounded badge
        p.drawRoundedRect(QRectF(m, m, w, h), 2.5 * scale, 2.5 * scale)
        p.drawLine(QPointF(m + w * 0.26, m + h * 0.52), QPointF(m + w * 0.44, m + h * 0.70))
        p.drawLine(QPointF(m + w * 0.44, m + h * 0.70), QPointF(m + w * 0.76, m + h * 0.32))
    elif kind == "tool" or kind == "sparkle":
        cx, cy = px / 2, px / 2
        r_out = w * 0.48
        r_in = w * 0.16
        path = QPainterPath()
        path.moveTo(cx, cy - r_out)
        path.quadTo(cx + r_in, cy - r_in, cx + r_out, cy)
        path.quadTo(cx + r_in, cy + r_in, cx, cy + r_out)
        path.quadTo(cx - r_in, cy + r_in, cx - r_out, cy)
        path.quadTo(cx - r_in, cy - r_in, cx, cy - r_out)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPath(path)
    elif kind == "workspace" or kind == "home":
        gap = 2.2 * scale
        hw = (w - gap) / 2
        hh = (h - gap) / 2
        p.drawRoundedRect(QRectF(m, m, hw, hh), 2 * scale, 2 * scale)
        p.drawRoundedRect(QRectF(m + hw + gap, m, hw, hh), 2 * scale, 2 * scale)
        p.drawRoundedRect(QRectF(m, m + hh + gap, hw, hh), 2 * scale, 2 * scale)
        p.drawRoundedRect(QRectF(m + hw + gap, m + hh + gap, hw, hh), 2 * scale, 2 * scale)
    elif kind == "settings":
        for idx, ratio in enumerate([0.25, 0.5, 0.75]):
            y = m + h * ratio
            p.drawLine(QPointF(m, y), QPointF(m + w, y))
            kx = m + w * (0.3 if idx % 2 == 0 else 0.7)
            p.setBrush(QColor(THEME["panel"]))
            p.drawEllipse(QPointF(kx, y), 2.3 * scale, 2.3 * scale)
    elif kind == "book":
        p.drawRoundedRect(QRectF(m + w * 0.1, m, w * 0.8, h), 2.5 * scale, 2.5 * scale)
        p.drawLine(QPointF(m + w * 0.32, m), QPointF(m + w * 0.32, m + h))
    elif kind == "log":
        p.drawRoundedRect(QRectF(m, m + h * 0.08, w, h * 0.84), 2.5 * scale, 2.5 * scale)
        p.drawLine(QPointF(m + w * 0.22, m + h * 0.38), QPointF(m + w * 0.38, m + h * 0.5))
        p.drawLine(QPointF(m + w * 0.38, m + h * 0.5), QPointF(m + w * 0.22, m + h * 0.62))
        p.drawLine(QPointF(m + w * 0.48, m + h * 0.62), QPointF(m + w * 0.72, m + h * 0.62))
    elif kind == "exit":
        p.drawLine(QPointF(m + w * 0.2, m + h * 0.2), QPointF(m + w * 0.8, m + h * 0.8))
        p.drawLine(QPointF(m + w * 0.8, m + h * 0.2), QPointF(m + w * 0.2, m + h * 0.8))
    elif kind == "search":
        r = w * 0.34
        cx, cy = m + r + 1, m + r + 1
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.drawLine(QPointF(cx + r * 0.72, cy + r * 0.72), QPointF(m + w, m + h))
    else:
        p.drawEllipse(QRectF(m, m, w, h))

    p.end()
    return QIcon(pix)


# ---------------------------------------------------------------------------
# Signal bridge for thread-safe Qt updates
# ---------------------------------------------------------------------------
class LogBridge(QObject):
    message = Signal(str, str)       # tag, text
    finished = Signal(int)           # exit code
    progress = Signal(int, int)      # cur, total
    ollama_status = Signal(bool, str)


_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))")


def _clean_log_text(text: str) -> str:
    """Remove terminal control sequences before displaying subprocess output."""
    text = _ANSI_ESCAPE_RE.sub("", text)
    return "".join(char for char in text if char in "\n\t" or ord(char) >= 32)


# ---------------------------------------------------------------------------
# Luxury Studio Custom Widgets
# ---------------------------------------------------------------------------
class AnimatedButton(QPushButton):
    """Tactile button with smooth 140ms hover/press transitions and top inner specular highlight."""
    PALETTES = {
        "primary": {
            "bg":       "#D4AF37",
            "hover":    "#E5C158",
            "press":    "#B59024",
            "border":   "#F3D87D",
            "text":     "#0A0A0D",
        },
        "secondary": {
            "bg":       "#1D1D26",
            "hover":    "#282734",
            "press":    "#15151C",
            "border":   "#353342",
            "text":     "#FAF8F5",
        },
        "accent": {
            "bg":       "#262012",
            "hover":    "#362D18",
            "press":    "#1C180E",
            "border":   "#856B24",
            "text":     "#FDE68A",
        },
        "danger": {
            "bg":       "#1F1418",
            "hover":    "#E11D48",
            "press":    "#BE123C",
            "border":   "#7F1D32",
            "text":     "#FDA4AF",
        },
        "warning": {
            "bg":       "#211B12",
            "hover":    "#D97706",
            "press":    "#B45309",
            "border":   "#78350F",
            "text":     "#FCD34D",
        },
        "ghost": {
            "bg":       "#16161D",
            "hover":    "#23222D",
            "press":    "#1A1A22",
            "border":   "#262532",
            "text":     "#A8A29E",
        },
    }

    def __init__(self, text: str = "", variant: str = "secondary", icon_kind: str = "", parent=None):
        super().__init__(text, parent)
        self._variant = variant if variant in self.PALETTES else "secondary"
        self._icon_kind = icon_kind
        self.setProperty("variant", self._variant)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(38)

        pal = self.PALETTES[self._variant]
        self._cur_bg = QColor(pal["bg"])
        self._cur_border = QColor(pal["border"])

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_step)

        if icon_kind:
            self.setIcon(make_vector_icon(icon_kind, 15, pal["text"]))
            self.setIconSize(QSize(16, 16))

    def set_variant(self, variant: str):
        self._variant = variant if variant in self.PALETTES else "secondary"
        self.setProperty("variant", self._variant)
        pal = self.PALETTES[self._variant]
        self._cur_bg = QColor(pal["bg"])
        self._cur_border = QColor(pal["border"])
        if self._icon_kind:
            self.setIcon(make_vector_icon(self._icon_kind, 15, pal["text"]))
        self.update()

    def _animate_to(self, target_hex: str):
        self._anim.stop()
        self._anim.setStartValue(self._cur_bg)
        self._anim.setEndValue(QColor(target_hex))
        self._anim.start()

    def _on_anim_step(self, val):
        if isinstance(val, QColor):
            self._cur_bg = val
            self.update()

    def enterEvent(self, event):
        if self.isEnabled():
            self._animate_to(self.PALETTES[self._variant]["hover"])
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isEnabled():
            self._animate_to(self.PALETTES[self._variant]["bg"])
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self.isEnabled() and event.button() == Qt.LeftButton:
            self._animate_to(self.PALETTES[self._variant]["press"])
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.isEnabled():
            target = self.PALETTES[self._variant]["hover"] if self.rect().contains(event.pos()) else self.PALETTES[self._variant]["bg"]
            self._animate_to(target)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        radius = 9.0

        pal = self.PALETTES[self._variant]
        if not self.isEnabled():
            bg = QColor("#0B0F18")
            border = QColor(THEME["border_subtle"])
            text_col = QColor(THEME["text_muted"])
        else:
            bg = self._cur_bg
            border = QColor(pal["border"])
            text_col = QColor("#FFFFFF") if (self._variant in ("danger", "warning") and self.underMouse()) else QColor(pal["text"])

        # Fill
        p.setPen(QPen(border, 1.0))
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(rect, radius, radius)

        # Subtle top specular highlight for luxury tactile depth
        if self.isEnabled():
            hi = QColor(255, 255, 255, 38 if self._variant == "primary" else 16)
            p.setPen(QPen(hi, 1.0))
            p.drawLine(QPointF(rect.left() + radius, rect.top() + 0.5),
                       QPointF(rect.right() - radius, rect.top() + 0.5))

        # Icon + Text centered (or left-aligned if nav/sidebar)
        content_rect = rect.adjusted(12, 0, -12, 0)
        fm = p.fontMetrics()
        label = self.text()
        icon = self.icon()
        has_icon = not icon.isNull()
        icon_w = 16 if has_icon else 0
        gap = 8 if (has_icon and label) else 0
        total_w = icon_w + gap + (fm.horizontalAdvance(label) if label else 0)

        start_x = content_rect.center().x() - total_w / 2.0
        cy = rect.center().y()

        if has_icon:
            pix = icon.pixmap(QSize(16, 16))
            if not self.isEnabled():
                p.setOpacity(0.35)
            p.drawPixmap(int(start_x), int(cy - 8), pix)
            p.setOpacity(1.0)
            start_x += icon_w + gap

        if label:
            font = self.font()
            font.setWeight(QFont.DemiBold)
            p.setFont(font)
            p.setPen(text_col)
            text_rect = QRectF(start_x, rect.top(), max(20.0, rect.right() - start_x), rect.height())
            p.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, label)

        p.end()


class ToggleSwitch(QCheckBox):
    """iOS/Studio animated pill toggle switch — 100% compatible with QCheckBox API."""
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(28)
        self._pos = 1.0 if self.isChecked() else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def _on_anim(self, val):
        self._pos = float(val)
        self.update()

    def _animate(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool):
        super().setChecked(checked)
        self._pos = 1.0 if checked else 0.0
        self.update()

    def sizeHint(self):
        fm = self.fontMetrics()
        w = 48 + (fm.horizontalAdvance(self.text()) + 12 if self.text() else 0)
        return QSize(w, 28)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        sw_w, sw_h = 40.0, 22.0
        sw_y = (self.height() - sw_h) / 2.0
        track_rect = QRectF(2.0, sw_y, sw_w, sw_h)

        # Interpolate track color between #201F29 and #D4AF37
        off_c = QColor("#201F29")
        on_c = QColor(THEME["primary"])
        r = int(off_c.red() + (on_c.red() - off_c.red()) * self._pos)
        g = int(off_c.green() + (on_c.green() - off_c.green()) * self._pos)
        b = int(off_c.blue() + (on_c.blue() - off_c.blue()) * self._pos)
        track_col = QColor(r, g, b)

        border_col = QColor("#F3D87D") if self._pos > 0.5 else (
            QColor("#4A4758") if self.underMouse() else QColor(THEME["border"])
        )

        p.setPen(QPen(border_col, 1.2))
        p.setBrush(QBrush(track_col))
        p.drawRoundedRect(track_rect, sw_h / 2.0, sw_h / 2.0)

        # Thumb: warm ivory when off -> deep obsidian when on gold track
        thumb_d = 16.0
        min_x = track_rect.left() + 3.0
        max_x = track_rect.right() - thumb_d - 3.0
        thumb_x = min_x + (max_x - min_x) * self._pos
        thumb_rect = QRectF(thumb_x, sw_y + 3.0, thumb_d, thumb_d)

        t_off = QColor("#D6D3CD")
        t_on = QColor("#0A0A0D")
        tr = int(t_off.red() + (t_on.red() - t_off.red()) * self._pos)
        tg = int(t_off.green() + (t_on.green() - t_off.green()) * self._pos)
        tb = int(t_off.blue() + (t_on.blue() - t_off.blue()) * self._pos)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(tr, tg, tb))
        p.drawEllipse(thumb_rect)

        # Label text
        if self.text():
            p.setPen(QColor(THEME["text"] if self.isChecked() else THEME["text_body"]))
            text_rect = QRectF(sw_w + 14.0, 0, self.width() - sw_w - 14.0, self.height())
            p.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, self.text())

        p.end()


class Card(QFrame):
    """Elevated studio surface card with subtle top specular border."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 18, 20, 18)
        self._layout.setSpacing(12)

    def addLayout(self, layout):
        self._layout.addLayout(layout)

    def addWidget(self, w, stretch: int = 0):
        self._layout.addWidget(w, stretch)

    def addStretch(self):
        self._layout.addStretch()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # Subtle top edge highlight for studio glass depth
        p.setPen(QPen(QColor(255, 255, 255, 14), 1.0))
        p.drawLine(QPointF(12, 1), QPointF(self.width() - 12, 1))
        p.end()


class StatBadge(QFrame):
    """Compact KPI card for the Workspace header strip."""
    def __init__(self, label: str, value: str, accent_hex: str = "#6366F1", parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self._accent = QColor(accent_hex)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(10)

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {accent_hex}; font-size: 11px; background: transparent;")
        h.addWidget(self.dot, 0)

        v = QVBoxLayout()
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        self.lbl = QLabel(label.upper())
        self.lbl.setStyleSheet(f"color: {THEME['text_muted']}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        self.val = QLabel(value)
        self.val.setStyleSheet(f"color: {THEME['text']}; font-size: 12px; font-weight: 700; background: transparent;")
        v.addWidget(self.lbl)
        v.addWidget(self.val)
        h.addLayout(v, 1)

    def set_value(self, value: str, accent_hex: str = ""):
        self.val.setText(value)
        if accent_hex:
            self.dot.setStyleSheet(f"color: {accent_hex}; font-size: 11px; background: transparent;")


class FieldRow(QWidget):
    """Label + widget row with consistent spacing."""
    def __init__(self, label_text: str, widget: QWidget, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
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
        self.setStyleSheet("background: transparent;")
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.line = QLineEdit()
        self.line.setPlaceholderText(placeholder)
        h.addWidget(self.line, 1)
        self.btn = AnimatedButton("Chọn file", variant="secondary", icon_kind="folder")
        self.btn.setToolTip("Chọn file video hoặc SRT…")
        self.btn.setMinimumWidth(108)
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
    """Read-only hiển thị output folder + nút mở nhanh thư mục."""
    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.line = QLineEdit()
        self.line.setPlaceholderText(placeholder)
        self.line.setReadOnly(True)
        self.line.setObjectName("outputPathDisplay")
        h.addWidget(self.line, 1)

        self.open_btn = AnimatedButton("Mở folder", variant="ghost", icon_kind="open")
        self.open_btn.setToolTip("Mở thư mục Output trong Explorer")
        self.open_btn.setMinimumWidth(108)
        self.open_btn.clicked.connect(self._open_dir)
        h.addWidget(self.open_btn, 0)

    def _open_dir(self):
        raw = self.text()
        target = raw if os.path.isdir(raw) else (os.path.dirname(raw) if raw else os.getcwd())
        if not target or not os.path.exists(target):
            target = os.getcwd()
        if sys.platform == "win32":
            os.startfile(target)
        elif sys.platform == "darwin":
            subprocess.run(["open", target])
        else:
            subprocess.run(["xdg-open", target])

    def text(self) -> str:
        return self.line.text().strip()

    def setText(self, s: str):
        self.line.setText(s)


class StatusPill(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusPill")
        self.setProperty("status", "success")
        self.setFixedHeight(28)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 12, 0)
        h.setSpacing(4)
        self.label = QLabel("●  Sẵn sàng")
        self.label.setAlignment(Qt.AlignCenter)
        h.addWidget(self.label)

    def set_status(self, text: str, kind: str = "success"):
        self.setProperty("status", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.label.setText("●  " + text)


class NavButton(QPushButton):
    def __init__(self, icon_kind: str, text: str, parent=None):
        super().__init__(parent)
        self._icon_kind = icon_kind
        self.setObjectName("navButton")
        self.setCheckable(True)
        self.setText(f"  {text}")
        self.setIcon(make_vector_icon(icon_kind, 16, "#D6D3CD"))
        self.setIconSize(QSize(16, 16))
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(42)
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool):
        col = "#FDE68A" if checked else "#D6D3CD"
        self.setIcon(make_vector_icon(self._icon_kind, 16, col))


class IconButton(AnimatedButton):
    """Square button with vector icon only — for row actions."""
    def __init__(self, icon: str, tooltip: str = "", variant: str = "ghost", parent=None):
        kind = "trash" if icon == ICON.get("trash") else ""
        super().__init__("" if kind else icon, variant=variant, icon_kind=kind, parent=parent)
        self.setProperty("iconOnly", True)
        self.setToolTip(tooltip)
        self.setFixedSize(36, 34)


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

        # Project Hero Header Card + KPI strip
        header = Card()
        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        proj_col = QVBoxLayout()
        proj_col.setSpacing(4)
        proj_col.addWidget(self._make_section_title("Không gian dự án"))
        self.project_name_lbl = QLabel("—")
        self.project_name_lbl.setObjectName("projectTitle")
        self.project_name_lbl.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {THEME['text']}; letter-spacing: 0.3px;")
        proj_col.addWidget(self.project_name_lbl)
        self.project_meta_lbl = QLabel("")
        self.project_meta_lbl.setObjectName("projectMeta")
        proj_col.addWidget(self.project_meta_lbl)
        top_row.addLayout(proj_col, 2)

        # Quick KPI Strip
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self.ollama_kpi = StatBadge("Ollama Engine", "Đang kiểm tra…", THEME["info"])
        self.glossary_kpi = StatBadge("Glossary", "0 mục", THEME["primary"])
        self.whisper_kpi = StatBadge("Whisper ASR", "large-v3 · CUDA", THEME["secondary"])
        kpi_row.addWidget(self.ollama_kpi)
        kpi_row.addWidget(self.glossary_kpi)
        kpi_row.addWidget(self.whisper_kpi)
        top_row.addLayout(kpi_row, 3)

        header.addLayout(top_row)
        v.addWidget(header)

        # Two-column body
        body = QHBoxLayout()
        body.setSpacing(16)
        body.addLayout(self._build_files_col(), 3)
        body.addLayout(self._build_side_col(), 2)
        v.addLayout(body, 1)

    def _make_section_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("sectionTitle")
        lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        return lbl

    def _build_files_col(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(16)
        files_card = Card()
        files_card.addWidget(self._make_section_title("Nguồn dữ liệu & Đầu ra"))

        # Interactive Dropzone Banner
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setProperty("dragActive", False)
        dz_layout = QHBoxLayout(self.drop_zone)
        dz_layout.setContentsMargins(16, 12, 16, 12)
        dz_layout.setSpacing(12)
        dz_icon = QLabel()
        dz_icon.setPixmap(make_vector_icon("folder", 22, THEME["primary"]).pixmap(QSize(24, 24)))
        dz_icon.setStyleSheet("background: transparent;")
        dz_layout.addWidget(dz_icon, 0)
        dz_text_col = QVBoxLayout()
        dz_text_col.setSpacing(2)
        dz_title = QLabel("Kéo & Thả file Video (.mp4, .mkv) hoặc Phụ đề (.srt) vào cửa sổ")
        dz_title.setStyleSheet(f"color: {THEME['text']}; font-weight: 600; font-size: 12px; background: transparent;")
        dz_sub = QLabel("Hoặc bấm 'Chọn file' bên dưới — thư mục Output và cấu hình phim sẽ tự động khởi tạo")
        dz_sub.setWordWrap(True)
        dz_sub.setStyleSheet(f"color: {THEME['text_muted']}; font-size: 11px; background: transparent;")
        dz_text_col.addWidget(dz_title)
        dz_text_col.addWidget(dz_sub)
        dz_layout.addLayout(dz_text_col, 1)
        files_card.addWidget(self.drop_zone)

        # Input
        self.input_row = FileInputRow("Chọn file video hoặc SRT…")
        files_card.addWidget(FieldRow("File Input (Video / SRT)", self.input_row))

        # Recent projects dropdown
        recent_row = QHBoxLayout()
        recent_row.setSpacing(8)
        recent_label = QLabel("Dự án gần đây:")
        recent_label.setObjectName("fieldLabel")
        recent_row.addWidget(recent_label, 0)
        self.recent_combo = StudioComboBox()
        self.recent_combo.setObjectName("recent_combo")
        self.recent_combo.setMinimumWidth(240)
        self.recent_combo.setPlaceholderText("(Chưa có lịch sử)")
        recent_row.addWidget(self.recent_combo, 1)
        files_card.addLayout(recent_row)

        # Output - read-only, tự sinh từ input
        self.output_row = OutputPathDisplay("Output folder sẽ tự tạo cùng tên project…")
        files_card.addWidget(FieldRow("Thư mục Output (Tự động)", self.output_row))

        col.addWidget(files_card)
        col.addStretch()
        return col

    def _build_side_col(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(16)  # Fixed: clear 16px separation between cards

        # Settings card
        settings_card = Card()
        settings_card.addWidget(self._make_section_title("Cấu hình dịch nhanh"))
        self.src_combo = StudioComboBox()
        self.src_combo.setObjectName("src_combo")
        for v in LANGUAGES.values():
            self.src_combo.addItem(v)
        settings_card.addWidget(FieldRow("Ngôn ngữ nguồn", self.src_combo))

        self.tgt_combo = StudioComboBox()
        self.tgt_combo.setObjectName("tgt_combo")
        for v in TARGET_LANGUAGES.values():
            self.tgt_combo.addItem(v)
        settings_card.addWidget(FieldRow("Ngôn ngữ đích", self.tgt_combo))

        self.mode_combo = StudioComboBox()
        self.mode_combo.setObjectName("mode_combo")
        for v in TRANSLATION_MODES.values():
            self.mode_combo.addItem(v)
        settings_card.addWidget(FieldRow("Chế độ AI Model", self.mode_combo))
        col.addWidget(settings_card)

        # Actions card
        actions_card = Card()
        actions_card.addWidget(self._make_section_title("Điều khiển Pipeline"))

        self.start_btn = AnimatedButton("Bắt đầu dịch phụ đề", variant="primary", icon_kind="play")
        self.start_btn.setMinimumHeight(44)
        actions_card.addWidget(self.start_btn)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.fix_btn = AnimatedButton("Sửa lỗi SRT (Rules)", variant="accent", icon_kind="sparkle")
        self.save_btn = AnimatedButton("Lưu cấu hình", variant="secondary", icon_kind="save")
        btn_row.addWidget(self.fix_btn, 1)
        btn_row.addWidget(self.save_btn, 1)
        actions_card.addLayout(btn_row)

        # Progress
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("valueLabel")
        self.progress_label.setStyleSheet(f"color: {THEME['accent']}; font-size: 12px;")
        actions_card.addWidget(self.progress_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate until first window parsed
        self.progress.setVisible(False)
        actions_card.addWidget(self.progress)

        col.addWidget(actions_card)
        col.addStretch()
        return col

    def set_status(self, text: str):
        self.project_meta_lbl.setText(text)

    def set_drag_active(self, active: bool):
        self.drop_zone.setProperty("dragActive", active)
        self.drop_zone.style().unpolish(self.drop_zone)
        self.drop_zone.style().polish(self.drop_zone)


class SettingsPage(QWidget):
    def __init__(self, app: "App", parent=None):
        super().__init__(parent)
        self.app = app
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        # Header bar with Save button right on Settings page too
        top_bar = QHBoxLayout()
        title_lbl = QLabel("Cài đặt Hệ thống & Mô hình AI")
        title_lbl.setObjectName("sectionTitle")
        title_lbl.setStyleSheet("font-size: 17px; font-weight: 800;")
        top_bar.addWidget(title_lbl)
        top_bar.addStretch()
        self.quick_save_btn = AnimatedButton("Lưu mọi cài đặt", variant="primary", icon_kind="save")
        self.quick_save_btn.clicked.connect(lambda: self.app._save_config())
        top_bar.addWidget(self.quick_save_btn)
        outer.addLayout(top_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("settingsScroll")
        outer.addWidget(scroll, 1)

        container = QWidget()
        container.setMaximumWidth(860)
        cl = QVBoxLayout(container)
        cl.setContentsMargins(4, 4, 12, 16)
        cl.setSpacing(16)

        # Row 1: Whisper + Pipeline switches side by side
        top_grid = QHBoxLayout()
        top_grid.setSpacing(16)

        # Whisper card
        whisper_card = Card()
        title = QLabel("Nhận diện giọng nói (Whisper ASR)")
        title.setObjectName("sectionTitle")
        whisper_card.addWidget(title)

        self.model_combo = StudioComboBox()
        self.model_combo.setObjectName("model_combo")
        self.model_combo.setMinimumWidth(180)
        self.model_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        whisper_card.addWidget(FieldRow("Kích thước Model", self.model_combo))

        self.device_combo = StudioComboBox()
        self.device_combo.setObjectName("device_combo")
        self.device_combo.setMinimumWidth(120)
        self.device_combo.addItems(["cuda", "cpu"])
        whisper_card.addWidget(FieldRow("Thiết bị xử lý (Device)", self.device_combo))
        whisper_card.addStretch()
        top_grid.addWidget(whisper_card, 2)

        # Pipeline card with Animated ToggleSwitches
        pipe_card = Card()
        title2 = QLabel("Tùy chọn Pipeline & Tự học")
        title2.setObjectName("sectionTitle")
        pipe_card.addWidget(title2)

        self.workers_edit = QLineEdit()
        self.workers_edit.setPlaceholderText("1")
        self.workers_edit.setMaximumWidth(140)
        pipe_card.addWidget(FieldRow("Số luồng dịch song song (Workers)", self.workers_edit))

        self.split_cb = ToggleSwitch("Tách phụ đề dài (> 6 giây)")
        pipe_card.addWidget(self.split_cb)
        self.glossary_cb = ToggleSwitch("Bật từ điển Glossary khi dịch")
        pipe_card.addWidget(self.glossary_cb)
        self.literal_cb = ToggleSwitch("Literal Mode (Uncen: dịch sát nghĩa, tắt polish)")
        pipe_card.addWidget(self.literal_cb)
        self.learn_glossary_cb = ToggleSwitch("Tự học từ vựng Uncen qua các dự án (Glossary Miner)")
        pipe_card.addWidget(self.learn_glossary_cb)
        pipe_card.addStretch()
        top_grid.addWidget(pipe_card, 3)

        cl.addLayout(top_grid)

        # LLM card (advanced) — organized in a clean 3-column grid
        llm_card = Card()
        llm_title = QLabel("Thông số LLM / Ollama (Advanced)")
        llm_title.setObjectName("sectionTitle")
        llm_card.addWidget(llm_title)

        llm_grid = QGridLayout()
        llm_grid.setHorizontalSpacing(16)
        llm_grid.setVerticalSpacing(12)

        self.num_predict_edit = QLineEdit()
        self.num_predict_edit.setPlaceholderText("theo mode")
        llm_grid.addWidget(FieldRow("num_predict (Token tối đa)", self.num_predict_edit), 0, 0)

        self.num_ctx_edit = QLineEdit()
        self.num_ctx_edit.setPlaceholderText("6144")
        llm_grid.addWidget(FieldRow("num_ctx (Cửa sổ ngữ cảnh)", self.num_ctx_edit), 0, 1)

        self.repeat_penalty_edit = QLineEdit()
        self.repeat_penalty_edit.setPlaceholderText("1.15")
        llm_grid.addWidget(FieldRow("repeat_penalty (Chống lặp)", self.repeat_penalty_edit), 0, 2)

        self.temperature_edit = QLineEdit()
        self.temperature_edit.setPlaceholderText("0.05")
        llm_grid.addWidget(FieldRow("temperature (Độ sáng tạo)", self.temperature_edit), 1, 0)

        self.timeout_edit = QLineEdit()
        self.timeout_edit.setPlaceholderText("180 (giây)")
        llm_grid.addWidget(FieldRow("timeout (Thời gian chờ, giây)", self.timeout_edit), 1, 1)

        self.ollama_url_edit = QLineEdit()
        self.ollama_url_edit.setPlaceholderText("http://localhost:11434/api/generate")
        llm_grid.addWidget(FieldRow("Ollama Endpoint URL", self.ollama_url_edit), 1, 2)

        llm_card.addLayout(llm_grid)
        cl.addWidget(llm_card)

        # Pipeline card 2 - advanced
        pipe2_card = Card()
        pipe2_title = QLabel("Kiểm soát Lỗi & Lưu trạng thái (Checkpointing)")
        pipe2_title.setObjectName("sectionTitle")
        pipe2_card.addWidget(pipe2_title)

        p2_row = QHBoxLayout()
        p2_row.setSpacing(16)
        self.max_retries_edit = QLineEdit()
        self.max_retries_edit.setPlaceholderText("5")
        self.max_retries_edit.setMaximumWidth(180)
        p2_row.addWidget(FieldRow("Số lần thử lại tối đa (Max retries)", self.max_retries_edit))

        self.checkpoint_edit = QLineEdit()
        self.checkpoint_edit.setPlaceholderText("20")
        self.checkpoint_edit.setMaximumWidth(180)
        p2_row.addWidget(FieldRow("Chu kỳ lưu Checkpoint (số dòng)", self.checkpoint_edit))
        p2_row.addStretch()
        pipe2_card.addLayout(p2_row)

        cl.addWidget(pipe2_card)
        cl.addStretch()
        scroll.setWidget(container)


class GlossaryPage(QWidget):
    def __init__(self, app: "App", parent=None):
        super().__init__(parent)
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # Toolbar Row 1: Title + Count Badge + Action Buttons
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        title = QLabel("Từ điển Glossary")
        title.setObjectName("sectionTitle")
        title.setStyleSheet("font-size: 17px; font-weight: 800; padding-right: 4px;")
        toolbar.addWidget(title, 0, Qt.AlignVCenter)

        self.count_badge = QLabel("0 mục")
        self.count_badge.setObjectName("pillBadge")
        self.count_badge.setFixedHeight(26)
        self.count_badge.setAlignment(Qt.AlignCenter)
        toolbar.addWidget(self.count_badge, 0, Qt.AlignVCenter)
        toolbar.addStretch(1)

        self.mine_btn = AnimatedButton("Học từ DB ngay", variant="accent", icon_kind="sparkle")
        self.mine_btn.setMinimumWidth(142)
        self.mine_btn.setToolTip("Quét toàn bộ các dự án đã dịch trong Database để trích xuất từ vựng mới vào glossary_learned.yaml")
        toolbar.addWidget(self.mine_btn)

        self.add_btn = AnimatedButton("Thêm từ", variant="secondary", icon_kind="plus")
        self.add_btn.setMinimumWidth(104)
        toolbar.addWidget(self.add_btn)

        self.import_btn = AnimatedButton("Import CSV", variant="ghost", icon_kind="open")
        self.import_btn.setMinimumWidth(112)
        toolbar.addWidget(self.import_btn)
        self.export_btn = AnimatedButton("Export CSV", variant="ghost", icon_kind="save")
        self.export_btn.setMinimumWidth(112)
        toolbar.addWidget(self.export_btn)

        self.save_btn = AnimatedButton("Lưu vào Phim", variant="primary", icon_kind="save")
        self.save_btn.setMinimumWidth(128)
        toolbar.addWidget(self.save_btn)
        v.addLayout(toolbar)

        # Toolbar Row 2: Live Search Filter
        search_bar = QHBoxLayout()
        search_bar.setSpacing(10)
        self.search_edit = QLineEdit()
        self.search_edit.addAction(make_vector_icon("search", 14, THEME["primary"]), QLineEdit.LeadingPosition)
        self.search_edit.setPlaceholderText("Tìm nhanh theo từ tiếng Nhật (Source), nghĩa tiếng Việt (Target) hoặc ghi chú (Context)…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_rows)
        search_bar.addWidget(self.search_edit, 1)
        v.addLayout(search_bar)

        # Header row
        header = QFrame()
        header.setObjectName("glossHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(10)
        for txt, w in [("SOURCE (TIẾNG NHẬT)", 3), ("TARGET (TIẾNG VIỆT)", 3), ("CONTEXT / QUY TẮC CẤM DỊCH SAI", 4), ("", 0)]:
            lbl = QLabel(txt)
            h.addWidget(lbl, w)
        v.addWidget(header)

        # Scroll area for rows
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_inner = QWidget()
        self.scroll_inner.setStyleSheet("background: transparent;")
        self.rows_layout = QVBoxLayout(self.scroll_inner)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)
        self.rows_layout.setSpacing(6)
        self.rows_layout.addStretch()
        self.scroll.setWidget(self.scroll_inner)
        v.addWidget(self.scroll, 1)

        self.rows = []  # list of dicts: {frame, src, tgt, ctx}
        self.add_btn.clicked.connect(lambda: self._add_row(scroll_to_bottom=True))
        self.save_btn.clicked.connect(self._save)
        self.import_btn.clicked.connect(self._import_csv)
        self.export_btn.clicked.connect(self._export_csv)
        self.mine_btn.clicked.connect(self._mine_learned_terms)

    def _update_count_badge(self):
        visible = sum(1 for r in self.rows if not r["frame"].isHidden())
        total = len(self.rows)
        if self.search_edit.text().strip():
            self.count_badge.setText(f"{visible} / {total} mục")
        else:
            self.count_badge.setText(f"{total} mục")
        if hasattr(self, "app") and hasattr(self.app, "page_workspace"):
            self.app.page_workspace.glossary_kpi.set_value(f"{total} thuật ngữ", THEME["primary"])

    def _filter_rows(self, query: str):
        q = (query or "").strip().lower()
        for r in self.rows:
            if not q:
                r["frame"].setVisible(True)
            else:
                s = r["src"].text().lower()
                t = r["tgt"].text().lower()
                c = r["ctx"].text().lower()
                r["frame"].setVisible(q in s or q in t or q in c)
        self._update_count_badge()

    def _mine_learned_terms(self):
        try:
            from core.database import Database
            from core.glossary_miner import GlossaryMiner, write_global_learned_glossary
            db = Database()
            miner = GlossaryMiner(db, self.app.config)
            candidates = miner.mine_all_projects()
            out_path = write_global_learned_glossary(candidates, path="glossary_learned.yaml")
            proj = (self.app.config.get("project", {}) or {}).get("name", "")
            self.load(load_glossary_files(proj))
            msg = f"Đã học {len(candidates)} từ vựng từ DB → {out_path or 'glossary_learned.yaml'}"
            self.app.set_status(msg, "success")
            self.app.log_message("OK", msg)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi học từ vựng", str(e))

    def _import_csv(self):
        """Import glossary từ file CSV (header: source,target,context)."""
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
            self._update_count_badge()
            if hasattr(self, 'app') and self.app:
                self.app.set_status(f"Đã import {rows_added} dòng", "success")
        except Exception as e:
            QMessageBox.critical(self, "Lỗi import", str(e))

    def _export_csv(self):
        """Export glossary hiện tại ra file CSV."""
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
            QMessageBox.critical(self, "Lỗi export", str(e))

    def _add_row(self, src="", tgt="", ctx="", scroll_to_bottom=False):
        idx = self.rows_layout.count() - 1
        row = QFrame()
        row.setObjectName("glossRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)

        src_edit = QLineEdit(src)
        src_edit.setPlaceholderText("おちんちん")
        h.addWidget(src_edit, 3)
        tgt_edit = QLineEdit(tgt)
        tgt_edit.setPlaceholderText("cặc / dương vật")
        h.addWidget(tgt_edit, 3)
        ctx_edit = QLineEdit(ctx)
        ctx_edit.setPlaceholderText("Ghi chú hoặc: KHÔNG dịch \"...\"")
        h.addWidget(ctx_edit, 4)
        del_btn = IconButton(ICON["trash"], "Xoá dòng", variant="danger")
        h.addWidget(del_btn, 0)

        self.rows_layout.insertWidget(idx, row)
        data = {"frame": row, "src": src_edit, "tgt": tgt_edit, "ctx": ctx_edit}
        del_btn.clicked.connect(lambda: self._remove_row(data))
        self.rows.append(data)
        self._update_count_badge()

        if scroll_to_bottom:
            QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().maximum()
            ))

    def _remove_row(self, data):
        data["frame"].deleteLater()
        if data in self.rows:
            self.rows.remove(data)
        self._update_count_badge()

    def load(self, glossary: list):
        self.scroll_inner.setUpdatesEnabled(False)
        try:
            for r in self.rows:
                r["frame"].deleteLater()
            self.rows.clear()
            if not glossary:
                self._add_row()
                return
            for entry in glossary:
                self._add_row(
                    entry.get("source_term") or entry.get("source", ""),
                    entry.get("target_term") or entry.get("target", ""),
                    entry.get("context_hint") or entry.get("context", ""),
                )
        finally:
            self.scroll_inner.setUpdatesEnabled(True)
            self._update_count_badge()

    def _save(self):
        project = (self.app.config.get("project", {}) or {}).get("name", "")
        terms = []
        for r in self.rows:
            s = r["src"].text().strip()
            t = r["tgt"].text().strip()
            c = r["ctx"].text().strip()
            if s and t:
                entry = {"source_term": s, "target_term": t}
                if c:
                    entry["context_hint"] = c
                terms.append(entry)
        try:
            path = save_glossary_terms(project, terms)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi lưu glossary", str(e))
            return
        if not path:
            QMessageBox.warning(self, "Chưa chọn phim",
                                "Chọn file input trước để biết tạo glossary cho phim nào.")
            return
        self.app.set_status(f"Đã lưu {len(terms)} glossary → {path}", "success")
        self.app.log_message("OK", f"Đã ghi {len(terms)} mục vào {path}")


class LogPage(QWidget):
    def __init__(self, app: "App", parent=None):
        super().__init__(parent)
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        title = QLabel("Nhật ký Hệ thống (Live Console)")
        title.setObjectName("sectionTitle")
        title.setStyleSheet("font-size: 17px; font-weight: 800;")
        toolbar.addWidget(title)
        toolbar.addStretch()

        # Tag filter
        self.filter_combo = StudioComboBox()
        self.filter_combo.setObjectName("filter_combo")
        self.filter_combo.addItems(["ALL", "INFO", "OK", "WARN", "ERR"])
        self.filter_combo.setMinimumWidth(90)
        filter_lbl = QLabel("Mức độ:")
        filter_lbl.setObjectName("muted")
        toolbar.addWidget(filter_lbl)
        toolbar.addWidget(self.filter_combo)

        # Auto-scroll toggle
        self.autoscroll_cb = ToggleSwitch("Tự cuộn")
        self.autoscroll_cb.setChecked(True)
        toolbar.addWidget(self.autoscroll_cb)

        self.clear_btn = AnimatedButton("Xoá log", variant="ghost", icon_kind="trash")
        toolbar.addWidget(self.clear_btn)
        self.open_btn = AnimatedButton("Mở thư mục", variant="secondary", icon_kind="open")
        toolbar.addWidget(self.open_btn)
        v.addLayout(toolbar)

        # Log text
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        self.text.setPlaceholderText("Log sẵn sàng. Bấm 'Bắt đầu dịch phụ đề' ở Workspace để khởi chạy pipeline.")
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
        self.setWindowTitle("Subtitle Translator — Studio Indigo Edition")
        self.setMinimumSize(1040, 680)
        self.resize(1180, 760)

        # Enable drag-drop
        self.setAcceptDrops(True)

        self.config = load_config()
        self.config_path = "config.yaml"
        self.is_running = False
        self.process = None
        self._page_anim = None
        self.log_queue = queue.Queue()
        self.log_bridge = LogBridge()
        self.log_bridge.message.connect(self._on_log_message)
        self.log_bridge.finished.connect(self._on_pipeline_finished)
        self.log_bridge.progress.connect(self._update_progress)
        self.log_bridge.ollama_status.connect(self._on_ollama_status)

        self.setStyleSheet(QSS)
        self._build_ui()
        self._refresh_from_config()
        self._poll_log_queue()
        self._check_ollama_async()

    def _kill_running_process(self):
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
        """Accept file drops on main window and highlight dropzone."""
        if event.mimeData().hasUrls():
            self.page_workspace.set_drag_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.page_workspace.set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        """Handle dropped file → auto-fill input field."""
        self.page_workspace.set_drag_active(False)
        urls = event.mimeData().urls()
        if not urls:
            return
        for url in urls:
            path = url.toLocalFile()
            if path and os.path.splitext(path)[1].lower() in ('.srt', '.mp4', '.mkv', '.mov', '.avi', '.webm'):
                self.page_workspace.input_row.setText(path)
                self._on_input_changed(path)
                self.set_status(f"Đã nhận file: {os.path.basename(path)}", "success")
                event.acceptProposedAction()
                return
        event.ignore()

    # ---- UI build -----------------------------------------------------
    def _build_ui(self):
        # Top bar
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(60)
        th = QHBoxLayout(topbar)
        th.setContentsMargins(20, 0, 20, 0)
        th.setSpacing(12)

        brand_icon = QLabel()
        brand_icon.setPixmap(make_vector_icon("sparkle", 18, THEME["primary"]).pixmap(QSize(20, 20)))
        brand_icon.setStyleSheet("background: transparent;")
        th.addWidget(brand_icon)

        title = QLabel("Subtitle Translator")
        title.setObjectName("appTitle")
        th.addWidget(title)

        badge = QLabel("STUDIO PRO")
        badge.setObjectName("appBadge")
        badge.setFixedHeight(22)
        badge.setAlignment(Qt.AlignCenter)
        th.addWidget(badge, 0, Qt.AlignVCenter)

        th.addStretch()
        self.project_title = QLabel("")
        self.project_title.setObjectName("projectTitle")
        th.addWidget(self.project_title, 0, Qt.AlignVCenter)
        self.status_pill = StatusPill()
        th.addWidget(self.status_pill, 0, Qt.AlignVCenter)

        # Body
        body = QWidget()
        bh = QHBoxLayout(body)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(0)

        # Sidebar
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(232)
        sl = QVBoxLayout(self.sidebar)
        sl.setContentsMargins(14, 18, 14, 16)
        sl.setSpacing(6)

        nav_lbl = QLabel("ĐIỀU HƯỚNG")
        nav_lbl.setObjectName("sidebarSection")
        sl.addWidget(nav_lbl)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        nav_items = [
            ("workspace", "workspace", "Workspace"),
            ("settings",  "settings",  "Cài đặt AI & Hệ thống"),
            ("glossary",  "book",      "Từ điển Glossary"),
            ("log",       "log",       "Nhật ký (Console)"),
        ]
        self.nav_buttons = {}
        for i, (key, icon_kind, label) in enumerate(nav_items):
            btn = NavButton(icon_kind, label)
            self.nav_group.addButton(btn, i)
            sl.addWidget(btn)
            self.nav_buttons[key] = btn
            btn.clicked.connect(lambda checked, k=key: self._switch_section(k))

        sl.addStretch()
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        sl.addWidget(sep)

        ctrl_lbl = QLabel("ĐIỀU KHIỂN NHANH")
        ctrl_lbl.setObjectName("sidebarSection")
        sl.addWidget(ctrl_lbl)

        self.stop_btn = AnimatedButton("Dừng Pipeline", variant="warning", icon_kind="stop")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        sl.addWidget(self.stop_btn)

        self.exit_btn = AnimatedButton("Thoát ứng dụng", variant="danger", icon_kind="exit")
        self.exit_btn.setObjectName("exitButton")
        self.exit_btn.clicked.connect(self._on_close)
        sl.addWidget(self.exit_btn)

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
        cl.setContentsMargins(24, 20, 24, 20)
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
        if page and self.stack.currentWidget() is not page:
            self.stack.setCurrentWidget(page)
            # Smooth 130ms fade-in transition; clean up effect when finished
            eff = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(115)
            anim.setStartValue(0.75)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda p=page: p.setGraphicsEffect(None))
            self._page_anim = anim
            anim.start()
        if key in self.nav_buttons:
            self.nav_buttons[key].setChecked(True)

    # ---- Ollama Health Check -------------------------------------------
    def _check_ollama_async(self):
        def _worker():
            active = resolved_model_config(self.config)
            model_name = active.get("name", "LLM")
            short_model = model_name.split("/")[-1]
            url = active.get("ollama_url", "http://localhost:11434/api/generate")
            base_url = url.split("/api/")[0] + "/api/tags"
            try:
                req = urllib.request.Request(base_url, method="GET")
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    if resp.status == 200:
                        self.log_bridge.ollama_status.emit(True, f"Online · {short_model}")
                        return
            except Exception:
                pass
            self.log_bridge.ollama_status.emit(False, f"Offline · {short_model}")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_ollama_status(self, online: bool, label: str):
        color = THEME["success"] if online else THEME["error"]
        self.page_workspace.ollama_kpi.set_value(label, color)

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
        w_model = w.get("model_size", "large-v3")
        w_dev = w.get("device", "cuda")
        self.page_settings.model_combo.setCurrentText(w_model)
        self.page_settings.device_combo.setCurrentText(w_dev)
        self.page_workspace.whisper_kpi.set_value(f"{w_model} · {w_dev.upper()}", THEME["secondary"])

        pipe = self.config.get("pipeline", {})
        self.page_settings.workers_edit.setText(str(pipe.get("num_workers", 1)))
        self.page_settings.split_cb.setChecked(pipe.get("split_long_subs", True))
        self.page_settings.glossary_cb.setChecked(pipe.get("enable_glossary", True))
        self.page_settings.literal_cb.setChecked(self.config.get("translation", {}).get("literal_mode", False))
        self.page_settings.learn_glossary_cb.setChecked(pipe.get("learn_glossary", True))

        # LLM settings — đọc giá trị main.py thực sự resolve
        active = resolved_model_config(self.config)
        self.page_settings.num_predict_edit.setText(str(active["num_predict"]))
        self.page_settings.num_ctx_edit.setText(str(active["num_ctx"]))
        self.page_settings.repeat_penalty_edit.setText(str(active["repeat_penalty"]))
        self.page_settings.temperature_edit.setText(str(active["temperature"]))
        self.page_settings.timeout_edit.setText(str(active["timeout"]))
        self.page_settings.ollama_url_edit.setText(active["ollama_url"])

        self.page_settings.max_retries_edit.setText(str(pipe.get("max_retries", 5)))
        self.page_settings.checkpoint_edit.setText(str(pipe.get("checkpoint_interval", 20)))

        terms = load_glossary_files(p.get("name", ""))
        self.page_glossary.load(terms)
        self.page_workspace.glossary_kpi.set_value(f"{len(terms)} thuật ngữ", THEME["primary"])

        # Populate recent projects dropdown
        self.page_workspace.recent_combo.blockSignals(True)
        self.page_workspace.recent_combo.clear()
        recents = self._load_recent_projects()
        for r in recents:
            self.page_workspace.recent_combo.addItem(r)
        if recents:
            self.page_workspace.recent_combo.setCurrentIndex(0)
        self.page_workspace.recent_combo.blockSignals(False)

        # Project header
        proj_name = p.get("name", "—")
        self.page_workspace.project_name_lbl.setText(proj_name)
        meta = f"{src}  →  {tgt}   ·   Chế độ: {mode.upper()}"
        self.page_workspace.project_meta_lbl.setText(meta)
        self.project_title.setText(f"{truncate(p.get('input_srt', ''), 36)}")

    def _save_config(self):
        p = self.config.setdefault("project", {})
        p["input_srt"] = self.page_workspace.input_row.text()
        p["output_srt"] = self.page_workspace.output_row.text()
        p["name"] = os.path.splitext(os.path.basename(p["input_srt"] or "project"))[0] or "my_project"
        for code, name in LANGUAGES.items():
            if name == self.page_workspace.src_combo.currentText():
                p["source_lang"] = name
                self.config.setdefault("whisper", {})["language"] = code
                try:
                    from main import WINDOW_PRESETS
                    preset = WINDOW_PRESETS.get(code, WINDOW_PRESETS["ja"])
                    if self.config.get("window") != preset:
                        self.config["window"] = dict(preset)
                except Exception:
                    pass
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
        pipe["learn_glossary"] = self.page_settings.learn_glossary_cb.isChecked()
        self.config["translation"]["literal_mode"] = self.page_settings.literal_cb.isChecked()

        # LLM settings (save to models.{active_mode})
        # Fallback lấy từ main.py resolve (đúng default theo từng mode) thay vì
        # viết số ở đây — tránh lệch khi main.py đổi default.
        mode = self.config.get("translation", {}).get("mode", "uncen")
        llm_cfg = self.config.setdefault("models", {}).setdefault(mode, {})
        dflt = resolved_model_config(self.config)
        try:
            llm_cfg["num_predict"] = int(self.page_settings.num_predict_edit.text() or dflt["num_predict"])
        except ValueError:
            llm_cfg["num_predict"] = dflt["num_predict"]
        try:
            llm_cfg["num_ctx"] = int(self.page_settings.num_ctx_edit.text() or dflt["num_ctx"])
        except ValueError:
            llm_cfg["num_ctx"] = dflt["num_ctx"]
        try:
            llm_cfg["repeat_penalty"] = float(self.page_settings.repeat_penalty_edit.text() or dflt["repeat_penalty"])
        except ValueError:
            llm_cfg["repeat_penalty"] = dflt["repeat_penalty"]
        try:
            llm_cfg["temperature"] = float(self.page_settings.temperature_edit.text() or dflt["temperature"])
        except ValueError:
            llm_cfg["temperature"] = dflt["temperature"]
        try:
            llm_cfg["timeout"] = int(self.page_settings.timeout_edit.text() or dflt["timeout"])
        except ValueError:
            llm_cfg["timeout"] = dflt["timeout"]
        llm_cfg["ollama_url"] = self.page_settings.ollama_url_edit.text().strip() or dflt["ollama_url"]

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
        self._check_ollama_async()

    def _save_recent_project(self, input_path: str):
        """Lưu input path vào recent projects (max 5)."""
        if not input_path or not os.path.exists(input_path):
            return
        recents = self.config.setdefault("recent_projects", [])
        recents = [r for r in recents if r != input_path]
        recents.insert(0, input_path)
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
            self._on_input_changed(path)
            self.set_status(f"Đã chọn: {os.path.basename(path)}", "success")

    def _on_input_changed(self, path: str):
        """Khi user chọn input mới -> tự tạo folder output cùng tên project, giống main.py."""
        if not path or not os.path.exists(path):
            return
        try:
            from main import _auto_setup_project_paths
        except Exception:
            return
        paths = _auto_setup_project_paths(self.config, path)
        self.page_workspace.output_row.setText(paths["output_dir"])
        proj_name = self.config.get('project', {}).get('name', '')
        if hasattr(self, 'project_title'):
            self.project_title.setText(proj_name)
        if hasattr(self.page_workspace, 'project_name_lbl'):
            self.page_workspace.project_name_lbl.setText(proj_name)
        terms = load_glossary_files(proj_name)
        self.page_glossary.load(terms)
        self._save_recent_project(path)

    def _start_translation(self):
        if self.is_running:
            QMessageBox.warning(self, "Đang chạy", "Pipeline đang chạy, vui lòng đợi hoàn tất.")
            return

        self._save_config()
        self.is_running = True
        self.page_workspace.start_btn.setEnabled(False)
        self.page_workspace.start_btn.setText("Đang dịch phụ đề…")
        self.stop_btn.setEnabled(True)
        self.page_workspace.progress.setRange(0, 0)
        self.page_workspace.progress.setVisible(True)
        self.page_workspace.progress_label.setText("Đang khởi động pipeline…")
        self.set_status("Đang chạy", "warning")
        self._switch_section("log")

        t = threading.Thread(target=self._run_pipeline, daemon=True)
        t.start()

    def _run_pipeline(self):
        try:
            cmd = [sys.executable, "main.py",
                   "--input", self.page_workspace.input_row.text(),
                   # GUI khong cap stdin: cho phep main.py bo qua input() o
                   # 2 diem hoi (Ollama loi, project da xong) thay vi treo/EOFError.
                   "--non-interactive"]
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
                elif "[+]" in line or "[ok]" in low or "✓" in line:
                    tag = "OK"

                m = re.search(r"[Ww]indow\s+(\d+)/(\d+)", line)
                if m:
                    try:
                        cur = int(m.group(1))
                        total = int(m.group(2))
                        if total > 0:
                            self.log_bridge.progress.emit(cur, total)
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
            proj_name = (self.config.get("project", {}) or {}).get("name", "")
            processor = RuleBasedPostProcessor()
            processor.set_character_names(self.config.get("character_names", []))
            processor.set_glossary_terms(load_glossary_files(proj_name))
            with open(input_srt, "r", encoding="utf-8") as f:
                lines = f.read().split("\n")
            fixed = processor.process_lines(lines)
            with open(input_srt + ".fixed", "w", encoding="utf-8") as f:
                f.write("\n".join(fixed))
            self.set_status("Đã sửa SRT", "success")
            self.log_message("OK", f"Đã sửa SRT → {input_srt}.fixed")
        except Exception as e:
            self.set_status("Lỗi", "error")
            self.log_message("ERR", f"Sửa SRT thất bại: {e}")

    # ---- Logging -------------------------------------------------------
    def log_message(self, tag: str, text: str):
        self.log_bridge.message.emit(tag, text)

    def _on_log_message(self, tag: str, text: str):
        current_filter = self.page_log.filter_combo.currentText()
        tag_normalized = tag.upper()
        if tag in ("[OK]", "OK"):
            tag_normalized = "OK"
        elif tag not in ("INFO", "WARN", "ERR"):
            tag_normalized = "INFO"

        if current_filter != "ALL" and tag_normalized != current_filter:
            return

        clean = _clean_log_text(text)
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        badge_colors = {
            "OK":   ("#10B981", "#A7F3D0"),
            "WARN": ("#F59E0B", "#FDE68A"),
            "ERR":  ("#F43F5E", "#FECDD3"),
            "INFO": ("#D4AF37", "#D6D3CD"),
        }
        badge_col, text_col = badge_colors.get(tag_normalized, ("#D4AF37", "#D6D3CD"))
        escaped = html.escape(clean)
        html_line = (
            f'<span style="color:#6E6A64;">[{ts}]</span> '
            f'<span style="color:{badge_col}; font-weight:700;">[{tag_normalized:<4}]</span> '
            f'<span style="color:{text_col};">{escaped}</span>'
        )
        self.page_log.text.appendHtml(html_line)

        if self.page_log.autoscroll_cb.isChecked():
            bar = self.page_log.text.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _update_progress(self, cur: int, total: int):
        """Update progress bar từ log parsing. Chạy an toàn trên UI thread."""
        bar = self.page_workspace.progress
        if bar.maximum() != total:
            bar.setRange(0, total)
        bar.setValue(cur)
        pct = int((cur / total) * 100) if total > 0 else 0
        self.page_workspace.progress_label.setText(f"Đang dịch Window {cur}/{total} ({pct}%)")

    def _on_pipeline_finished(self, rc: int):
        self.is_running = False
        self.page_workspace.start_btn.setEnabled(True)
        self.page_workspace.start_btn.setText("Bắt đầu dịch phụ đề")
        self.stop_btn.setEnabled(False)
        self.page_workspace.progress.setVisible(False)
        if rc == 0:
            self.page_workspace.progress_label.setText("✓ Hoàn tất dịch phụ đề!")
            self.set_status("Hoàn tất", "success")
            # Refresh glossary in case new terms were learned
            proj_name = (self.config.get("project", {}) or {}).get("name", "")
            self.page_glossary.load(load_glossary_files(proj_name))
        else:
            self.page_workspace.progress_label.setText(f"✗ Dừng với mã lỗi ({rc})")
            self.set_status(f"Lỗi (exit {rc})", "error")

    def _poll_log_queue(self):
        QTimer.singleShot(500, self._poll_log_queue)

    # ---- Status --------------------------------------------------------
    def set_status(self, text: str, kind: str = "info"):
        self.status_pill.set_status(text, kind)
        self.statusBar.showMessage(text)

    # ---- Close ---------------------------------------------------------
    def _on_stop_clicked(self):
        """Nút 'Dừng' trên sidebar: kill subprocess main.py, giữ GUI chạy."""
        if not self.is_running:
            return
        self.log_bridge.message.emit("WARN", "Yêu cầu dừng pipeline...")
        self.stop_btn.setEnabled(False)
        self._kill_running_process()

    def _on_close(self):
        if self.is_running:
            if QMessageBox.question(self, "Đang chạy", "Pipeline đang chạy. Thoát?",
                                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        self.close()


def main():
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
