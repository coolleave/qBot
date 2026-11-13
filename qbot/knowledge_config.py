"""知识库输入约束；上传路径只使用服务端生成的标识。"""
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

EXTENSIONS = {".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv"}
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_CHUNKS = 1000
MAX_TEXT_BYTES = 2 * 1024 * 1024


class KnowledgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    enabled: bool = Field(default=False, strict=True)
    mode: Literal["assist", "strict"] = "assist"
    base_url: str = Field(default="", max_length=2048)
    model_name: str = Field(default="", max_length=200)
    api_key: SecretStr = SecretStr("")

    @field_validator("base_url")
    @classmethod
    def https_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return ""
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Embedding 地址须为无凭证、查询参数的 HTTPS 基础地址")
        if url.path.endswith("/embeddings"):
            raise ValueError("请填写基础地址，不包含 /embeddings")
        return value

    @field_validator("model_name")
    @classmethod
    def clean_model(cls, value: str) -> str:
        return value.strip()


def file_path(root: str, bot_id: int, file_id: str, extension: str) -> Path:
    if bot_id <= 0 or str(UUID(file_id)) != file_id or extension not in EXTENSIONS:
        raise ValueError("文件标识不合法")
    base = Path(root).resolve()
    path = (base / str(bot_id) / file_id / ("source" + extension)).resolve()
    if not path.is_relative_to(base):
        raise ValueError("文件路径越界")
    return path
