"""场景环景及透视参考图。"""
from math import atan2, hypot, pi, radians, tan
from pathlib import Path

SCENE_PANORAMA_SIZE = (2048, 1024)
SCENE_VIEW_SIZE = (1280, 720)
SCENE_VIEWS = {"panorama": "360° 环景", "forward": "正向 · 前景 / 后景", "reverse": "反向 · 前景 / 后景"}
SCENE_PANORAMA_PROMPT = (
    "单张 360° 等距柱状投影环景（equirectangular），2:1 横幅，水平 360°、垂直 180°。"
    "相机固定在场景中央、视线平齐，地平线在画面中线，左右边界为同一个方位且连续衔接。"
    "画幅中心朝主空间或主入口，环景接缝方向是它的反向机位。"
    "按真实尺度布置一个可连通的空间，近处前景、中景通道、远处后景清楚可辨。"
    "复杂空间用两到三个固定视觉锚点帮助辨认方向，例如入口、楼梯、柱廊或标志性陈设；"
    "以已有设定为准，保持其方位、数量、色彩、材质和光源方向，保留走位通道。"
    "只画这个空间的空镜，全幅为一张连续环景。"
)


def scene_view_for_shot(text: str) -> str:
    return "reverse" if any(term in text for term in ("反打", "反向机位", "反向视角", "朝向来路")) else "forward"


def project_scene_views(source: Path, outputs: dict[str, Path]) -> None:
    """从同一环景投影水平 90° 的正向与反向透视。"""
    from PIL import Image

    with Image.open(source) as image:
        width, height = image.size
        wrapped = Image.new("RGB", (width * 3, height))
        for column in range(3):
            wrapped.paste(image, (column * width, 0))
        out_width, out_height = SCENE_VIEW_SIZE
        focal = out_width / (2 * tan(radians(45)))
        for view, yaw in (("forward", 0), ("reverse", pi)):
            def source_point(x, y):
                horizontal = (x - out_width / 2) / focal
                vertical = (out_height / 2 - y) / focal
                longitude = yaw + atan2(horizontal, 1)
                latitude = atan2(vertical, hypot(horizontal, 1))
                return width * (1.5 + longitude / (2 * pi)), height * (.5 - latitude / pi)

            mesh = []
            for y in range(0, out_height, 32):
                bottom = min(y + 32, out_height)
                for x in range(0, out_width, 32):
                    right = min(x + 32, out_width)
                    corners = ((x, y), (x, bottom), (right, bottom), (right, y))
                    quad = tuple(value for corner in corners for value in source_point(*corner))
                    mesh.append(((x, y, right, bottom), quad))
            wrapped.transform(SCENE_VIEW_SIZE, Image.Transform.MESH, mesh, Image.Resampling.BICUBIC).save(outputs[view])
