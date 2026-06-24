# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

block_cipher = None

a = Analysis(
    ['src/novel_writer/app.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('logo.png', '.'),
    ],
    hiddenimports=[
        'novel_writer.core.llm.client',
        'novel_writer.core.llm.providers',
        'novel_writer.core.project_io',
        'novel_writer.core.exporter',
        'novel_writer.core.logger',
        'novel_writer.ui.main_window',
        'novel_writer.ui.chat_widget',
        'novel_writer.ui.project_widget',
        'novel_writer.ui.settings_widget',
        'PySide6.QtCore',
        'PySide6.QtWidgets',
        'PySide6.QtGui',
        'openai',
        'anthropic',
        'httpx',
        'httpcore',
        'pydantic',
        'ebooklib',
        'reportlab',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NovelWriter',
    icon='logo.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
