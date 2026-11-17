"""持久化 Seedance 任务，支持跨进程续取和防重复提交。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path

from . import project_io


class SeedanceTasks:
    def __init__(self, drama_dir: Path | str):
        root = Path(drama_dir)
        self.path = root / "videos" / "seedance_tasks.json"
        self.legacy_path = root / "budget.json"

    @contextmanager
    def locked(self):
        path = self.path.with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("此项目已有 Seedance 任务正在运行") from exc
            try:
                yield self._load()
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _load(self):
        if self.path.is_file():
            try:
                records = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(records, dict) or not all(isinstance(record, dict) for record in records.values()):
                    raise ValueError
                return records
            except (OSError, ValueError) as exc:
                raise RuntimeError("Seedance 任务记录无法读取") from exc
        # 旧项目只导入云端任务状态，便于接着轮询或下载。
        try:
            legacy = json.loads(self.legacy_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        entries = legacy.get("entries", []) if isinstance(legacy, dict) else []
        records = {}
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or entry.get("state") not in ("reserved", "submitted", "completed"):
                continue
            if not entry.get("shot_id"):
                continue
            key = f"{entry.get('episode', 0)}:{entry['shot_id']}"
            records[key] = {
                "input_hash": entry.get("input_hash", ""),
                "task_id": entry.get("task_id", ""),
                "state": "submitting" if entry["state"] == "reserved" else entry["state"],
            }
        if records:
            self.save(records)
        return records

    def save(self, records):
        project_io.write_json(self.path, records)
