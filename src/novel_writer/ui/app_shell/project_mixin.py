"""项目/章节/规划文档/导出"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import QInputDialog, QMessageBox, QDialog
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from ..editor.editor_panel import PLANNING_DOCS
from ..project.project_dialogs import OpenProjectDialog
from ..project.new_project_dialog import NewProjectDialog
from ...models.project import Project
from ...models.chapter import Chapter
from ...core import project_io
from ...core.app_config import PROJECTS_DIR, safe_project_dirname
from ...core.exporter import Exporter
from ...locales import t
from ...core.logger import get_logger

logger = get_logger(__name__)

class ProjectMixin:
    def _new_project(self):
        # 先保存当前项目
        if self.project.title:
            self._save_project()

        from ..project.new_project_dialog import NewProjectDialog
        dialog = NewProjectDialog(parent=self, agents=self.agents)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        data = dialog.get_data()
        title = data["title"]

        # 创建项目目录（安全目录名 + 重名自动加后缀，避免覆盖已有项目）
        safe_name = safe_project_dirname(title)
        project_dir = PROJECTS_DIR / safe_name
        suffix = 2
        while project_dir.exists() and (project_dir / "meta.json").exists():
            project_dir = PROJECTS_DIR / f"{safe_name}_{suffix}"
            suffix += 1
        project_dir.mkdir(parents=True, exist_ok=True)

        # 复制封面到项目目录
        cover_rel = ""
        if data["cover"]:
            src = Path(data["cover"])
            ext = src.suffix or ".png"
            dst = project_dir / f"cover{ext}"
            shutil.copy2(src, dst)
            cover_rel = f"cover{ext}"

        # 先卸下旧项目的当前章节，避免 clear() 触发 content_changed 把旧章写空
        self.editor.detach_chapter()
        self.project = Project(
            title=title,
            genre=data["genre"],
            style=data["style"],
            theme=data["theme"],
            direction=data["direction"],
            target_words=data["target_words"],
            target_chapters=data.get("target_chapters", 100),
            synopsis=data["synopsis"],
            cover=cover_rel,
        )
        self.project.set_project_dir(project_dir)
        project_io.init_project_dir(project_dir)

        # 保存立意文档
        ideation_content = f"# {title}\n\n"
        ideation_content += f"## 核心立意\n\n{data['theme']}\n\n"
        ideation_content += f"## 规划方向\n\n{data['direction']}\n\n"
        if data["synopsis"]:
            ideation_content += f"## 简介\n\n{data['synopsis']}\n\n"
        ideation_content += f"## 基本信息\n\n"
        ideation_content += f"- 题材：{data['genre']}\n"
        ideation_content += f"- 风格：{data['style']}\n"
        ideation_content += f"- 目标字数：{data['target_words']:,}\n"
        project_io.write_md(project_dir / "planning" / "立意.md", ideation_content)

        self.project.save()
        self.agent_panel.set_project_db(project_dir)
        self._load_workflow_for_project()
        self.sidebar.set_project_title(title)
        self.sidebar.load_chapters(self.project.chapters)
        self.sidebar.update_stats(0, self.project.target_words, len(self.project.chapters))
        self.editor.clear()
        self.editor.set_project_dir(project_dir)
        self.statusBar().showMessage(t("status_created", title))
        logger.info("新项目已创建: %s (%s)", title, project_dir)

    def _open_project(self):
        """打开已有项目。"""
        # 先保存当前项目
        if self.project.title:
            self._save_project()
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        projects_info = project_io.list_projects(PROJECTS_DIR)
        if not projects_info:
            QMessageBox.information(self, t("dialog_prompt"), t("msg_no_projects_saved"))
            return

        dialog = OpenProjectDialog(projects_info, self)
        dialog.exec()
        if dialog.selected_dir:
            self._load_project_from_dir(dialog.selected_dir)

    def _load_project_from_dir(self, path: Path):
        """从项目目录加载项目。"""
        try:
            # 先卸下旧项目的当前章节，避免 clear() 触发 content_changed 把旧章写空
            self.editor.detach_chapter()
            self.project = Project.load(path)
            self.agent_panel.set_project_db(path)
            self._load_workflow_for_project()
            self.sidebar.set_project_title(self.project.title)
            self.sidebar.load_chapters(self.project.chapters)
            self.sidebar.update_stats(
                self.project.total_words(),
                self.project.target_words,
                len(self.project.chapters),
            )
            self.editor.clear()
            self.editor.set_project_dir(self.project.project_dir)
            self._select_initial_chapter()
            self.statusBar().showMessage(t("status_opened", self.project.title))
            logger.info("项目已加载: %s (%s)", self.project.title, path)
        except Exception as e:
            logger.error("加载项目失败: %s - %s", path, e, exc_info=True)
            QMessageBox.critical(self, t("dialog_error"), t("msg_open_fail", e))

    def _select_initial_chapter(self):
        """打开项目后默认载入最后一章，避免“有章节却提示选择章节”的空窗。"""
        if not self.project.chapters:
            return
        tree = self.sidebar.chapter_tree
        item = tree.topLevelItem(len(self.project.chapters) - 1)
        if item is not None:
            tree.setCurrentItem(item)

    def _add_chapter(self):
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), t("msg_no_project"))
            return
        title, ok = QInputDialog.getText(self, t("dialog_add_chapter"), t("dialog_chapter_title"))
        if ok:
            self.project.add_chapter(title)
            self.project.save()
            self.sidebar.load_chapters(self.project.chapters)
            self.sidebar.update_stats(
                self.project.total_words(),
                self.project.target_words,
                len(self.project.chapters),
            )

    def _show_export_success(self, path: Path):
        """导出完成：给路径 + 一个“打开所在目录”的动作。"""
        box = QMessageBox(self)
        box.setWindowTitle(t("export_success"))
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"{t('export_success')}\n{path}")
        open_btn = box.addButton(t("export_open_dir"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_btn:
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
            except Exception:
                pass

    def _on_chapter_selected(self, index: int):
        if 0 <= index < len(self.project.chapters):
            self.editor.load_chapter(self.project.chapters[index])

    def _on_content_changed(self):
        chapter = getattr(self.editor, "_current_chapter", None)
        if chapter is not None:
            chapter.content = self.editor.get_content()
            self.sidebar.update_stats(
                self.project.total_words(),
                self.project.target_words,
                len(self.project.chapters),
            )

    def _save_project(self):
        if not self.project.title:
            return
        # 规划文档（含章节细纲）未落盘的编辑一并保存
        saved_docs = self.editor.flush_planning_autosave()
        # 如果还没有项目目录（兼容旧流程），创建一个
        if not self.project.project_dir:
            safe_name = safe_project_dirname(self.project.title)
            project_dir = PROJECTS_DIR / safe_name
            project_dir.mkdir(parents=True, exist_ok=True)
            self.project.set_project_dir(project_dir)
        try:
            self.project.save()
            n_ch = len(self.project.chapters)
            # 正文本来就是实时落盘的，这里说清楚「保存项目」到底写了什么
            extra = f" · 规划文档 {len(saved_docs)} 份" if saved_docs else ""
            self._show_toast(f"已保存书目信息 · {n_ch} 章正文已是实时保存{extra}")
            self.statusBar().showMessage(f"书目信息已保存（{n_ch} 章）", 3000)
        except Exception as e:
            self._show_toast(f"保存失败: {e}", error=True)
            logger.error("项目保存失败: %s", e)

    def _on_planning_save(self, doc_name: str):
        """保存规划文档（手动，带提示）。"""
        if not self.project.project_dir:
            return
        from ..editor.editor_panel import PLANNING_DOCS
        for label, rel_path in PLANNING_DOCS:
            if label == doc_name:
                content = self.editor.get_planning_content(doc_name)
                fpath = self.project.project_dir / rel_path
                try:
                    project_io.write_md(fpath, content)
                    # 清掉脏标记，避免自动保存再写一遍
                    self.editor._planning_dirty.discard(label)
                    self._show_toast(f"已保存: {doc_name}")
                    logger.info("规划文档已保存: %s", fpath)
                except Exception as e:
                    self._show_toast(f"保存失败: {e}", error=True)
                    logger.error("规划文档保存失败: %s - %s", fpath, e)
                return

    def _refresh_chapters(self):
        """从磁盘重新加载章节列表到侧边栏。"""
        if not self.project.project_dir:
            return
        had_current = getattr(self.editor, "_current_chapter", None) is not None
        # 重新扫描章节文件
        scanned = project_io.scan_chapters(self.project.project_dir)
        # 更新 Project 对象的 chapters 列表
        existing = {ch.number: ch for ch in self.project.chapters}
        for item in scanned:
            num = item["number"]
            if num in existing:
                # 已有章节：同步路径和标题（fix_titles 改名后路径会变）
                ch = existing[num]
                ch.title = item["title"]
                ch._content_path = str(item["content_path"].resolve())
                ch._outline_path = str(item["outline_path"].resolve()) if item["outline_path"] else ""
                # 工作流可能刚改写了磁盘正文：让下一次读取回源，避免显示旧缓存
                ch._content_cache = None
            else:
                from ...models.chapter import Chapter
                ch = Chapter(
                    number=num,
                    title=item["title"],
                    _content_path=str(item["content_path"].resolve()),
                    _outline_path=str(item["outline_path"].resolve()) if item["outline_path"] else "",
                )
                self.project.chapters.append(ch)
        self.project.chapters.sort(key=lambda c: c.number)
        # 刷新侧边栏
        self.sidebar.load_chapters(self.project.chapters)
        self.sidebar.update_stats(
            self.project.total_words(),
            self.project.target_words,
            len(self.project.chapters),
        )
        if not had_current:
            self._select_initial_chapter()

    def _export(self, fmt: str):
        """导出当前项目（txt/epub/pdf）。"""
        if not self.project.project_dir:
            QMessageBox.warning(self, t("menu_export"), t("export_no_project"))
            return
        try:
            from ...core.exporter import Exporter
            exporter = Exporter(self.project.project_dir)
            exporters = {
                "txt": exporter.export_txt,
                "epub": exporter.export_epub,
                "pdf": exporter.export_pdf,
            }
            output_path = exporters[fmt]()
            self._show_export_success(Path(output_path))
        except ImportError as e:
            deps = {"pdf": "reportlab"}
            if fmt in deps:
                QMessageBox.warning(self, t("export_missing_dep"), f"pip install {deps[fmt]}")
            else:
                QMessageBox.critical(self, t("export_failed"), str(e))
                logger.error("%s 导出失败: %s", fmt.upper(), e)
        except Exception as e:
            QMessageBox.critical(self, t("export_failed"), str(e))
            logger.error("%s 导出失败: %s", fmt.upper(), e)

