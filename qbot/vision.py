"""独立视觉理解阶段：仅返回描述，最终回复由聊天模型生成。"""
import asyncio
import logging
import time
from importlib.resources import files
from contextlib import nullcontext

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser

from qbot.config import Settings
from qbot.model import provider_model
from qbot.multimodal import MEDIA_SLOTS

logger = logging.getLogger(__name__)


class VisionError(RuntimeError):
    pass


async def describe_images(settings: Settings, provider: dict, text: str,
                          blocks: list[dict], details: dict, *, slot_acquired: bool = False) -> str:
    started = time.monotonic()
    model = None
    details.update(status="processing", provider=provider["name"], model=provider["model_name"])
    try:
        # 后台上传试用已持有媒体名额，避免嵌套获取同一信号量。
        async with asyncio.timeout(30), (nullcontext() if slot_acquired else MEDIA_SLOTS):
            config = settings.model_copy(update={"model_max_retries": 0,
                "model_timeout_seconds": 25, "model_max_tokens": 1800})
            model = provider_model(config, provider)
            output = await model.ainvoke([
                SystemMessage(content=files("qbot").joinpath("prompts/vision.txt").read_text("utf-8")),
                HumanMessage(content=[{"type": "text", "text": text}] + blocks),
            ], config={"callbacks": []})
            summary = StrOutputParser().invoke(output).strip()
            if not summary:
                raise ValueError("empty vision response")
            if provider.get("api_key"):
                summary = summary.replace(provider["api_key"], "[已隐藏密钥]")
            if len(summary) > 3000:
                summary = summary[:2970] + "\n（视觉说明过长，已截断）"
            usage = getattr(output, "usage_metadata", None) or {}
            details.update(status="success", summary=summary, usage={k: v for k, v in usage.items()
                if k in ("input_tokens", "output_tokens", "total_tokens") and type(v) in (int, float)})
            return summary
    except Exception as exc:
        details.update(status="failed", error="视觉理解失败或超时，请检查视觉模型配置和额度")
        logger.warning("视觉理解失败：%s", type(exc).__name__)
        raise VisionError(details["error"]) from None
    finally:
        if details["status"] == "processing":
            details.update(status="failed", error="视觉理解被取消或超过消息处理时限")
        details["latency_ms"] = round((time.monotonic() - started) * 1000)
        if model is not None:
            try:
                async with asyncio.timeout(5):
                    if getattr(model, "root_async_client", None):
                        await model.root_async_client.close()
                    if getattr(model, "root_client", None):
                        await asyncio.to_thread(model.root_client.close)
            except Exception as exc:
                logger.warning("视觉连接释放失败：%s", type(exc).__name__)
