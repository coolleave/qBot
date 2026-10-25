"""管理员显式触发的单次模型连通性检查，不写聊天记录。"""
import asyncio
import logging
import time

from langchain_core.messages import HumanMessage
from openai import APIConnectionError, APIStatusError, APITimeoutError

from qbot.config import Settings
from qbot.model import provider_model

logger = logging.getLogger(__name__)


async def probe_provider(settings: Settings, provider: dict, content: str | list = "你好") -> dict:
    # 禁用自动重试，耗时表示完整模型调用时间（非首 token 延迟）。
    config = settings.model_copy(update={"model_max_retries": 0,
        "model_timeout_seconds": 20, "model_max_tokens": 64})
    model = None
    started = time.perf_counter()
    result = {"success": False, "reply": "", "message": ""}
    try:
        async with asyncio.timeout(25):
            model = provider_model(config, provider)
            response = await model.ainvoke([HumanMessage(content=content)], config={"callbacks": []})
            content = response.content
            reply = content if isinstance(content, str) else "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            # 防止异常服务将请求中的密钥原样反射到后台页面。
            reply = reply.replace(provider["api_key"], "[已隐藏密钥]").strip()
            result.update(success=bool(reply), reply=reply[:1000],
                          message="调用成功" if reply else "接口返回成功，但没有文本回复")
    except (TimeoutError, APITimeoutError):
        result["message"] = "请求超时，请检查网络或模型服务状态"
    except APIConnectionError:
        result["message"] = "无法连接模型服务，请检查服务地址和网络"
    except APIStatusError as exc:
        messages = {401: "认证失败，请检查 API Key", 403: "访问被拒绝，请检查账号权限",
                    402: "余额或付费状态异常", 404: "接口或模型不存在，请检查地址和模型名称",
                    429: "请求限流或额度不足"}
        result["message"] = messages.get(exc.status_code, "模型服务返回错误")
        result["http_status"] = exc.status_code
    except Exception as exc:
        logger.warning("供应商连接检查失败：%s", type(exc).__name__)
        result["message"] = "调用失败，请检查模型配置或响应格式"
    finally:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        if model is not None:
            try:
                async with asyncio.timeout(5):
                    if getattr(model, "root_async_client", None):
                        await model.root_async_client.close()
                    if getattr(model, "root_client", None):
                        await asyncio.to_thread(model.root_client.close)
            except Exception as exc:
                logger.warning("供应商检查连接释放失败：%s", type(exc).__name__)
    return result
