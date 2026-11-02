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


def provider_model(settings: Settings, provider: dict | None):
    if provider is None:
        if settings.admin_enabled:
            raise ValueError("请在后台添加、启用并选择默认模型供应商")
        return create_model(settings)
    if provider.get("model_type", "chat") != "chat":
        raise ValueError("聊天只能使用 chat 类型模型")
    thinking = thinking_parameters(provider)
    common = dict(model=provider["model_name"], api_key=provider["api_key"],
                  timeout=settings.model_timeout_seconds, max_retries=settings.model_max_retries,
                  max_tokens=settings.model_max_tokens,
                  # 调用方会关闭模型客户端，必须独占连接池，不能使用 LangChain 默认缓存。
                  http_client=DefaultHttpxClient(timeout=settings.model_timeout_seconds),
                  http_async_client=DefaultAsyncHttpxClient(timeout=settings.model_timeout_seconds))
    if thinking:
        common["extra_body"] = thinking
    if provider["api_type"] == "deepseek":
        return ChatDeepSeek(**common, api_base=provider["base_url"])
    return ChatOpenAI(**common, base_url=provider["base_url"], use_responses_api=False)
