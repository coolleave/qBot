"""记忆配置、显式命令和模型输出边界，不包含数据库访问。"""
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    automatic: bool = False
    provider_id: int | None = Field(default=None, gt=0)
    budget_compatible: bool = False
    daily_calls: int = Field(default=20, ge=1, le=60)
    daily_tokens: int = Field(default=60000, ge=1000, le=180000)
    batch_messages: int = Field(default=10, ge=2, le=20)
    cooldown_minutes: int = Field(default=15, ge=5, le=1440)
    idle_minutes: int = Field(default=5, ge=5, le=1440)


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    conversation_id: int = Field(gt=0)
    content: str = Field(min_length=1, max_length=600)
    scope: Literal["personal", "group"] = "personal"
    category: Literal["note", "preference", "fact", "goal", "event", "request"] = "note"
    fact_key: str = Field(default="", max_length=80)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("到期时间需包含时区")
            return value.astimezone(timezone.utc)
        return None


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=400)
    category: Literal["preference", "fact", "goal", "event", "request"]
    fact_key: str = Field(min_length=1, max_length=80)
    source_ids: list[int] = Field(min_length=1, max_length=20)
    shared: bool = False
    replace_id: int | None = Field(default=None, gt=0)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime | None) -> datetime | None:
        return MemoryInput.normalize_expiry(value)


class MemoryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(max_length=1200)
    items: list[Candidate] = Field(default_factory=list, max_length=8)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def token_bound(value: str) -> int:
    """字节级 tokenizer 的保守上界；不是实际计量，仅用于预算预留。"""
    return len(value.encode("utf-8"))


def clip_bytes(value: str, budget: int) -> str:
    return value.encode("utf-8")[:max(0, budget)].decode("utf-8", errors="ignore")


def memory_command(text: str) -> tuple[str, str] | None:
    text = text.strip()
    if text in ("/memory", "你记得我什么", "你记得我什么？", "你记得我什么?"):
        return "list", ""
    for prefix in ("/remember ", "记住：", "记住:"):
        if text.startswith(prefix):
            return "add", text[len(prefix):].strip()
    for prefix, kind in (("/forget ", "forget"), ("/memory-edit ", "edit"), ("/remember-group ", "group")):
        if text.startswith(prefix):
            return kind, text[len(prefix):].strip()
    if text in ("记住这个", "忘掉这个", "/remember", "/forget", "/memory-edit", "/remember-group"):
        return "help", ""
    return None


def useful_text(text: str) -> bool:
    return bool(text.strip()) and not memory_command(text) and text.strip() != "/reset" and not re.fullmatch(
        r"[\s。！!？?]*(嗯+|哦+|好[的吧]?|收到|谢谢|你好|哈哈+|ok|OK)[\s。！!？?]*", text)
