from urllib.parse import urlsplit
from httpx import Limits

from openai import DefaultHttpxClient, DefaultAsyncHttpxClient

from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from qbot.config import Settings


def create_model(settings: Settings) -> ChatDeepSeek:
    """集中配置模型，默认关闭深度思考以控制聊天延迟。"""
    settings.require_credentials()
    return ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        api_base=settings.deepseek_base_url,
        timeout=settings.model_timeout_seconds,
        max_retries=settings.model_max_retries,
        max_tokens=settings.model_max_tokens,
        extra_body={"thinking": {"type": "disabled"}},
    )


def thinking_parameters(provider: dict) -> dict:
    """将显式模式映射到所选接口；默认模式不覆盖供应商行为。"""
    mode = provider.get("thinking_mode", "enabled" if provider.get("enable_thinking") else "default")
    if mode not in ("default", "enabled", "disabled"):
        raise ValueError("无效的思考模式")
    if mode == "default":
        return {}
    if provider.get("model_type", "chat") != "chat":
        raise ValueError("思考模式仅用于 chat 模型")
    if mode == "disabled" and provider["model_name"].lower() == "qwen3.8-2.4t-a95b":
        raise ValueError("该模型仅支持思考，请选择跟随模型默认或开启，或更换支持关闭思考的模型")
    if provider["api_type"] == "deepseek":
        return {"thinking": {"type": mode}}
    # 兼容接口沿用 enable_thinking 扩展；不支持时由供应商报错，不静默回退。
    return {"enable_thinking": mode == "enabled"}


def web_search_parameters(provider: dict) -> dict:
    """仅为显式开启的百炼兼容聊天请求生成搜索参数。"""
    mode = provider.get("web_search_mode", "disabled")
    if mode not in ("disabled", "auto", "forced"):
        raise ValueError("无效的联网搜索模式")
    fetch = provider.get("enable_web_fetch", False)
    if fetch and mode == "disabled":
        raise ValueError("网页抓取需先开启联网搜索（自动或强制）")
    if fetch and provider.get("thinking_mode", "default") != "enabled":
        raise ValueError("网页抓取需将思考模式设为开启")
    if mode == "disabled":
        return {}
    if (provider.get("model_type", "chat") != "chat"
            or provider.get("api_type") not in ("openai_compatible", "openai_chat")):
        raise ValueError("联网搜索仅支持百炼 OpenAI 兼容 chat 接口")
    url = urlsplit(provider.get("base_url", ""))
    host = url.hostname or ""
    if (url.scheme != "https" or url.username or url.password or url.query or url.fragment
            or not (host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com",
                             "dashscope-us.aliyuncs.com"} or host.endswith(".maas.aliyuncs.com"))
            or url.path.rstrip("/") != "/compatible-mode/v1"):
        raise ValueError("联网搜索请使用百炼官方 compatible-mode/v1 地址")
    # 不推断模型能力；模型或地域不支持时交由供应商明确报错。
    return {"enable_search": True, "search_options": {
        "forced_search": mode == "forced", "search_strategy": "agent_max" if fetch else "turbo"}}


def provider_model(settings: Settings, provider: dict | None, *, allow_web_search: bool = True,
                   connection_limits: Limits | None = None):
    if provider is None:
        if settings.admin_enabled:
            raise ValueError("请在后台添加、启用并选择默认模型供应商")
        return create_model(settings)
    if provider.get("model_type", "chat") != "chat":
        raise ValueError("聊天只能使用 chat 类型模型")
    extra_body = thinking_parameters(provider)
    if allow_web_search:
        extra_body.update(web_search_parameters(provider))
    transport_options = {"limits": connection_limits} if connection_limits is not None else {}
    common = dict(model=provider["model_name"], api_key=provider["api_key"],
                  timeout=settings.model_timeout_seconds, max_retries=settings.model_max_retries,
                  max_tokens=settings.model_max_tokens,
                  # 调用方会关闭模型客户端，必须独占连接池，不能使用 LangChain 默认缓存。
                  http_client=DefaultHttpxClient(timeout=settings.model_timeout_seconds, **transport_options),
                  http_async_client=DefaultAsyncHttpxClient(timeout=settings.model_timeout_seconds, **transport_options))
    if extra_body:
        common["extra_body"] = extra_body
    if allow_web_search and provider.get("enable_web_fetch", False):
        # 百炼 agent_max 不支持非流式请求；ainvoke 会聚合正文及用量后返回。
        common.update(streaming=True, stream_usage=True)
    if provider["api_type"] == "deepseek":
        return ChatDeepSeek(**common, api_base=provider["base_url"])
    return ChatOpenAI(**common, base_url=provider["base_url"], use_responses_api=False)
