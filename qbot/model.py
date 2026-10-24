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


def provider_model(settings: Settings, provider: dict | None):
    if provider is None:
        return create_model(settings)
    common = dict(model=provider["model_name"], api_key=provider["api_key"],
                  timeout=settings.model_timeout_seconds, max_retries=settings.model_max_retries,
                  max_tokens=settings.model_max_tokens)
    if provider["api_type"] == "deepseek":
        return ChatDeepSeek(**common, api_base=provider["base_url"], extra_body={"thinking": {"type": "disabled"}})
    return ChatOpenAI(**common, base_url=provider["base_url"], use_responses_api=False)
