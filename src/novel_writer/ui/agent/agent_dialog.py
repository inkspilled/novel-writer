"""Agent 管理对话框 — 增删改查 Agent 配置。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QLineEdit, QComboBox, QPushButton, QFormLayout,
    QGroupBox, QMessageBox, QDoubleSpinBox, QListWidget,
    QInputDialog, QTextEdit,
)

from ...locales import t
from ...core.agents import load_agents, save_agents, reset_agents, is_builtin_agent


class AgentDialog(QDialog):
    """Agent 管理：增删改查 Agent 配置。"""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings_tab_agent"))
        self.setMinimumSize(640, 560)
        self.config = dict(config)
        self._agents = load_agents()
        # 旧配置里的每 Agent 模型字段直接丢弃：所有 Agent 统一用主模型
        for info in self._agents.values():
            info.pop("model", None)
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        body = QHBoxLayout()
        body.setSpacing(14)

        # 左侧列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self._agent_list_label = QLabel(t("settings_agent_list"))
        left_layout.addWidget(self._agent_list_label)
        self.agent_list = QListWidget()
        self.agent_list.currentItemChanged.connect(self._on_agent_selected)
        left_layout.addWidget(self.agent_list)
        btn_row = QHBoxLayout()
        self._btn_add = QPushButton(t("settings_btn_add"))
        self._btn_add.clicked.connect(self._add_agent)
        self._btn_del = QPushButton(t("settings_btn_delete"))
        self._btn_del.setObjectName("danger")
        self._btn_del.clicked.connect(self._del_agent)
        self._btn_reset = QPushButton(t("settings_btn_reset"))
        self._btn_reset.clicked.connect(self._reset_agents)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_del)
        btn_row.addWidget(self._btn_reset)
        left_layout.addLayout(btn_row)
        # 默认智能体锁定策略：新增/删除入口先隐藏（逻辑保留，后续开放自定义时恢复）
        self._btn_add.setVisible(False)
        self._btn_del.setVisible(False)
        body.addWidget(left, 1)

        # 右侧详情
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.agent_detail_group = QGroupBox(t("settings_agent_detail"))
        form = QFormLayout(self.agent_detail_group)
        form.setSpacing(10)

        self.agent_name_input = QLineEdit()
        self.agent_name_input.setPlaceholderText(t("settings_ph_agent_id"))
        form.addRow(t("settings_agent_id"), self.agent_name_input)

        self.agent_title_input = QLineEdit()
        self.agent_title_input.setPlaceholderText(t("settings_ph_agent_title"))
        form.addRow(t("settings_agent_title"), self.agent_title_input)

        self.agent_emoji_combo = QComboBox()
        self.agent_emoji_combo.setEditable(True)
        self._emoji_list = [
            "📋", "🗺️", "✍️", "🔍", "✅", "✨", "💡", "🎯",
            "📖", "🖊️", "🧠", "💬", "🎨", "📐", "🔮", "🎭",
            "📝", "🤖", "📚", "🌟", "⚡", "🔥", "💎", "🎪",
        ]
        self.agent_emoji_combo.addItems(self._emoji_list)
        self.agent_emoji_combo.setPlaceholderText(t("settings_ph_emoji"))
        form.addRow(t("settings_agent_emoji"), self.agent_emoji_combo)

        self.agent_temp_spin = QDoubleSpinBox()
        self.agent_temp_spin.setRange(0.0, 2.0)
        self.agent_temp_spin.setSingleStep(0.1)
        self.agent_temp_spin.setValue(0.7)
        form.addRow(t("settings_temperature"), self.agent_temp_spin)

        self.agent_skills_input = QLineEdit()
        self.agent_skills_input.setPlaceholderText(t("settings_ph_skills"))
        form.addRow(t("settings_agent_skills"), self.agent_skills_input)

        self.agent_prompt_input = QTextEdit()
        self.agent_prompt_input.setPlaceholderText(t("settings_ph_prompt"))
        self.agent_prompt_input.setMinimumHeight(100)
        self.agent_prompt_input.setMaximumHeight(160)
        form.addRow(t("settings_agent_prompt"), self.agent_prompt_input)

        right_layout.addWidget(self.agent_detail_group)
        right_layout.addStretch()
        body.addWidget(right, 2)

        layout.addLayout(body)

        btn_row2 = QHBoxLayout()
        btn_row2.addStretch()
        self._btn_save = QPushButton(t("settings_save_all"))
        self._btn_save.setObjectName("primary")
        self._btn_save.clicked.connect(self._save)
        self._btn_cancel = QPushButton(t("settings_cancel"))
        self._btn_cancel.clicked.connect(self.reject)
        btn_row2.addWidget(self._btn_save)
        btn_row2.addWidget(self._btn_cancel)
        layout.addLayout(btn_row2)

    def _detail_inputs(self) -> list:
        """右侧详情的全部输入控件（用于整体切只读）。"""
        return [
            self.agent_name_input, self.agent_title_input, self.agent_emoji_combo,
            self.agent_temp_spin, self.agent_skills_input, self.agent_prompt_input,
        ]

    def _refresh_agent_list(self):
        self.agent_list.clear()
        for name, info in self._agents.items():
            emoji = info.get("emoji", "🤖")
            title = info.get("title", name)
            lock = " 🔒" if is_builtin_agent(name) else ""
            self.agent_list.addItem(f"{emoji} {title}{lock}")

    def _on_agent_selected(self, current, _prev):
        if not current:
            return
        row = self.agent_list.currentRow()
        names = list(self._agents.keys())
        if row >= len(names):
            return
        name = names[row]
        info = self._agents[name]
        builtin = is_builtin_agent(name)
        # 内置智能体只读展示，不允许改
        for w in self._detail_inputs():
            w.setEnabled(not builtin)
        self.agent_name_input.setText(name)
        self.agent_title_input.setText(info.get("title", ""))
        emoji = info.get("emoji", "🤖")
        idx = self.agent_emoji_combo.findText(emoji)
        if idx >= 0:
            self.agent_emoji_combo.setCurrentIndex(idx)
        else:
            self.agent_emoji_combo.setEditText(emoji)
        self.agent_temp_spin.setValue(info.get("temperature", 0.7))
        self.agent_skills_input.setText(", ".join(info.get("skills", [])))
        self.agent_prompt_input.setPlainText(info.get("system_prompt", ""))

    def _save_current_agent(self):
        name = self.agent_name_input.text().strip()
        if not name:
            return
        # 内置智能体不允许改写（双保险：输入已只读，这里再拦一次）
        if is_builtin_agent(name):
            return
        if name not in self._agents:
            self._agents[name] = {}
        self._agents[name]["title"] = self.agent_title_input.text().strip()
        self._agents[name]["emoji"] = self.agent_emoji_combo.currentText().strip() or "🤖"
        self._agents[name]["temperature"] = self.agent_temp_spin.value()
        self._agents[name]["skills"] = [s.strip() for s in self.agent_skills_input.text().split(",") if s.strip()]
        self._agents[name]["system_prompt"] = self.agent_prompt_input.toPlainText().strip()

    def _add_agent(self):
        name, ok = QInputDialog.getText(self, t("settings_btn_add"), t("settings_ph_agent_id"))
        if ok and name:
            name = name.strip().lower().replace(" ", "_")
            if is_builtin_agent(name):
                QMessageBox.warning(self, t("dialog_prompt"), t("msg_agent_builtin_locked", name))
                return
            if name in self._agents:
                QMessageBox.warning(self, t("dialog_prompt"), t("msg_agent_exists", name))
                return
            self._agents[name] = {"title": name, "emoji": "🤖", "skills": [], "temperature": 0.7, "system_prompt": ""}
            self._refresh_agent_list()
            self.agent_list.setCurrentRow(self.agent_list.count() - 1)

    def _del_agent(self):
        row = self.agent_list.currentRow()
        if row < 0:
            return
        names = list(self._agents.keys())
        if row >= len(names):
            return
        name = names[row]
        if is_builtin_agent(name):
            # 内置智能体不允许删除（入口已隐藏，这里防御性再拦）
            QMessageBox.warning(self, t("dialog_prompt"), t("msg_agent_builtin_locked", name))
            return
        if QMessageBox.question(self, t("dialog_confirm"),
                                t("msg_delete_agent", name)) == QMessageBox.StandardButton.Yes:
            del self._agents[name]
            save_agents(self._agents)
            self._refresh_agent_list()

    def _reset_agents(self):
        if QMessageBox.question(self, t("dialog_confirm"),
                                t("msg_reset_agents")) == QMessageBox.StandardButton.Yes:
            self._agents = reset_agents()
            save_agents(self._agents)
            self._refresh_agent_list()
            if self.agent_list.count() > 0:
                self.agent_list.setCurrentRow(0)

    def _load_config(self):
        self._refresh_agent_list()

    def _save(self):
        self._save_current_agent()
        # 彻底移除旧字段：避免以后又被 load/save 带回来
        for info in self._agents.values():
            info.pop("model", None)
        save_agents(self._agents)
        self.accept()

    def get_config(self) -> dict:
        return self.config
