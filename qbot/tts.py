"""Qwen Realtime TTS：收集 PCM 并封装 WAV，供后台试听和媒体发送复用。"""
import asyncio
import base64
import io
import json
import logging
import wave
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from websockets.asyncio.client import connect

from qbot.media import MAX_MEDIA_BYTES

logger = logging.getLogger(__name__)


class SpeechError(ValueError):
    """可安全展示给管理员的合成错误。"""


async def synthesize(provider: dict, text: str, voice: str) -> bytes:
    if provider.get("model_type") != "tts" or not provider["model_name"].startswith("qwen3-tts-flash-realtime"):
        raise ValueError("当前语音合成支持 qwen3-tts-flash-realtime 及其快照模型，请检查模型类型和名称")
    url = urlsplit(provider["base_url"])
    path = url.path.rstrip("/")
    if path in ("", "/compatible-mode/v1"):
        path = "/api-ws/v1/realtime"
    endpoint = urlunsplit(("wss", url.netloc, path, urlencode({"model": provider["model_name"]}), ""))
    pcm = bytearray()
    try:
        # 每次点击只建立一次连接，不自动重试付费合成；同时限制时长和音频体积。
        async with asyncio.timeout(60):
            async with connect(endpoint, additional_headers={"Authorization": "Bearer " + provider["api_key"]},
                               open_timeout=10, close_timeout=2, max_size=MAX_MEDIA_BYTES) as ws:
                async def send(kind: str, **values) -> None:
                    await ws.send(json.dumps({"event_id": uuid4().hex, "type": kind, **values}))

                await send("session.update", session={"mode": "commit", "voice": voice,
                           "response_format": "pcm", "sample_rate": 24000})
                configured = False
                finished = False
                async for raw in ws:
                    event = json.loads(raw)
                    kind = event.get("type")
                    if kind == "error":
                        raise SpeechError("语音服务拒绝合成，请检查密钥、模型权限、音色和额度")
                    if kind == "session.updated" and not configured:
                        configured = True
                        await send("input_text_buffer.append", text=text)
                        await send("input_text_buffer.commit")
                    elif kind == "response.audio.delta":
                        chunk = base64.b64decode(event["delta"], validate=True)
                        if len(pcm) + len(chunk) > MAX_MEDIA_BYTES - 44:
                            raise SpeechError("生成音频超过 3.5 MB，请缩短文本")
                        pcm.extend(chunk)
                    elif kind == "response.audio.done":
                        finished = True
                        await send("session.finish")
                    elif kind == "session.finished":
                        break
                if not finished or not pcm or len(pcm) % 2:
                    raise SpeechError("语音服务未返回完整音频，请重试")
    except SpeechError:
        raise
    except TimeoutError:
        raise ValueError("语音合成超时，请缩短文本或检查服务状态") from None
    except Exception as exc:
        logger.warning("语音合成失败：%s", type(exc).__name__)
        raise ValueError("语音合成失败，请检查服务地址、密钥、网络及模型配置") from None
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(pcm)
    return output.getvalue()
