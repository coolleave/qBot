"""管理员显式触发的单次模型连通性检查，不写聊天记录。"""
import asyncio
import logging
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError

from qbot.config import Settings
from qbot.model import provider_model

logger = logging.getLogger(__name__)


async def probe_provider(settings: Settings, provider: dict, content: str | list = "你好") -> dict:
    # 单次流式探测区分首正文等待和完整调用耗时，不自动重试。
    config = settings.model_copy(update={"model_max_retries": 0,
        # 思考型模型的推理也可能占用输出额度，64 token 容易在正文生成前耗尽。
        "model_timeout_seconds": 120, "model_max_tokens": 4096})
    model = None
    started = time.perf_counter()
    result = {"success": False, "reply": "", "message": "", "reasoning": "",
              "first_text_ms": None, "reasoning_elapsed_ms": None, "reasoning_tokens": None}
    mode = provider.get("thinking_mode", "enabled" if provider.get("enable_thinking") else "default")
    mode_label = {"default": "跟随模型默认", "enabled": "开启", "disabled": "关闭"}.get(mode, "无效")
    result["thinking_mode"] = mode
    first_reasoning = None
    try:
        async with asyncio.timeout(120):
            model = provider_model(config, provider)
            # 直接读取 SDK 流，避免兼容模型包装器丢弃 reasoning_content 扩展字段。
            request = dict(model=provider["model_name"], messages=[{"role": "user", "content": content}],
                           max_tokens=4096, stream=True, stream_options={"include_usage": True})
            if model.extra_body:
                request["extra_body"] = model.extra_body
            reply = ""
            finish_reason = None
            stream = await model.root_async_client.chat.completions.create(**request)
            async with stream:
                async for chunk in stream:
                    now = time.perf_counter()
                    if chunk.usage:
                        details = chunk.usage.completion_tokens_details
                        if details is not None:
                            result["reasoning_tokens"] = details.reasoning_tokens
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    text = delta.content or ""
                    reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None) or ""
                    if not isinstance(reasoning, str):
                        reasoning = ""
                    if reasoning:
                        if first_reasoning is None:
                            first_reasoning = now
                        result["reasoning"] += reasoning
                    if text and result["first_text_ms"] is None:
                        result["first_text_ms"] = round((now - started) * 1000)
                        # 观测间隔不含首个思考片段之前的网络和服务端等待。
                        if first_reasoning is not None:
                            result["reasoning_elapsed_ms"] = round((now - first_reasoning) * 1000)
                    reply += text
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
            # 防止异常服务将请求中的密钥原样反射到后台页面。
            reply = reply.replace(provider["api_key"], "[已隐藏密钥]").strip()
            if reply:
                message = "调用成功（输出达到 token 上限，回复可能不完整）" if finish_reason == "length" else "调用成功"
            elif finish_reason == "length":
                message = "模型已达到测试输出上限（4096 token），但未生成文本回复；思考过程可能已耗尽额度"
            elif finish_reason == "content_filter":
                message = "模型回复被供应商内容过滤，未返回文本"
            elif result["reasoning"] or result["reasoning_tokens"]:
                message = "模型仅返回思考内容，没有正式文本回复；请检查模型的思考模式和输出额度"
            else:
                message = "模型请求已完成，但未返回文本；请检查模型是否支持 Chat Completions 及响应格式"
            result.update(success=bool(reply), reply=reply[:1000], message=message)
    except (TimeoutError, APITimeoutError):
        result["message"] = "请求超时，请检查网络或模型服务状态"
    except APIConnectionError:
        result["message"] = "无法连接模型服务，请检查服务地址和网络"
    except APIStatusError as exc:
        messages = {400: "请求参数不被支持，请检查思考模式、流式能力和模型配置", 401: "认证失败，请检查 API Key", 403: "访问被拒绝，请检查账号权限",
                    402: "余额或付费状态异常", 404: "接口或模型不存在，请检查地址和模型名称",
                    429: "请求限流或额度不足"}
        result["message"] = messages.get(exc.status_code, "模型服务返回错误")
        result["http_status"] = exc.status_code
    except ValueError as exc:
        result["message"] = str(exc).replace(provider["api_key"], "[已隐藏密钥]")[:500]
    except Exception as exc:
        logger.warning("供应商连接检查失败：%s", type(exc).__name__)
        result["message"] = "调用失败，请检查模型配置或响应格式"
    finally:
        result["message"] = f"思考模式：{mode_label}；{result['message']}"
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        # 输出到页面前统一脱敏；超时也可展示已收到的诊断信息。
        result["reasoning"] = result["reasoning"].replace(provider["api_key"], "[已隐藏密钥]")[:4000]
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
