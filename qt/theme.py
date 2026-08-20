"""Visual language — Bitcoin-Qt inspired, orange accent, premium look.
Provides both LIGHT and DARK themes. Call apply_theme(app, dark=True/False)
to switch at runtime and persist the preference.
"""

from __future__ import annotations

import json
import os

# ── Shared accent ──────────────────────────────────────────────────────────
BITCOIN_ORANGE = "#F7931A"

# ── Light palette ──────────────────────────────────────────────────────────
WINDOW_BG = "#F2F1F0"
PANEL_BG  = "#FFFFFF"
BORDER    = "#C6C6C6"
TEXT      = "#1A1A1A"
MUTED     = "#6D6D6D"
GREEN     = "#1E7E34"
RED       = "#C0392B"
PENDING   = "#7A7A7A"
BLUE      = "#2471A3"

# ── Dark palette ───────────────────────────────────────────────────────────
DARK_WINDOW_BG = "#1C1C1C"
DARK_PANEL_BG  = "#252525"
DARK_BORDER    = "#3A3A3A"
DARK_TEXT      = "#E8E8E8"
DARK_MUTED     = "#999999"
DARK_GREEN     = "#27AE60"
DARK_RED       = "#E74C3C"
DARK_PENDING   = "#888888"
DARK_INPUT_BG  = "#2D2D2D"
DARK_HEADER_BG = "#1E1E1E"


# ── QSS generator ──────────────────────────────────────────────────────────

def _build_qss(dark: bool) -> str:
    if dark:
        WB  = DARK_WINDOW_BG
        PB  = DARK_PANEL_BG
        BD  = DARK_BORDER
        TX  = DARK_TEXT
        MT  = DARK_MUTED
        GR  = DARK_GREEN
        RD  = DARK_RED
        PD  = DARK_PENDING
        IB  = DARK_INPUT_BG
        HB  = DARK_HEADER_BG
        ACC = BITCOIN_ORANGE
        # Nav hover / selection tints
        NAV_HOVER_BG   = "#2A2A2A"
        NAV_HOVER_FG   = "#FFA133"
        NAV_SEL_FG     = "#F7931A"
        MENU_BG        = "#2D2D2D"
        MENU_HOVER_BG  = "#3A3A3A"
        MENU_HOVER_FG  = "#FFA133"
        SEL_BG         = "#3D2E1A"
        SEL_FG         = "#F7931A"
        TT_BG          = "#2D2000"
        SBBAR_BG       = "#1A1A1A"
        PROG_BG        = "#2D2D2D"
        GRID           = "#2E2E2E"
        ALT_ROW        = "#202020"
    else:
        WB  = WINDOW_BG
        PB  = PANEL_BG
        BD  = BORDER
        TX  = TEXT
        MT  = MUTED
        GR  = GREEN
        RD  = RED
        PD  = PENDING
        IB  = PANEL_BG
        HB  = "#F7F7F7"
        ACC = BITCOIN_ORANGE
        NAV_HOVER_BG   = "#FEF3E2"
        NAV_HOVER_FG   = "#E08612"
        NAV_SEL_FG     = "#C07A10"
        MENU_BG        = "#FFFFFF"
        MENU_HOVER_BG  = "#FEF3E2"
        MENU_HOVER_FG  = "#D07B0E"
        SEL_BG         = "#FEF3E2"
        SEL_FG         = TX
        TT_BG          = "#FFFBE6"
        SBBAR_BG       = "#EDEDED"
        PROG_BG        = "#F5F5F5"
        GRID           = "#F0F0F0"
        ALT_ROW        = "#FAFAFA"

    return f"""
/* ─── Global ─────────────────────────────────────────────── */
QMainWindow, QDialog, QWidget {{
    background-color: {WB};
    color: {TX};
    font-family: "Segoe UI", "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}}

/* ─── Menu bar ────────────────────────────────────────────── */
QMenuBar {{
    background: {HB};
    border-bottom: 1px solid {BD};
    padding: 2px 6px;
}}
QMenuBar::item {{ padding: 4px 10px; border-radius: 3px; }}
QMenuBar::item:selected {{ background: {MENU_HOVER_BG}; color: {MENU_HOVER_FG}; }}
QMenu {{
    background: {MENU_BG};
    border: 1px solid {BD};
    border-radius: 4px;
    padding: 4px 0;
    color: {TX};
}}
QMenu::item {{ padding: 6px 24px; }}
QMenu::item:selected {{ background: {MENU_HOVER_BG}; color: {MENU_HOVER_FG}; }}
QMenu::separator {{ height: 1px; background: {BD}; margin: 4px 0; }}

/* ─── Toolbar / nav tabs ──────────────────────────────────── */
QToolBar#mainToolbar {{
    background: {PB};
    border: none;
    border-bottom: 2px solid {BD};
    spacing: 0px;
    padding: 0px;
}}
QToolButton#navButton {{
    background: transparent;
    border: none;
    border-bottom: 3px solid transparent;
    padding: 10px 26px 8px 26px;
    margin: 0;
    color: {MT};
    font-size: 12px;
    font-weight: 500;
}}
QToolButton#navButton:hover {{
    background: {NAV_HOVER_BG};
    color: {NAV_HOVER_FG};
}}
QToolButton#navButton:checked {{
    background: transparent;
    border-bottom: 3px solid {ACC};
    color: {NAV_SEL_FG};
    font-weight: 700;
}}

/* ─── GroupBox ────────────────────────────────────────────── */
QGroupBox {{
    background: {PB};
    border: 1px solid {BD};
    border-radius: 6px;
    margin-top: 16px;
    padding: 14px 14px 12px 14px;
    font-weight: 600;
    font-size: 12px;
    color: {MT};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {MT};
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

/* ─── Input widgets ───────────────────────────────────────── */
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
QComboBox, QTextEdit {{
    background: {IB};
    border: 1px solid {BD};
    border-radius: 4px;
    padding: 6px 9px;
    selection-background-color: {ACC};
    selection-color: #FFFFFF;
    color: {TX};
}}
QLineEdit:focus, QPlainTextEdit:focus,
QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
    border: 1.5px solid {ACC};
    outline: none;
}}
QLineEdit:read-only {{ background: {WB}; color: {MT}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {MENU_BG};
    border: 1px solid {BD};
    selection-background-color: {SEL_BG};
    color: {TX};
}}

/* ─── Buttons ─────────────────────────────────────────────── */
QPushButton {{
    background: {IB};
    border: 1px solid {BD};
    border-radius: 5px;
    padding: 7px 18px;
    min-width: 80px;
    font-weight: 500;
    color: {TX};
}}
QPushButton:hover {{ background: {NAV_HOVER_BG}; border-color: {MT}; }}
QPushButton:pressed {{ background: {WB}; }}
QPushButton:disabled {{ color: {MT}; opacity: 0.5; }}

QPushButton#primaryButton {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #FFA133, stop:1 #F7931A
    );
    border: 1px solid #D97E0F;
    color: #FFFFFF;
    font-weight: 700;
    letter-spacing: 0.3px;
}}
QPushButton#primaryButton:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #FFAE55, stop:1 #FFA133
    );
}}
QPushButton#primaryButton:pressed {{
    background: #E08612;
}}
QPushButton#primaryButton:disabled {{
    background: #7A5220;
    border-color: #5A3A10;
    color: #BBA070;
}}

/* ─── Tables / Lists ──────────────────────────────────────── */
QTableWidget, QTableView, QTreeWidget, QListWidget {{
    background: {PB};
    border: 1px solid {BD};
    border-radius: 4px;
    gridline-color: {GRID};
    alternate-background-color: {ALT_ROW};
    selection-background-color: {SEL_BG};
    selection-color: {TX};
    outline: none;
    color: {TX};
}}
QHeaderView::section {{
    background: {HB};
    border: none;
    border-right: 1px solid {BD};
    border-bottom: 1px solid {BD};
    padding: 7px 10px;
    font-weight: 600;
    font-size: 11px;
    color: {MT};
    text-transform: uppercase;
    letter-spacing: 0.3px;
}}

/* ─── ScrollBars ──────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {WB};
    width: 10px;
    margin: 0;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {BD};
    min-height: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: {MT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {WB};
    height: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background: {BD};
    min-width: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{ background: {MT}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ─── Status bar ──────────────────────────────────────────── */
QStatusBar {{
    background: {SBBAR_BG};
    border-top: 1px solid {BD};
    font-size: 12px;
    color: {MT};
}}

/* ─── Progress bar ────────────────────────────────────────── */
QProgressBar {{
    border: 1px solid {BD};
    border-radius: 4px;
    background: {PROG_BG};
    text-align: center;
    max-height: 14px;
    color: {TX};
}}
QProgressBar::chunk {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACC}, stop:1 #FFA133
    );
    border-radius: 3px;
}}

/* ─── Labels (semantic) ───────────────────────────────────── */
QLabel#balanceValue {{
    font-size: 16px;
    font-weight: 700;
    color: {TX};
    letter-spacing: -0.3px;
}}
QLabel#balanceTotal {{
    font-size: 18px;
    font-weight: 800;
    color: {TX};
    letter-spacing: -0.5px;
}}
QLabel#pending  {{ color: {PD}; font-weight: 500; }}
QLabel#muted    {{ color: {MT}; font-size: 12px; }}
QLabel#positive {{ color: {GR}; font-weight: 700; }}
QLabel#negative {{ color: {RD}; font-weight: 700; }}

/* ─── Tabs ────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {BD};
    border-radius: 0 4px 4px 4px;
    background: {PB};
}}
QTabBar::tab {{
    background: {WB};
    border: 1px solid {BD};
    border-bottom: none;
    border-radius: 4px 4px 0 0;
    padding: 7px 16px;
    margin-right: 2px;
    font-weight: 500;
    color: {TX};
}}
QTabBar::tab:selected {{
    background: {PB};
    border-bottom-color: {PB};
    color: {NAV_SEL_FG};
    font-weight: 700;
}}
QTabBar::tab:hover:!selected {{ background: {NAV_HOVER_BG}; }}

/* ─── Misc ────────────────────────────────────────────────── */
QScrollArea {{ border: none; background: transparent; }}
QSplitter::handle {{ background: {BD}; }}
QCheckBox, QRadioButton {{ spacing: 8px; color: {TX}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}
QFrame[frameShape="4"],   /* HLine */
QFrame[frameShape="5"] {{  /* VLine */
    color: {BD};
}}
QToolTip {{
    background: {TT_BG};
    border: 1px solid {ACC};
    color: {TX};
    padding: 4px 8px;
    border-radius: 3px;
}}
"""


# ── Public API ─────────────────────────────────────────────────────────────

# Build the two precomputed stylesheets
QSS      = _build_qss(dark=False)   # light (backwards-compat)
QSS_DARK = _build_qss(dark=True)


def _prefs_path() -> str:
    """Per-user preferences file next to the running exe / script."""
    base = os.environ.get("BTPY_DATA_DIR") or os.path.expanduser("~/.ori")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "ui_prefs.json")


def load_dark_pref() -> bool:
    """Return saved dark-mode preference (default: False = light)."""
    try:
        with open(_prefs_path(), "r", encoding="utf-8") as f:
            return bool(json.load(f).get("dark_mode", False))
    except Exception:
        return False


def save_dark_pref(dark: bool) -> None:
    """Persist dark-mode preference to disk."""
    prefs: dict = {}
    try:
        with open(_prefs_path(), "r", encoding="utf-8") as f:
            prefs = json.load(f)
    except Exception:
        pass
    prefs["dark_mode"] = dark
    try:
        with open(_prefs_path(), "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except Exception:
        pass


def apply_theme(app, dark: bool) -> None:
    """Switch the running QApplication to light or dark theme."""
    app.setStyleSheet(QSS_DARK if dark else QSS)
    save_dark_pref(dark)
