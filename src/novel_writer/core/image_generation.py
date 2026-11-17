"""资产图片生成：兼容图片 API 与已有 ComfyUI 配置。"""
import base64
import io
from contextlib import contextmanager
import httpx
from .comfyui import ComfyUIClient, ComfyError, config_from_app as comfy_config
from .logger import get_logger

logger = get_logger(__name__)


def _looks_like_image(data: bytes) -> bool:
    """魔数校验，替代 core 层不许用的 QImage.isNull。"""
    if not data:
        return False
    if data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM")):
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _to_png(data: bytes) -> bytes:
    """PNG 直通；其他格式在 Pillow 可用时归一成 PNG（保存侧固定 .png 扩展名）。"""
    if data.startswith(b"\x89PNG"):
        return data
    try:
        from PIL import Image
    except ImportError:
        logger.warning("图片 API 返回非 PNG 内容且未安装 Pillow，按原始格式保存")
        return data
    with Image.open(io.BytesIO(data)) as im:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def config_from_app(cfg=None):
    if cfg is None:
        from .app_config import load_config
        cfg = load_config()
    section = dict(cfg.get("image_generation") or {})
    provider = section.get("provider", "comfyui")
    if provider == "comfyui":
        return {**comfy_config(cfg), "provider": "comfyui"}
    return section


def configuration_error(cfg):
    provider = cfg.get("provider")
    if provider == "comfyui":
        return "请配置 ComfyUI 工作流模板。" if not cfg.get("workflow") else ""
    if provider == "codex":
        from .codex_cli import codex_binary

        if not (cfg.get("codex_bin") or codex_binary()):
            return (
                "没找到 Codex CLI：先安装 codex 并放进 PATH，"
                "或改用 ComfyUI / 图片 API。"
            )
        return ""
    if not cfg.get("base_url") or not cfg.get("model"):
        return "请配置图片 API 地址和图片模型。"
    return ""


class ImageAPIClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = httpx.Client(timeout=float(cfg.get("timeout") or 300))

    def generate(self, positive, negative="", prefix="asset", width=1024, height=1024, reference=None):
        payload = {
            "model": self.cfg["model"], "prompt": positive, "n": 1,
            "size": f"{int(width)}x{int(height)}",
        }
        if negative:
            if self.cfg.get("supports_negative_prompt"):
                # 服务支持独立负面词通道时，否定词不要混进正向词
                payload["negative_prompt"] = negative
            else:
                # 纯 OpenAI 兼容接口没有负面词字段：只能把否定词附在正向词末尾。
                # 这是已知降级（文章：写"不要"经常没用），ComfyUI 路径有真正的负面词节点。
                payload["prompt"] = positive + "\n避免：" + negative
        headers = {}
        if self.cfg.get("api_key"):
            headers["Authorization"] = "Bearer " + self.cfg["api_key"]
        try:
            if reference:
                from pathlib import Path
                with Path(reference).open("rb") as stream:
                    response = self.client.post(self.cfg["base_url"].rstrip("/") + "/images/edits",
                        headers=headers, data={k: str(v) for k, v in payload.items()},
                        files={"image": (Path(reference).name, stream, "image/png")})
            else:
                response = self.client.post(self.cfg["base_url"].rstrip("/") + "/images/generations",
                    headers=headers, json=payload)
            response.raise_for_status()
            item = response.json()["data"][0]
            if item.get("b64_json"):
                data = base64.b64decode(item["b64_json"], validate=True)
            elif item.get("url"):
                # 下载链接不携带 API 密钥。
                response = self.client.get(item["url"], follow_redirects=True)
                response.raise_for_status()
                data = response.content
            else:
                raise ValueError("响应缺少 b64_json 或 url")
            if not _looks_like_image(data):
                raise ValueError("服务返回的内容不是有效图片")
            return _to_png(data)
        except httpx.HTTPStatusError as exc:
            raise ComfyError(f"图片 API 请求失败（HTTP {exc.response.status_code}），请检查地址、密钥和模型") from None
        except (httpx.RequestError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ComfyError("图片生成失败，请检查服务连接及响应格式（data[].b64_json / url）") from None

    def close(self):
        self.client.close()


def create_client(cfg):
    if cfg.get("provider") == "comfyui":
        return ComfyUIClient(base_url=cfg.get("base_url", ""),
            workflow_path=cfg.get("workflow", ""), timeout=float(cfg.get("timeout") or 300),
            reference_workflow=cfg.get("reference_workflow", ""))
    if cfg.get("provider") == "codex":
        from .codex_cli import CodexImageClient

        return CodexImageClient(cfg)
    return ImageAPIClient(cfg)


def generate_image(client, positive, negative="", *, prefix="asset",
                   width=1024, height=1024, reference=None, seed=None):
    """按客户端能力派发参数：ComfyUI 用 `reference_image`，Codex 用 `reference`。

    图片 API 通过 images/edits 发送参考图。
    """
    import inspect

    if reference and isinstance(client, ComfyUIClient) and not client.reference_workflow:
        raise ComfyError("参考图生成需要 ComfyUI 一致性工作流；不能静默忽略参考图")

    kwargs = {"prefix": prefix, "width": width, "height": height}
    try:
        params = inspect.signature(client.generate).parameters
        if "reference_image" in params and reference:
            kwargs["reference_image"] = reference
        elif "reference" in params and reference:
            kwargs["reference"] = reference
        if "seed" in params and seed is not None:
            kwargs["seed"] = seed
    except (TypeError, ValueError):
        pass
    return client.generate(positive, negative, **kwargs)


def generate_makeup_view(store, client, asset, view, reference=None):
    from .drama import MAKEUP_VIEWS, asset_canvas, asset_seed
    from .drama_records import record_generation

    positive, negative = store.makeup_prompt(asset, view)
    prefix = f"{asset['id']}_{view}"
    width, height = asset_canvas("character")
    seed = asset_seed(store.assets_dir, prefix)
    with image_reference_canvas(reference, (width, height)) as prepared:
        data = generate_image(client, positive, negative, prefix=prefix, width=width, height=height,
                              reference=prepared, seed=seed)
    path = store.save_makeup_view(asset["id"], view, data)
    record_generation(store, target=f"asset:{asset['id']}:makeup_{view}", client=client,
                      seed=seed, positive=positive, negative=negative, width=width, height=height,
                      reference=str(reference or ""), output=path)
    return path


def generate_fullbody_view(store, client, asset, view, reference=None):
    """人物全身三视图：以定妆/全身正面为外观基准，全身镜头的参考层。"""
    from .drama import FULLBODY_VIEWS, asset_canvas, asset_seed
    from .drama_records import record_generation

    positive, negative = store.fullbody_prompt(asset, view)
    prefix = f"{asset['id']}_fb_{view}"
    width, height = asset_canvas("character")
    seed = asset_seed(store.assets_dir, prefix)
    with image_reference_canvas(reference, (width, height)) as prepared:
        data = generate_image(client, positive, negative, prefix=prefix, width=width, height=height,
                              reference=prepared, seed=seed)
    path = store.save_fullbody_view(asset["id"], view, data)
    record_generation(store, target=f"asset:{asset['id']}:fullbody_{view}", client=client,
                      seed=seed, positive=positive, negative=negative, width=width, height=height,
                      reference=str(reference or ""), output=path)
    return path


def generate_prop_view(store, client, asset, view, reference=None):
    """道具三视图：正面先行，侧面/背面以正面照为同一物品基准。"""
    from .drama import PROP_VIEWS, asset_canvas, asset_seed
    from .drama_records import record_generation

    positive, negative = store.prop_prompt(asset, view)
    prefix = f"{asset['id']}_{view}"
    width, height = asset_canvas("prop")
    seed = asset_seed(store.assets_dir, prefix)
    with image_reference_canvas(reference, (width, height)) as prepared:
        data = generate_image(client, positive, negative, prefix=prefix, width=width, height=height,
                              reference=prepared, seed=seed)
    path = store.save_prop_view(asset["id"], view, data)
    record_generation(store, target=f"asset:{asset['id']}:prop_{view}", client=client,
                      seed=seed, positive=positive, negative=negative, width=width, height=height,
                      reference=str(reference or ""), output=path)
    return path


def generate_scene_panorama(store, client, asset, reference=None):
    from .drama import asset_seed
    from .drama_records import record_generation
    from .drama_scene import SCENE_PANORAMA_SIZE

    positive, negative = store.scene_prompt(asset)
    prefix = f"{asset['id']}_panorama"
    width, height = SCENE_PANORAMA_SIZE
    seed = asset_seed(store.assets_dir, prefix)
    with image_reference_canvas(reference, (width, height)) as prepared:
        data = generate_image(client, positive, negative, prefix=prefix, width=width, height=height,
                              reference=prepared, seed=seed)
    path = store.save_scene_panorama(asset["id"], data)
    record_generation(store, target=f"asset:{asset['id']}:scene_panorama", client=client,
                      seed=seed, positive=positive, negative=negative, width=width, height=height,
                      reference=str(reference or ""), output=path)
    return path


@contextmanager
def image_reference_canvas(reference, size):
    """为沿用参考图尺寸的工作流准备画布，保留完整参考图。"""
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from PIL import Image, ImageOps

    if reference is None:
        yield None
        return
    with Image.open(reference) as image:
        if image.size == size and image.format == "PNG":
            prepared = None
        else:
            rgb = image.convert("RGB")
            prepared = ImageOps.pad(rgb, size, method=Image.Resampling.LANCZOS,
                                    color=rgb.getpixel((0, 0)))
    if prepared is None:
        yield reference
    else:
        with TemporaryDirectory(prefix="drama-reference-") as folder:
            path = Path(folder) / Path(reference).with_suffix(".png").name
            prepared.save(path)
            yield path
