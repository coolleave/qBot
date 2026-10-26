"""按素材库配置随机读取图片，不接收客户端文件路径。"""
import json
import random

from qbot.media import MAX_MEDIA_BYTES, MediaUpload, validate_media

from qbot.greeting_workflows import PROJECT_ROOT, load_catalog


def choose_library_image(library_id: str) -> tuple[MediaUpload, str]:
    """从指定素材库的 ready 素材中随机选一张；允许不同天重复抽中。"""
    try:
        library = next((item for item in load_catalog().libraries if item.id == library_id), None)
        if library is None:
            raise ValueError("素材库不存在，请重新配置任务")
        manifest_path = (PROJECT_ROOT / library.manifest).resolve()
        if not manifest_path.is_relative_to(PROJECT_ROOT.resolve()):
            raise ValueError("素材清单必须位于项目目录内")
        root = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        items = [item for item in manifest["items"] if item.get("status") == "ready"]
        if not items:
            raise ValueError("素材库没有可用图片")
        item = random.choice(items)
        path = (root / item["image"]).resolve()
        if path.parent != root or not path.is_file():
            raise ValueError("素材图片缺失或路径无效")
        with path.open("rb") as stream:
            media = validate_media("image", path.name, stream.read(MAX_MEDIA_BYTES + 1))
        return media, str(item.get("pose_description", ""))[:500]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("素材读取失败，请检查素材库及清单") from exc
