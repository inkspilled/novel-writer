"""短剧资产图生成 Worker：按资产逐张调图片服务，落盘并回填 assets/index.json。

分层出图：人物 = 四角度头肩定妆 + 全身正/侧/背三视图；场景 = 360° 环景 + 正反透视；
道具 = 正/侧/背三视图。单资产失败记进失败清单（drama/failures.json）继续下一项，
UI 可见并可只重试失败项。
"""
from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...core.comfyui import ComfyUIClient, ComfyError
from ...core.drama import (
    ASSET_PROMPT_PLANS, FULLBODY_VIEWS, MAKEUP_VIEWS, PROP_VIEWS,
    DramaStore, asset_canvas, asset_seed,
)
from ...core.drama_records import clear_failures, failures_summary, record_failure, record_generation
from ...core.drama_scene import SCENE_VIEWS
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

    def _anchor(self, asset):
        """固定参考图（首次图锚点）；comfyui 无参考工作流时返回 None。"""
        ref = asset.get("reference_image") or asset.get("image") or asset.get("ref")
        if not ref:
            return None
        path = Path(ref)
        if not path.is_absolute():
            path = self.store.assets_dir / path
        if not path.is_file():
            return None
        if self.config.get("provider") == "comfyui" and not self.config.get("reference_workflow"):
            return None
        return path

    def _no_reference_guard(self, label: str):
        if self.config.get("provider") == "comfyui" and not self.config.get("reference_workflow"):
            raise ComfyError(f"{label}需要配置 ComfyUI 参考图工作流")

    def run(self):
        from ...core.image_generation import (
            create_client, generate_fullbody_view, generate_makeup_view,
            generate_prop_view, generate_scene_panorama,
        )
        client = None
        done = 0
        failed = 0
        try:
            client = create_client(self.config)
            total = max(1, len(self.assets))
            for i, asset in enumerate(self.assets, start=1):
                if self._stop:
                    break
                name = asset.get("name") or asset.get("id") or "资产"
                asset_id = str(asset.get("id") or "")
                self.progress.emit(f"生成 {name}（{i}/{total}）", int((i - 1) / total * 100))
                # 提交前体检：能在文字里发现的错，不留到图上
                for issue in self.store.audit_asset_prompt(asset):
                    self.log.emit(f"{name} ⚠ {issue}")
                positive, negative = self.store.asset_prompt(asset)
                kind = str(asset.get("kind") or "character")
                plan = ASSET_PROMPT_PLANS.get(kind, ASSET_PROMPT_PLANS["character"])
                width, height = asset_canvas(kind)
                try:
                    # 公用素材库本地匹配（此路径无 LLM，只按名称匹配）：命中直接复用不出图
                    from ...core import drama_library
                    if (asset.get("source") in drama_library.MATCHABLE_SOURCES
                            and not asset.get("library_id")):
                        catalog = drama_library.load_catalog()
                        entry = (drama_library.local_match(
                            kind, str(asset.get("name") or ""), catalog)
                            if catalog else None)
                        if entry is not None:
                            drama_library.apply_match(self.store, asset, entry)
                            self.log.emit(f"{name} 复用公用库「{entry['name']}」（本地名称匹配），未出图")
                            done += 1
                            clear_failures(self.store, targets=[asset_id])
                            continue
                    if kind == "location":
                        if len(self.store.restore_scene_views(asset)) == len(SCENE_VIEWS):
                            self.log.emit(f"{name} 已有环景和正反向透视")
                            clear_failures(self.store, targets=[asset_id])
                            continue
                        anchor = self._anchor(asset)
                        generate_scene_panorama(self.store, client, asset, anchor)
                        done += 1
                        clear_failures(self.store, targets=[asset_id])
                        continue
                    if asset.get("kind") == "character":
                        views = self.store.makeup_paths(asset)
                        front = views.get("front")
                        missing = [view for view in MAKEUP_VIEWS if not front or view not in views]
                        if missing:
                            self._no_reference_guard("人物四角度定妆")
                        anchor = self._anchor(asset)
                        for view in missing:
                            if self._stop:
                                break
                            self.log.emit(f"{name} · {MAKEUP_VIEWS[view][0]}定妆照")
                            path = generate_makeup_view(self.store, client, asset, view,
                                                        front or anchor)
                            if view == "front":
                                front = path
                            done += 1
                        # 全身三视图：以定妆正面 / 已有全身正面为外观基准
                        fb = self.store.fullbody_paths(asset)
                        fb_front = fb.get("front")
                        fb_missing = [v for v in FULLBODY_VIEWS
                                      if "front" not in fb or v not in fb]
                        if fb_missing and self._stop:
                            break
                        if fb_missing:
                            self._no_reference_guard("人物全身三视图")
                        for view in fb_missing:
                            if self._stop:
                                break
                            self.log.emit(f"{name} · {FULLBODY_VIEWS[view][0]}全身照")
                            path = generate_fullbody_view(self.store, client, asset, view,
                                                          fb_front or front or anchor)
                            if view == "front":
                                fb_front = path
                            done += 1
                        if not missing and not fb_missing:
                            self.log.emit(f"{name} 已有定妆照和全身三视图")
                        clear_failures(self.store, targets=[asset_id])
                        continue
                    if asset.get("kind") == "prop":
                        propv = self.store.prop_paths(asset)
                        p_front = propv.get("front")
                        missing = [view for view in PROP_VIEWS if not p_front or view not in propv]
                        if missing:
                            self._no_reference_guard("道具三视图")
                        anchor = self._anchor(asset)
                        for view in missing:
                            if self._stop:
                                break
                            self.log.emit(f"{name} · {PROP_VIEWS[view][0]}三视图")
                            path = generate_prop_view(self.store, client, asset, view,
                                                      p_front or anchor)
                            if view == "front":
                                p_front = path
                            done += 1
                        if not missing:
                            self.log.emit(f"{name} 已有正/侧/背三视图")
                        clear_failures(self.store, targets=[asset_id])
                        continue
                    options = {"width": width, "height": height}
                    if self.config.get("provider") == "comfyui":
                        options["seed"] = asset_seed(self.store.assets_dir, asset_id)
                        # 参考图不能静默忽略：填了路径但文件缺失要报错，
                        # 只有配置了参考工作流才真正携带参考图。
                        ref = asset.get("reference_image") or asset.get("ref") or asset.get("image")
                        if ref:
                            ref_path = Path(ref)
                            if not ref_path.is_absolute():
                                ref_path = self.store.assets_dir / ref_path
                            if not ref_path.is_file():
                                raise ComfyError(f"参考图不存在：{ref}")
                            if self.config.get("reference_workflow"):
                                options["reference_image"] = ref_path
                                positive = plan["reference_prefix"] + positive
                                self.log.emit(f"{name}：使用固定参考图生成")
                    data = client.generate(positive, negative, prefix=asset_id, **options)
                except (ComfyError, ValueError) as e:
                    failed += 1
                    record_failure(self.store, stage="asset_images", target=asset_id,
                                   label=name, message=str(e))
                    self.log.emit(f"{name} 失败：{e}")
                    logger.warning("素材生成失败 %s: %s", name, e)
                    continue
                rel = self.store.save_generated_image(
                    asset_id,
                    data,
                    expected_size=(width, height),
                )
                record_generation(self.store, target=f"asset:{asset_id}:plain", client=client,
                                  seed=options.get("seed"), positive=positive, negative=negative,
                                  width=width, height=height,
                                  reference=str(options.get("reference_image") or ""),
                                  output=self.store.assets_dir / rel)
                warning = next(
                    (a.get("aspect_warning") for a in self.store.load_assets()
                     if a.get("id") == asset_id),
                    "",
                )
                if warning:
                    self.log.emit(f"{name} ⚠ {warning}")
                done += 1
                clear_failures(self.store, targets=[asset_id])
                self.log.emit(f"{name} → {rel}")
                self.progress.emit(f"已完成 {i}/{total}", int(i / total * 100))
        except Exception:
            self.failed.emit("图片生成失败，请检查服务配置和资产目录写入权限")
            return
        finally:
            if client is not None:
                client.close()

        tail = failures_summary(self.store, stage="asset_images")
        tail_text = f"；{tail}" if tail else ""
        if self._stop:
            self.finished_ok.emit(f"已停止：生成 {done} 张" + (f"，失败 {failed}" if failed else "") + tail_text)
        elif done == 0 and failed:
            self.failed.emit("一张都没生成成功，检查图片生成 API 配置或 ComfyUI 工作流")
        elif done == 0:
            self.finished_ok.emit("设定图已齐全")
        else:
            self.finished_ok.emit(f"生成 {done} 张素材" + (f"，失败 {failed}" if failed else "") + tail_text)
