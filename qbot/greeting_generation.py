"""按机器人人设生成问候草稿，不保存任务或发送 QQ 消息。"""
import asyncio
import json
import logging
from importlib.resources import files

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser

from qbot.config import Settings
from qbot.model import provider_model

logger = logging.getLogger(__name__)


async def generate_greeting(settings: Settings, provider: dict, persona: str, record: dict, slot: str) -> str:
    config = settings.model_copy(update={"model_max_retries": 0,
        "model_timeout_seconds": 30, "model_max_tokens": 400})
    prompts = files("qbot").joinpath("prompts")
    context = {"时段": "早间" if slot == "morning" else "晚间",
               "场景": "群聊" if record["source"] == "qq_group" else "私聊"}
    # 群消息面向整个群，避免把绑定成员的私人称呼注入群问候。
    if record["source"] == "qq_c2c":
        context.update(姓名=record.get("member_name") or record.get("contact_name"),
                       家庭角色=record.get("family_role"))
    model = None
    try:
        async with asyncio.timeout(35):
            model = provider_model(config, provider)
            response = await model.ainvoke([
                SystemMessage(content=persona or prompts.joinpath("system.txt").read_text("utf-8")),
                SystemMessage(content=prompts.joinpath("greeting.txt").read_text("utf-8")),
                HumanMessage(content=json.dumps(context, ensure_ascii=False)),
            ], config={"callbacks": []})
            text = StrOutputParser().invoke(response).strip()
            if provider.get("api_key"):
                text = text.replace(provider["api_key"], "[已隐藏密钥]")
            if not text or len(text) > 200:
                raise ValueError("模型未返回 1 至 200 字的文案，请重新生成")
            return text
    finally:
        if model is not None:
            try:
                async with asyncio.timeout(5):
                    if getattr(model, "root_async_client", None):
                        await model.root_async_client.close()
                    if getattr(model, "root_client", None):
                        await asyncio.to_thread(model.root_client.close)
            except Exception as exc:
                logger.warning("问候生成连接释放失败：%s", type(exc).__name__)
