"""短剧资产图生成 Worker：按资产逐张调 ComfyUI，落盘并回填 assets/index.json。"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ...core.comfyui import ComfyUIClient, ComfyError
from ...core.drama import DramaStore
from ...core.logger import get_logger

logger = get_logger(__name__)


class ComfyImageWorker(QThread):
    progress = Signal(str, int)
    log = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, store: DramaStore, assets: list[dict], config: dict, parent=None):
        super().__init__(parent)
        self.store = store
        self.assets = list(assets)
        self.config = dict(config)
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        client = ComfyUIClient(
            base_url=self.config.get("base_url", ""),
            workflow_path=self.config.get("workflow", ""),
            timeout=float(self.config.get("timeout") or 300),
        )
        done = 0
        failed = 0
        try:
            total = max(1, len(self.assets))
            for i, asset in enumerate(self.assets, start=1):
                if self._stop:
                    break
                name = asset.get("name") or asset.get("id") or "资产"
                self.progress.emit(f"生成 {name}（{i}/{total}）", int((i - 1) / total * 100))
                positive, negative = self.store.asset_prompt(asset)
                try:
                    data = client.generate(positive, negative, prefix=str(asset.get("id") or "asset"))
                except ComfyError as e:
                    failed += 1
                    self.log.emit(f"{name} 失败：{e}")
                    logger.warning("素材生成失败 %s: %s", name, e)
                    continue
                rel = self.store.save_generated_image(str(asset.get("id") or ""), data)
                done += 1
                self.log.emit(f"{name} → {rel}")
                self.progress.emit(f"已完成 {i}/{total}", int(i / total * 100))
        finally:
            client.close()

        if self._stop:
            self.finished_ok.emit(f"已停止：生成 {done} 张" + (f"，失败 {failed}" if failed else ""))
        elif done == 0:
            self.failed.emit("一张都没生成成功，检查 ComfyUI 是否启动、工作流模板是否正确")
        else:
            self.finished_ok.emit(
                f"生成 {done} 张素材" + (f"，失败 {failed}" if failed else "")
            )
