"""定时消息的配置组合；模板只是配置，执行器不依赖模板编号。"""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = PROJECT_ROOT / "config" / "scheduled_templates.json"


class GreetingWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    content_source: Literal["fixed", "ai"] = "fixed"
    image_source: Literal["none", "upload", "library"] = "none"
    output: Literal["text", "audio"] = "text"
    library_id: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_library(self):
        if self.image_source == "library" and not self.library_id:
            raise ValueError("随机图片需选择素材库")
        if self.image_source != "library":
            self.library_id = None
        return self


class Library(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    manifest: str = Field(min_length=1, max_length=500)


class Template(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    workflow: GreetingWorkflow


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    libraries: list[Library]
    templates: list[Template]


def load_catalog() -> Catalog:
    try:
        catalog = Catalog.model_validate(json.loads(CATALOG_PATH.read_text(encoding="utf-8")))
        libraries = {item.id for item in catalog.libraries}
        templates = {item.id for item in catalog.templates}
        if len(libraries) != len(catalog.libraries) or len(templates) != len(catalog.templates) or "custom" in templates:
            raise ValueError("模板或素材库标识重复，custom 为保留名称")
        for item in catalog.templates:
            if item.workflow.image_source == "library" and item.workflow.library_id not in libraries:
                raise ValueError("模板引用了不存在的素材库")
        return catalog
    except (OSError, ValueError) as exc:
        raise ValueError("定时模板配置无效，请检查 config/scheduled_templates.json") from exc


def resolve_workflow(delivery: dict) -> GreetingWorkflow:
    """新配置使用快照；旧 delivery_mode 在唯一入口映射为流程。"""
    if delivery.get("workflow") is not None:
        return GreetingWorkflow.model_validate(delivery["workflow"])
    mode = delivery.get("delivery_mode", "text")
    template = next((item for item in load_catalog().templates if item.id == mode), None)
    if template is None:
        raise ValueError("任务模板不存在，请重新选择流程并保存")
    return template.workflow.model_copy(deep=True)


def public_catalog() -> dict:
    catalog = load_catalog()
    return {"templates": [item.model_dump() for item in catalog.templates],
            "libraries": [{"id": item.id, "name": item.name} for item in catalog.libraries]}
