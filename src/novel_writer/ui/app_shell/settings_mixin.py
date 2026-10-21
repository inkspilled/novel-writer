"""设置类对话框入口"""
from __future__ import annotations

from pathlib import Path

import shutil

from PySide6.QtWidgets import QDialog, QMessageBox

from ..settings.model_dialog import ModelDialog
from ..settings.appearance_dialog import AppearanceDialog
from ...locales import t
from ...core.logger import get_logger

logger = get_logger(__name__)

class SettingsMixin:
    def _open_model_settings(self):
        """打开模型设置对话框。"""
        dialog = ModelDialog(self.config, self)
        if dialog.exec() == ModelDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            self._save_config()
            self._init_llm()
            self._init_agents()
            self.statusBar().showMessage(t("status_settings_saved"))

    def _open_project_settings(self):
        """打开项目设置对话框。"""
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), "请先打开或新建项目")
            return
        from ..project.new_project_dialog import NewProjectDialog
        project_data = {
            "title": self.project.title,
            "genre": self.project.genre,
            "style": self.project.style,
            "narrative_voice": self.project.narrative_voice,
            "meme_level": self.project.meme_level,
            "target_words": self.project.target_words,
            "target_chapters": self.project.target_chapters,
            "theme": self.project.theme,
            "direction": self.project.direction,
            "synopsis": self.project.synopsis,
            "cover": self.project.cover,
            "_project_dir": str(self.project.project_dir) if self.project.project_dir else "",
        }
        dialog = NewProjectDialog(parent=self, project_data=project_data, agents=self.agents)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            # 更新项目信息
            self.project.title = data["title"]
            self.project.genre = data["genre"]
            self.project.style = data["style"]
            self.project.narrative_voice = data.get(
                "narrative_voice", self.project.narrative_voice
            )
            self.project.meme_level = data.get("meme_level", self.project.meme_level)
            self.project.target_words = data["target_words"]
            try:
                chapters = int(data.get("target_chapters") or 0)
            except (TypeError, ValueError):
                chapters = 0
            if chapters > 0:
                self.project.target_chapters = chapters
            self.project.theme = data["theme"]
            self.project.direction = data["direction"]
            self.project.synopsis = data["synopsis"]
            # 更新封面
            if data["cover"] and self.project.project_dir:
                src = Path(data["cover"])
                ext = src.suffix or ".png"
                dst = self.project.project_dir / f"cover{ext}"
                if src.resolve() != dst.resolve():
                    shutil.copy2(src, dst)
                self.project.cover = f"cover{ext}"
            self._save_project()
            self.sidebar.set_project_title(self.project.title)
            self.statusBar().showMessage("项目设置已保存")

    def _open_appearance(self):
        """打开外观设置对话框。"""
        dialog = AppearanceDialog(self.config, self)
        dialog.theme_preview.connect(self._apply_theme)
        if dialog.exec() == AppearanceDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            self._save_config()
            self._apply_theme()
            self._refresh_ui_texts()
            self.statusBar().showMessage(t("status_settings_saved"))

