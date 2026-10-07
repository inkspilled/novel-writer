"""聊天记录持久化 — 项目级 SQLite 存储。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)


class ChatHistory:
    """项目级聊天记录（SQLite，chat.db）。

    messages 表：agent（智能体配置名）、role（user/agent）、content、created_at。
    save() 全量覆盖当前项目记录，load() 按 id 顺序读出。
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
        return conn

    def save(self, chat_history: dict[str, list[dict]]) -> None:
        """将所有聊天记录写入 SQLite（全量覆盖当前项目）。

        chat_history: {agent_name: [{"type": "user"|"agent", "text": str}, ...]}
        """
        db = self._connect()
        try:
            db.execute("DELETE FROM messages")
            rows = []
            for agent_name, messages in chat_history.items():
                for m in messages:
                    rows.append((agent_name, m["type"], m["text"]))
            db.executemany("INSERT INTO messages (agent, role, content) VALUES (?, ?, ?)", rows)
            db.commit()
        finally:
            db.close()

    def load(self) -> dict[str, list[dict]]:
        """从 SQLite 加载当前项目的聊天记录，按消息 id 顺序返回。"""
        if not self.db_path.exists():
            return {}
        db = self._connect()
        try:
            history: dict[str, list[dict]] = {}
            for agent, role, content in db.execute(
                    "SELECT agent, role, content FROM messages ORDER BY id"):
                if agent not in history:
                    history[agent] = []
                history[agent].append({"type": role, "text": content, "agent_name": agent})
            return history
        finally:
            db.close()
