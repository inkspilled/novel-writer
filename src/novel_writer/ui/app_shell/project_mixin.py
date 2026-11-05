"""项目/章节/规划文档/导出"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import QInputDialog, QMessageBox, QDialog
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices

from ..editor.editor_panel import PLANNING_DOCS
from ..project.project_dialogs import OpenProjectDialog
from ..project.new_project_dialog import NewProjectDialog
from ..drama.drama_worker import DramaAIWorker, NEEDS_LLM_TASKS
from ..drama.drama_mode_dialog import DramaModeDialog
from ...models.project import Project
from ...models.chapter import Chapter
from ...core import project_io
from ...core.app_config import PROJECTS_DIR, safe_project_dirname
from ...core.meme_style import DEFAULT_MEME_LEVEL
from ...core.narrative_voice import DEFAULT_VOICE
from ...core.exporter import Exporter
from ...locales import t
from ...core.logger import get_logger

logger = get_logger(__name__)

class ProjectMixin:
    # ── 顶层大页签：小说 / 短剧 ──

    def _reset_drama_tab(self):
        """切换/新建项目时卸载短剧页，避免把旧项目内容写进新项目。"""
        self._drama_loaded_dir = None
        self.drama_workspace.set_project(None)
        self.drama_panel.set_project(None)

    def _on_mode_tab_changed(self, index: int):
        """短剧页签依附当前小说项目；切页时装载/保存，不新建项目。"""
        if index == 1:
            if not self.project.title or not self.project.project_dir:
                QMessageBox.information(
                    self, "短剧工作区",
                    "短剧依附于小说项目。\n请先新建或打开一个小说项目。",
                )
                self.mode_tabs.blockSignals(True)
                self.mode_tabs.setCurrentIndex(0)
                self.mode_tabs.blockSignals(False)
                return
            if self._drama_loaded_dir != str(self.project.project_dir):
                self.drama_workspace.set_project(self.project)
                self.drama_panel.set_project(self.project)
                self._drama_loaded_dir = str(self.project.project_dir)
        else:
            if (self._drama_loaded_dir and self.drama_workspace.store is not None
                    and not self._drama_busy()):
                self.drama_workspace.save_all(silent=True)

    def _ensure_drama_loaded(self) -> bool:
        if not self.project.project_dir:
            return False
        if self._drama_loaded_dir != str(self.project.project_dir):
            self.drama_workspace.set_project(self.project)
            self.drama_panel.set_project(self.project)
            self._drama_loaded_dir = str(self.project.project_dir)
        return True

    def _drama_busy(self) -> bool:
        return self._drama_worker is not None and self._drama_worker.isRunning()

    def _drama_start(self):
        """短剧「执行模式」：弹出模式对话框（对齐小说侧），选任务与范围后启动。"""
        if self._drama_busy():
            QMessageBox.information(self, "短剧工作流", "短剧任务正在运行，先等它结束或点「停止」。")
            return
        if not self._ensure_drama_loaded():
            QMessageBox.information(self, "短剧工作流", "请先新建或打开一个小说项目。")
            return
        self.drama_workspace.save_all(silent=True)
        store = self.drama_workspace.store
        episodes = store.load_episodes()
        episodes_with_script = [e for e in episodes if store.has_script(int(e["number"]))]
        used_chapters = set()
        for ep in episodes:
            numbers = ep.get("source_chapters") or (
                [ep["source_chapter"]] if ep.get("source_chapter") else []
            )
            used_chapters.update(int(n) for n in numbers if n)
        pending = [
            c for c in self.project.chapters
            if c.number not in used_chapters and (c.content or "").strip()
        ]
        missing_shots = [
            e for e in episodes_with_script
            if not store.load_storyboard(int(e["number"]))["shots"]
        ]
        full_hint = (
            f"已有剧本 {len(episodes_with_script)} 集 · 待用正文 {len(pending)} 章 · "
            f"缺分镜 {len(missing_shots)} 集 · 圣经"
            f"{'已有（跳过）' if store.bible_text().strip() else '待生成'}。\n"
            "全流程只补缺的：已完成的剧本/分镜/资产不动，可随时停止再续。"
        )
        dialog = DramaModeDialog(
            storyboard_all_label=(
                f"全部集（{len(episodes_with_script)} 集有剧本，逐集调用模型）"
                if episodes_with_script else "全部集（暂无剧本）"
            ),
            full_hint=full_hint,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        task, scope = dialog.get_result()
        if task in NEEDS_LLM_TASKS and self.llm is None:
            QMessageBox.warning(
                self, "未配置模型",
                "该任务需要调用大模型。\n请先到「模型与智能体」里配置 API Key / 模型。",
            )
            return
        if task == "adapt" and store.bible_text().strip():
            ret = QMessageBox.question(
                self, "重编圣经",
                "已存在改编圣经。将调用模型重新生成并覆盖，继续？",
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        self._start_drama_worker(task, scope)

    def _start_drama_worker(self, task: str, scope: str):
        self._flush_content_save()
        store = self.drama_workspace.store
        if store is None:
            return
        chapters = [
            {"number": c.number, "title": c.title, "text": c.content or ""}
            for c in self.project.chapters
        ]
        current_chapter = getattr(self.editor, "_current_chapter", None)
        current_episode = self.drama_workspace.script_page._current
        project_info = {
            "title": self.project.title,
            "genre": self.project.genre,
            "style": self.project.style,
            "theme": self.project.theme,
            "synopsis": self.project.synopsis,
        }
        worker = DramaAIWorker(
            llm=self.llm,
            store=store,
            project_info=project_info,
            task=task,
            scope=scope,
            chapters=chapters,
            current_chapter=current_chapter.number if current_chapter else 0,
            current_episode=current_episode,
        )
        worker.progress.connect(self._on_drama_progress)
        worker.log.connect(self.drama_panel.append_log)
        worker.usage_updated.connect(self.drama_panel.set_usage)
        worker.agent_state.connect(self._on_drama_agent_state)
        worker.finished_ok.connect(self._on_drama_done)
        worker.failed.connect(self._on_drama_failed)
        self._drama_worker = worker
        self.drama_workspace.setEnabled(False)
        self.drama_panel.set_running(True)
        scope_labels = {
            ("full", "current"): "下一集", ("full", "all"): "全部待用",
            ("segment", "current"): "下一集", ("segment", "all"): "全部待用",
            ("storyboard", "current"): "当前集", ("storyboard", "all"): "全部集",
        }
        scope_text = scope_labels.get((task, scope), "—")
        model = getattr(self.llm, "model", "") if self.llm else ""
        if task in NEEDS_LLM_TASKS:
            self.drama_panel.set_status(f"准备调用模型（{scope_text}）…", 2)
            self.drama_panel.append_log(
                f"开始 {task}：范围={scope_text} · 模型={model} · 会逐章/逐集调用，真实耗时。"
            )
        else:
            self.drama_panel.set_status("正在本地提取…", 2)
            self.drama_panel.append_log(f"开始 {task}：范围={scope_text} · 本地规则提取，不调模型。")
        worker.start()

    def _on_drama_progress(self, message: str, percent: int):
        self.drama_panel.set_status(message, percent)

    def _on_drama_agent_state(self, name: str, state: str, task: str):
        from ..agent.office_scene import AgentState
        mapping = {
            "working": AgentState.WORKING,
            "done": AgentState.DONE,
            "error": AgentState.ERROR,
            "idle": AgentState.IDLE,
        }
        self.drama_panel.set_agent_state(name, mapping.get(state, AgentState.IDLE), task)

    def _on_drama_done(self, summary: str):
        worker = self._drama_worker
        task = worker.task if worker else ""
        self.drama_workspace.setEnabled(True)
        self.drama_panel.set_running(False)
        self.drama_panel.set_status(summary, 100)
        self.drama_panel.append_log(summary)
        store = self.drama_workspace.store
        if store is not None:
            self.drama_workspace.series_page.load(store, self.project)
            self.drama_workspace.script_page.load(
                store, keep=self.drama_workspace.script_page._current
            )
            self.drama_workspace.storyboard_page.load(
                store, keep=self.drama_workspace.script_page._current
            )
            self.drama_workspace.assets_page.load(store)
            page = {"adapt": 0, "segment": 1, "storyboard": 2, "assets": 3, "full": 2}.get(task, 0)
            self.drama_workspace.nav.setCurrentRow(page)
        self._finish_drama_worker()

    def _on_drama_failed(self, error: str):
        self.drama_workspace.setEnabled(True)
        self.drama_panel.set_running(False)
        self.drama_panel.set_status("生成失败", 0)
        self.drama_panel.append_log(f"生成失败：{error}")
        QMessageBox.warning(self, "短剧生成失败", error)
        self._finish_drama_worker()

    def _finish_drama_worker(self):
        worker = self._drama_worker
        self._drama_worker = None
        if worker is not None:
            self._old_drama_workers.append(worker)

    def _drama_stop(self, silent: bool = False):
        worker = self._drama_worker
        if worker is not None and worker.isRunning():
            worker.request_stop()
            self.drama_panel.set_status("正在停止…", 0)
            if not silent:
                self.drama_panel.append_log("已请求停止，当前这一次模型调用结束后会停。")

    def _new_project(self):
        if self._drama_busy():
            QMessageBox.information(self, "短剧生成中", "先停止当前短剧生成，再新建项目。")
            return
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
        self._flush_content_save()
        self.editor.detach_chapter()
        self.project = Project(
            title=title,
            genre=data["genre"],
            style=data["style"],
            narrative_voice=data.get("narrative_voice", DEFAULT_VOICE),
            meme_level=data.get("meme_level", DEFAULT_MEME_LEVEL),
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
        self._reset_drama_tab()
        self.mode_tabs.setCurrentIndex(0)
        self.statusBar().showMessage(t("status_created", title))
        logger.info("新项目已创建: %s (%s)", title, project_dir)

    def _open_project(self):
        """打开已有项目。"""
        if self._drama_busy():
            QMessageBox.information(self, "短剧生成中", "先停止当前短剧生成，再打开其他项目。")
            return
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
        """从项目目录加载小说项目；短剧数据依附在同一项目的 drama/ 下。"""
        self._flush_content_save()
        try:
            # 先卸下旧项目的当前章节，避免 clear() 触发 content_changed 把旧章写空
            self.editor.detach_chapter()
            self.project = Project.load(path)
            self.agent_panel.set_project_db(path)
            self.sidebar.set_project_title(self.project.title)

            self._sync_world_state_from_canon(path)
            self._load_workflow_for_project()
            self.sidebar.load_chapters(self.project.chapters)
            self.sidebar.update_stats(
                self.project.total_words(),
                self.project.target_words,
                len(self.project.chapters),
            )
            self.editor.clear()
            self.editor.set_project_dir(self.project.project_dir)
            self._select_initial_chapter()
            self._reset_drama_tab()
            self.mode_tabs.setCurrentIndex(0)
            self.statusBar().showMessage(t("status_opened", self.project.title))
            logger.info("项目已加载: %s (%s)", self.project.title, path)
        except Exception as e:
            logger.error("加载项目失败: %s - %s", path, e, exc_info=True)
            QMessageBox.critical(self, t("dialog_error"), t("msg_open_fail", e))

    def _sync_world_state_from_canon(self, path: Path):
        """有 world_state 事件时，用正典重放快照（历史才是源）。"""
        try:
            from ...core.canon.projection import project_world_state, world_state_event_count
            if world_state_event_count(path):
                project_world_state(path)
                logger.info("世界状态已从正典重放: %s", path)
        except Exception as e:
            logger.warning("世界状态重放失败（保留现有快照）: %s", e)

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

    def _seal_current_chapter(self):
        """手动把当前章（含最新修订版）封印到正典。"""
        chapter = getattr(self.editor, "_current_chapter", None)
        if chapter is None or not self.project.project_dir:
            QMessageBox.information(self, t("workflow_seal_current"), t("msg_no_project"))
            return
        self._flush_content_save()
        from ...core.canon.seal import seal_chapter
        try:
            event = seal_chapter(self.project.project_dir, chapter.number)
        except Exception as e:
            QMessageBox.warning(self, t("workflow_seal_current"), str(e))
            return
        if event:
            self.statusBar().showMessage(f"第{chapter.number}章已封印", 4000)
            QMessageBox.information(self, t("workflow_seal_current"),
                                    f"第{chapter.number}章已封印（含最新修订版）")
        else:
            QMessageBox.information(self, t("workflow_seal_current"),
                                    f"第{chapter.number}章内容未变，无需重复封印")

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
        self._flush_content_save()
        if 0 <= index < len(self.project.chapters):
            self.editor.load_chapter(self.project.chapters[index])

    def _open_chapter_number(self, number: int):
        """跳到指定章号（故事线双击/「打开当前章」）。"""
        for i, chapter in enumerate(self.project.chapters):
            if getattr(chapter, "number", 0) != number:
                continue
            self.editor.tab_bar.setCurrentIndex(0)  # 切回正文页
            self.editor.load_chapter(chapter)
            tree = self.sidebar.chapter_tree
            for row in range(tree.topLevelItemCount()):
                if tree.topLevelItem(row).data(0, Qt.ItemDataRole.UserRole) == i:
                    tree.setCurrentItem(tree.topLevelItem(row))
                    break
            return
        self.statusBar().showMessage(f"第{number}章还没有正文", 4000)

    def _flush_content_save(self):
        """把防抖中的正文写入磁盘；切换章节/项目/关闭前必须调用。"""
        chapter = getattr(self, "_pending_chapter", None)
        if chapter is None:
            return
        self._pending_chapter = None
        try:
            chapter.content = chapter._content_cache or ""
            # 仍停留在这一章时才把状态改回「已保存」，避免切章后误标
            if getattr(self.editor, "_current_chapter", None) is chapter:
                self.editor.mark_saved()
        except Exception as e:
            logger.error("正文落盘失败: %s", e)

    def _on_content_changed(self):
        chapter = getattr(self.editor, "_current_chapter", None)
        if chapter is not None:
            text = self.editor.get_content()
            # 先更新内存缓存（字数统计/断点读取都走缓存），350ms 后统一写盘
            chapter._content_cache = text
            self._pending_chapter = chapter
            timer = getattr(self, "_content_save_timer", None)
            if timer is not None:
                timer.start()
            else:
                chapter.content = text
            self.sidebar.update_stats(
                self.project.total_words(),
                self.project.target_words,
                len(self.project.chapters),
            )

    def _save_project(self):
        self._flush_content_save()
        if not self.project.title:
            return
        # 如果还没有项目目录（兼容旧流程），创建一个
        if not self.project.project_dir:
            safe_name = safe_project_dirname(self.project.title)
            project_dir = PROJECTS_DIR / safe_name
            project_dir.mkdir(parents=True, exist_ok=True)
            self.project.set_project_dir(project_dir)
        # 短剧是当前小说的附加数据：只有装载过短剧页签才写 drama/
        if (self._drama_loaded_dir == str(self.project.project_dir)
                and self.drama_workspace.store and not self._drama_busy()):
            try:
                self.drama_workspace.save_all(silent=True)
            except Exception as e:
                logger.error("短剧数据保存失败（不影响小说保存）: %s", e)
        # 规划文档（含章节细纲）未落盘的编辑一并保存
        saved_docs = self.editor.flush_planning_autosave()
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
        if getattr(self, "mode_tabs", None) and self.mode_tabs.currentIndex() == 1:
            QMessageBox.information(
                self, "导出",
                "当前在「短剧」大页签。分镜请用分镜页的「导出 CSV / JSON」；\n"
                "小说 TXT/EPUB/PDF 请切回「小说」页签。",
            )
            return
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

