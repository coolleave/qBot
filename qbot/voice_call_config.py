"""网页通话的配置和供应商协议边界。"""
from urllib.parse import urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field


class VoiceCallConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: bool = False
    chat_provider_id: int | None = Field(default=None, gt=0)
    asr_provider_id: int | None = Field(default=None, gt=0)
    tts_provider_id: int | None = Field(default=None, gt=0)
    voice: str = Field(default="Bunny", min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_ -]+$")
    max_seconds: int = Field(default=900, ge=60, le=1800)
    idle_seconds: int = Field(default=60, ge=30, le=300)


def validate_voice_provider(provider: dict, kind: str) -> None:
    if provider.get("model_type") != kind:
        raise ValueError("通话供应商类型不匹配")
    if kind == "chat":
        return
    prefix = {"asr_realtime": "qwen3-asr-flash-realtime", "tts": "qwen3-tts-flash-realtime"}[kind]
    model = provider["model_name"]
    if provider["api_type"] != "openai_compatible" or not (model == prefix or model.startswith(prefix + "-")):
        raise ValueError(f"通话当前仅支持 {prefix} 及其快照模型，请选择千问原生语音接口")
    realtime_endpoint(provider)


def realtime_endpoint(provider: dict) -> str:
    url = urlsplit(provider["base_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("实时语音服务地址必须是无凭证的 HTTPS 地址")
    path = url.path.rstrip("/")
    if path in ("", "/compatible-mode/v1"):
        path = "/api-ws/v1/realtime"
    if path != "/api-ws/v1/realtime":
        raise ValueError("实时语音服务路径应为 /api-ws/v1/realtime 或留空")
    return urlunsplit(("wss", url.netloc, path, urlencode({"model": provider["model_name"]}), ""))
