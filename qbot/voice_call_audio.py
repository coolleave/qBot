"""通话使用流式 PCM，保留原有完整 WAV 合成接口。"""
import asyncio
import base64
import json
from collections.abc import AsyncIterator
from uuid import uuid4

from websockets.asyncio.client import connect

from qbot.voice_call_config import realtime_endpoint, validate_voice_provider


async def send_event(ws, kind: str, **values) -> None:
    async with asyncio.timeout(5):
        await ws.send(json.dumps({"event_id": uuid4().hex, "type": kind, **values}))


async def stream_speech(provider: dict, text: str, voice: str) -> AsyncIterator[bytes]:
    """每个短句至多一次付费合成；取消迭代会关闭上游 WebSocket。"""
    validate_voice_provider(provider, "tts")
    async with asyncio.timeout(45):
        async with connect(realtime_endpoint(provider), additional_headers={"Authorization": "Bearer " + provider["api_key"]},
                           open_timeout=10, close_timeout=2, max_size=2_000_000, max_queue=4) as ws:
            await send_event(ws, "session.update", session={"mode": "commit", "voice": voice,
                "response_format": "pcm", "sample_rate": 24000})
            configured = False
            total = 0
            async for raw in ws:
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "error":
                    raise ValueError("语音合成服务拒绝请求，请检查模型、音色、密钥及额度")
                if kind == "session.updated" and not configured:
                    configured = True
                    await send_event(ws, "input_text_buffer.append", text=text)
                    await send_event(ws, "input_text_buffer.commit")
                elif kind == "response.audio.delta":
                    chunk = base64.b64decode(event["delta"], validate=True)
                    total += len(chunk)
                    if not chunk or len(chunk) % 2 or total > 2_880_000:
                        raise ValueError("通话合成音频格式或长度异常")
                    # 控制每条浏览器消息的大小，不给每个块插入静音。
                    for start in range(0, len(chunk), 9600):
                        yield chunk[start:start + 9600]
                elif kind == "response.audio.done":
                    if total == 0:
                        raise ValueError("语音合成未返回音频")
                    await send_event(ws, "session.finish")
                    return
            raise ValueError("语音合成连接提前结束")
