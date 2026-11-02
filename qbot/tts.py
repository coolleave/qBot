"""Qwen Realtime TTS：收集 PCM 并封装 WAV，供后台试听和媒体发送复用。"""
import asyncio
import base64
import io
import json
import logging
import struct
import wave
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from websockets.asyncio.client import connect

from qbot.media import MAX_MEDIA_BYTES

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
LEADING_SILENCE_MS = 200
TRAILING_SILENCE_MS = 350
EDGE_FADE_MS = 5
WAV_HEADER_BYTES = 44
PADDING_BYTES = SAMPLE_RATE * (LEADING_SILENCE_MS + TRAILING_SILENCE_MS) // 1000 * 2
MAX_RAW_PCM_BYTES = MAX_MEDIA_BYTES - WAV_HEADER_BYTES - PADDING_BYTES


class SpeechError(ValueError):
    """可安全展示给管理员的合成错误。"""


def smooth_pcm_edges(pcm: bytes | bytearray) -> bytes:
    """完整保留语音，仅平滑最边缘样本并加播放缓冲，不能恢复上游漏字。"""
    if not pcm or len(pcm) % 2 or len(pcm) > MAX_RAW_PCM_BYTES:
        raise SpeechError("音频无效或超过大小限制，请缩短文本")
    result = bytearray(pcm)
    frames = len(result) // 2
    fade = min(SAMPLE_RATE * EDGE_FADE_MS // 1000, frames // 2)
    for index in range(fade):
        gain = index / max(1, fade - 1)
        for position in (index, frames - 1 - index):
            sample = struct.unpack_from("<h", result, position * 2)[0]
            struct.pack_into("<h", result, position * 2, round(sample * gain))
    leading = bytes(SAMPLE_RATE * LEADING_SILENCE_MS // 1000 * 2)
    trailing = bytes(SAMPLE_RATE * TRAILING_SILENCE_MS // 1000 * 2)
    return leading + result + trailing


async def synthesize(provider: dict, text: str, voice: str) -> bytes:
    from qbot.voice_cloning import CLONE_MODELS
    if provider.get("model_type") == "tts" and provider["model_name"] in CLONE_MODELS:
        return await synthesize_audio_tts(provider, text, voice)
    if provider.get("model_type") != "tts" or not provider["model_name"].startswith("qwen3-tts-flash-realtime"):
        raise ValueError("当前语音合成支持 qwen3-tts-flash-realtime 及快照、qwen-audio-3.1-tts-flash/plus，请检查模型类型和名称")
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
                           "response_format": "pcm", "sample_rate": SAMPLE_RATE})
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
                        if len(pcm) + len(chunk) > MAX_RAW_PCM_BYTES:
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
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(smooth_pcm_edges(pcm))
    return output.getvalue()


async def synthesize_audio_tts(provider: dict, text: str, voice: str) -> bytes:
    """Qwen-Audio-TTS 任务协议；收齐PCM后复用原有WAV与首尾缓冲。"""
    from qbot.voice_cloning import audio_tts_endpoint
    if provider.get("api_type") != "openai_compatible":
        raise ValueError("请选择千问原生语音接口")
    endpoint = audio_tts_endpoint(provider, websocket=True)
    pcm = bytearray()
    task_id = str(uuid4())
    finished = False
    started = False
    try:
        async with asyncio.timeout(60):
            async with connect(endpoint, additional_headers={"Authorization": "Bearer " + provider["api_key"]},
                               open_timeout=10, close_timeout=2, max_size=MAX_MEDIA_BYTES) as ws:
                async def send(action: str, payload: dict) -> None:
                    await ws.send(json.dumps({"header": {"action": action, "task_id": task_id,
                                                          "streaming": "duplex"}, "payload": payload}))
                await send("run-task", {"task_group": "audio", "task": "tts", "function": "SpeechSynthesizer",
                    "model": provider["model_name"], "parameters": {"text_type": "PlainText", "voice": voice,
                    "format": "pcm", "sample_rate": SAMPLE_RATE}, "input": {}})
                async for raw in ws:
                    if isinstance(raw, bytes):
                        if not started or len(pcm) + len(raw) > MAX_RAW_PCM_BYTES:
                            raise SpeechError("音频状态无效或超过3.5 MB，请缩短文本")
                        pcm.extend(raw)
                        continue
                    event = json.loads(raw)
                    header = event.get("header", {})
                    if header.get("task_id") != task_id:
                        raise SpeechError("语音服务返回不匹配的任务")
                    kind = header.get("event")
                    if kind == "task-failed":
                        raise SpeechError("语音服务拒绝合成，请检查模型权限、音色就绪状态和额度")
                    if kind == "task-started" and not started:
                        started = True
                        await send("continue-task", {"input": {"text": text}})
                        await send("finish-task", {"input": {}})
                    elif kind == "task-finished":
                        finished = True
                        break
                if not finished or not pcm or len(pcm) % 2:
                    raise SpeechError("语音服务未返回完整音频")
    except SpeechError:
        raise
    except TimeoutError:
        raise SpeechError("语音合成超时，请缩短文本或检查服务状态") from None
    except Exception as exc:
        logger.warning("Qwen-Audio-TTS合成失败 error=%s", type(exc).__name__)
        raise SpeechError("语音合成失败，请检查服务地址、密钥和模型配置") from None
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(smooth_pcm_edges(pcm))
    return output.getvalue()
