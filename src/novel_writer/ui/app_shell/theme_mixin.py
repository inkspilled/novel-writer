"""主题/菜单/Toast/关于 — MainWindow 外壳行为"""
from __future__ import annotations

from functools import partial

from PySide6.QtWidgets import QLabel, QMessageBox
from PySide6.QtGui import QAction, QKeySequence, QPixmap
from PySide6.QtCore import Qt, QTimer

from ..common.styles import build_style
from ...locales import t, set_language
from ...designsystem import get_theme_colors
from ...core.app_config import save_config as _save_app_config, ASSETS_DIR
from ...core.logger import get_logger

logger = get_logger(__name__)

class ThemeMixin:
    def _setup_menu(self):
        menubar = self.menuBar()

        # ── 文件：项目生命周期（入口 → 保存 → 导出 → 退出） ──
        self._file_menu = menubar.addMenu(t("menu_file"))
        self._new_action = QAction(t("menu_new_project"), self)
        self._new_action.setShortcut(QKeySequence.StandardKey.New)
        self._new_action.triggered.connect(self._new_project)
        self._file_menu.addAction(self._new_action)
        self._open_action = QAction(t("menu_open_project"), self)
        self._open_action.setShortcut(QKeySequence("Ctrl+O"))
        self._open_action.triggered.connect(self._open_project)
        self._file_menu.addAction(self._open_action)
        self._file_menu.addSeparator()
        self._save_action = QAction(t("menu_save_project"), self)
        self._save_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_action.triggered.connect(self._save_project)
        self._file_menu.addAction(self._save_action)
        self._file_menu.addSeparator()
        self._export_menu = self._file_menu.addMenu(t("menu_export"))
        self._export_txt_action = QAction(t("menu_export_txt"), self)
        self._export_txt_action.triggered.connect(partial(self._export, "txt"))
        self._export_menu.addAction(self._export_txt_action)
        self._export_epub_action = QAction(t("menu_export_epub"), self)
        self._export_epub_action.triggered.connect(partial(self._export, "epub"))
        self._export_menu.addAction(self._export_epub_action)
        self._export_pdf_action = QAction(t("menu_export_pdf"), self)
        self._export_pdf_action.triggered.connect(partial(self._export, "pdf"))
        self._export_menu.addAction(self._export_pdf_action)
        self._file_menu.addSeparator()
        self._exit_action = QAction(t("menu_exit"), self)
        self._exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self._exit_action.triggered.connect(self.close)
        self._file_menu.addAction(self._exit_action)

        # ── 模型 & 智能体 ──
        self._model_agent_menu = menubar.addMenu(t("menu_model_agent"))
        self._model_action = QAction(t("menu_model_open"), self)
        self._model_action.triggered.connect(self._open_model_settings)
        self._model_agent_menu.addAction(self._model_action)
        self._manage_action = QAction(t("menu_agent_manage"), self)
        self._manage_action.triggered.connect(self._open_agent_manage)
        self._model_agent_menu.addAction(self._manage_action)

        # ── 工作流：面板入口 / 一键载入默认 ──
        self._workflow_menu = menubar.addMenu(t("workflow_menu"))
        self._workflow_open_action = QAction(t("workflow_menu_open"), self)
        self._workflow_open_action.setShortcut(QKeySequence("Ctrl+Shift+W"))
        self._workflow_open_action.triggered.connect(self._open_workflow_panel)
        self._workflow_menu.addAction(self._workflow_open_action)
        self._workflow_menu.addSeparator()
        self._workflow_default_action = QAction(t("workflow_menu_default"), self)
        self._workflow_default_action.triggered.connect(self._load_default_workflow)
        self._workflow_menu.addAction(self._workflow_default_action)

        # ── 设置：所有偏好收拢（项目 / 外观） ──
        self._settings_menu = menubar.addMenu(t("menu_settings"))
        self._settings_action = QAction(t("menu_project_settings"), self)
        self._settings_action.setShortcut(QKeySequence("Ctrl+Shift+,"))
        self._settings_action.triggered.connect(self._open_project_settings)
        self._settings_menu.addAction(self._settings_action)
        self._appearance_action = QAction(t("menu_appearance_open"), self)
        self._appearance_action.setShortcut(QKeySequence("Ctrl+,"))
        self._appearance_action.triggered.connect(self._open_appearance)
        self._settings_menu.addAction(self._appearance_action)

        # ── 帮助 ──
        self._about_menu = menubar.addMenu(t("menu_help"))
        self._about_action = QAction(t("menu_about_app"), self)
        self._about_action.triggered.connect(self._show_about)
        self._about_menu.addAction(self._about_action)

    def _setup_menubar_icon(self):
        """在状态栏左侧显示应用小图标。"""
        status_icon = ASSETS_DIR / "status_icon.png"
        if status_icon.exists():
            label = QLabel()
            pixmap = QPixmap(str(status_icon)).scaled(
                18, 18, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            label.setPixmap(pixmap)
            label.setContentsMargins(8, 0, 0, 0)
            self.statusBar().addPermanentWidget(label)

    def _refresh_ui_texts(self):
        """切换语言后刷新所有 UI 文本。"""
        lang = self.config.get("language", "zh")
        set_language(lang)
        self.setWindowTitle(t("window_title"))
        # 菜单
        self._file_menu.setTitle(t("menu_file"))
        self._new_action.setText(t("menu_new_project"))
        self._open_action.setText(t("menu_open_project"))
        self._save_action.setText(t("menu_save_project"))
        self._exit_action.setText(t("menu_exit"))
        self._model_agent_menu.setTitle(t("menu_model_agent"))
        self._model_action.setText(t("menu_model_open"))
        self._manage_action.setText(t("menu_agent_manage"))
        self._settings_menu.setTitle(t("menu_settings"))
        self._settings_action.setText(t("menu_project_settings"))
        self._appearance_action.setText(t("menu_appearance_open"))
        self._about_menu.setTitle(t("menu_help"))
        self._about_action.setText(t("menu_about_app"))
        self._workflow_menu.setTitle(t("workflow_menu"))
        self._workflow_open_action.setText(t("workflow_menu_open"))
        self._workflow_default_action.setText(t("workflow_menu_default"))
        self._export_menu.setTitle(t("menu_export"))
        self._export_txt_action.setText(t("menu_export_txt"))
        self._export_epub_action.setText(t("menu_export_epub"))
        self._export_pdf_action.setText(t("menu_export_pdf"))
        # 侧边栏
        self.sidebar.retranslate()
        # 编辑区
        self.editor.retranslate()
        # Agent 面板
        self.agent_panel.retranslate()

    def _apply_theme(self, theme_name: str = None, preview_config: dict = None):
        theme_name = theme_name or self.config.get("theme", "light")
        self.config["theme"] = theme_name
        cfg = preview_config or self.config
        self.setStyleSheet(build_style(theme_name, cfg))
        self.agent_panel.set_config(cfg)
        self.agent_panel.refresh_theme()

    def _show_toast(self, text: str, duration: int = 2500, error: bool = False):
        """显示一个自动消失的提示框（用主题强调色，palette 在自定义 QSS 下会隐身）。"""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QLabel as _QLabel
        colors = get_theme_colors(
            self.config.get("theme", "light"),
            self.config if self.config.get("theme") == "custom" else None,
        )
        if error:
            bg, fg = "#c0392b", "#ffffff"
        else:
            bg, fg = colors.get("accent", "#0071e3"), "#ffffff"
        toast = _QLabel(text, self)
        toast.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; padding: 10px 20px;"
            f" border-radius: 8px; font-size: 13px; font-weight: 600; }}"
        )
        toast.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.ToolTip)
        toast.adjustSize()
        x = self.x() + (self.width() - toast.width()) // 2
        y = self.y() + self.height() - 120  # 高一点，避开状态栏
        toast.move(x, y)
        toast.show()
        toast.raise_()
        QTimer.singleShot(duration, toast.deleteLater)

    def _show_about(self):
        """显示关于对话框。"""
        QMessageBox.about(
            self,
            t("menu_about_app"),
            f"<h2 style='margin-bottom:8px;'>Novel Writer</h2>"
            f"<p>{t('about_version')}</p>"
            f"<p>{t('about_desc')}</p>"
            f"<p style='color:gray;font-size:12px;'>PySide6 · Python {'.'.join(map(str, __import__('sys').version_info[:3]))}</p>",
        )

    # ── 配置与 LLM/Agent 初始化 ──

    def _save_config(self):
        _save_app_config(self.config)

