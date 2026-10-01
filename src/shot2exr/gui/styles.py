"""Dark, flat stylesheet in the spirit of VFX pipeline tools."""

ACCENT = "#e8a33d"
OK = "#6cc070"
WARN = "#e8a33d"
ERROR = "#e06666"
MUTED = "#8a8f98"

STATE_COLORS = {"DETECTED": OK, "INFERRED": WARN, "UNKNOWN": ERROR}

STYLESHEET = f"""
QWidget {{ background: #1e2025; color: #d7dae0; font-size: 10pt; }}
QGroupBox {{ border: 1px solid #33363d; border-radius: 4px; margin-top: 14px; padding: 8px 8px 6px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {ACCENT}; font-weight: bold; letter-spacing: 1px; }}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{
    background: #15171b; border: 1px solid #383c44; border-radius: 3px; padding: 3px 5px;
    selection-background-color: #3d5a80;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit[invalid="true"] {{ border-color: {ERROR}; }}
QPushButton {{ background: #2c3038; border: 1px solid #3a3f48; border-radius: 3px; padding: 5px 12px; }}
QPushButton:hover {{ background: #353a44; }}
QPushButton:pressed {{ background: #24272d; }}
QPushButton:disabled {{ color: #5d6169; background: #24262b; border-color: #2c2f35; }}
QPushButton#primary {{ background: #8a5f1f; border-color: {ACCENT}; color: #fff; font-weight: bold; }}
QPushButton#primary:disabled {{ background: #3a3326; color: #7d7464; border-color: #4a4232; }}
QProgressBar {{ background: #15171b; border: 1px solid #383c44; border-radius: 3px; text-align: center; height: 16px; }}
QProgressBar::chunk {{ background: {ACCENT}; }}
QLabel#preview {{ font-family: monospace; color: {ACCENT}; font-size: 11pt; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#value {{ color: #ffffff; }}
QScrollArea {{ border: none; }}
QCheckBox::indicator {{ width: 14px; height: 14px; }}
"""
