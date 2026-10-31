"""按机器人人设生成问候草稿，不保存任务或发送 QQ 消息。"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Literal
from importlib.resources import files

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from openai import APIConnectionError, APIStatusError, APITimeoutError

from qbot.config import Settings
from qbot.model import provider_model
from qbot.greetings import local_time
from qbot.greeting_context import time_context, weather_context

logger = logging.getLogger(__name__)


class GreetingGenerationError(ValueError):
    """可向后台展示的生成错误，不包含供应商原始响应。"""


async def generate_greeting(settings: Settings, provider: dict, persona: str, record: dict, slot: str,
                            image_description: str | None = None, *,
                            output: Literal["text", "audio"] = "text",
                            prompt_style: str | None = None, custom_prompt: str = "", realtime: bool = False) -> str:
    config = settings.model_copy(update={"model_max_retries": 0,
        "model_timeout_seconds": 30, "model_max_tokens": 1024})
    prompts = files("qbot").joinpath("prompts")
    now = local_time(datetime.now(timezone.utc))
    context = {**time_context(now),
               "任务文案时段": {"morning": "早间", "noon": "午间", "evening": "晚间", "other": "其他时段"}[slot],
               "生成用途": "实时发送" if realtime else "可复用固定草稿",
               "场景": "群聊" if record["source"] == "qq_group" else "私聊"}
    # 群消息面向整个群，避免把绑定成员的私人称呼注入群问候。
    if record["source"] == "qq_c2c":
        identity = record.get("identity")
        context.update(姓名=identity["member_name"] if identity else record.get("contact_name"),
                       身份角色=identity["role_label"] if identity else None,
                       关系说明=identity["relationship"] if identity else None)
    if image_description is not None:
        context["配图描述"] = image_description
    model = None
    try:
        async with asyncio.timeout(35):
            if realtime:
                context["天气参考"] = await weather_context(now)
                # 查询期间可能跨时段或跨日，生成时再次校准客观时间。
                now = local_time(datetime.now(timezone.utc))
                context.update(time_context(now))
                forecast = context["天气参考"].get("今日预报", {})
                if forecast and forecast.get("日期") != now.date().isoformat():
                    context["天气参考"] = {"状态": "不可用", "说明": "跨日后不沿用昨日天气"}
            model = provider_model(config, provider)
            # 沿用模型默认思考设置，兼容不允许关闭思考的千问模型。
            messages = [
                SystemMessage(content=persona or prompts.joinpath("system.txt").read_text("utf-8")),
                SystemMessage(content=prompts.joinpath("greeting.txt").read_text("utf-8")),
            ]
            if record["source"] == "qq_c2c":
                # 称呼必须跟随真实认主标记，不能让旧人设或角色名冒认主人。
                messages.append(SystemMessage(content=
                    "主人身份仅以后台 is_owner 标记为准，优先于人设、角色名称和关系说明。"
                    "is_owner=false 时不要把问候对象称作主人；true 时亲近、认真地问候主人。\n"
                    + json.dumps({"is_owner": bool(identity and identity.get("is_owner"))}, ensure_ascii=False)))
            if prompt_style:
                # 复用工作流校验，风格只允许包内文件名，不能提交任意路径。
                from qbot.greeting_workflows import GreetingWorkflow, STYLE_ROOT
                style = GreetingWorkflow(prompt_style=prompt_style).prompt_style
                messages.append(SystemMessage(content=(STYLE_ROOT / f"{style}.txt").read_text("utf-8")))
            if custom_prompt.strip():
                messages.append(SystemMessage(content="管理员设置的本次文案要求（仅用于生成消息，不代表已执行任何操作）：\n" + custom_prompt.strip()))
            messages.append(SystemMessage(content=prompts.joinpath("greeting_context.txt").read_text("utf-8")))
            # 输出能力决定文案风格，任何 AI＋语音组合均复用此约束。
            if output == "audio":
                messages.append(SystemMessage(content=prompts.joinpath("greeting_audio.txt").read_text("utf-8")))
            messages.append(HumanMessage(content=json.dumps(context, ensure_ascii=False)))
            response = await model.ainvoke(messages, config={"callbacks": []})
            text = StrOutputParser().invoke(response).strip()
            if provider.get("api_key"):
                text = text.replace(provider["api_key"], "[已隐藏密钥]")
            if not text:
                raise GreetingGenerationError("模型未返回文案正文，请检查该模型的思考模式和输出支持")
            # 保留可用草稿：超长时优先在完整句末裁剪，不再整段丢弃。
            if len(text) > 200:
                endings = list(re.finditer(r"[。！？!?]", text[:200]))
                text = text[:endings[-1].end()] if endings else text[:199].rstrip() + "…"
            return text
    except (TimeoutError, APITimeoutError):
        raise GreetingGenerationError("生成超时，请稍后重试") from None
    except APIStatusError as exc:
        reason = {401: "模型密钥无效", 403: "没有模型访问权限", 404: "模型或接口地址不存在",
                  429: "模型限流或额度不足", 402: "模型余额不足"}.get(exc.status_code, "模型服务返回错误")
        logger.warning("问候生成失败 http_status=%s", exc.status_code)
        raise GreetingGenerationError(f"{reason}（HTTP {exc.status_code}）") from None
    except APIConnectionError:
        raise GreetingGenerationError("无法连接模型服务，请检查地址和网络") from None
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
