"""自动语音回复：仅在消息发送前失败时回退文字，送达未知不补发。"""
import asyncio
import re
import time

from qbot.audio_codec import to_private_audio
from qbot.media import validate_media
from qbot.multimodal import MEDIA_SLOTS
from qbot.tts import synthesize


async def voice_file(text: str, config: dict, store, source: str, qq, target: str, details: dict) -> str:
    started = time.monotonic()
    stage = "tts"
    try:
        async with MEDIA_SLOTS:
            provider = await store.call("capability_provider", config["tts_provider_id"], "tts")
            details["tts_model"] = provider["model_name"]
            details["tts_provider"] = provider["name"]
            speech = re.sub(r"[*_#~]", "", text).strip()
            if not speech:
                raise ValueError("没有可朗读内容")
            details["tts_text"] = speech
            async with asyncio.timeout(30):
                wav = await synthesize(provider, speech, config["voice"])
            details["tts_status"] = "success"
            media = validate_media("audio", "reply.wav", wav)
            stage = "convert"
            if source == "qq_c2c":
                media = await to_private_audio(media)
            stage = "upload"
            async with asyncio.timeout(20):
                info = await qq.upload_media(source, target, media)
            details["upload_status"] = "success"
            return info
    except Exception:
        details["fallback_reason"] = {"tts": "语音合成失败", "convert": "语音转码失败", "upload": "语音上传失败"}[stage]
        raise
    finally:
        details["voice_latency_ms"] = round((time.monotonic() - started) * 1000)


def voice_requested(config: dict, source: str, voice_input: bool) -> bool:
    """生成前可确定的输出偏好；实际发送仍需检查内容与剩余时间。"""
    mode = config["private_reply" if source == "qq_c2c" else "group_reply"]
    return mode == "voice" or (mode == "follow" and voice_input)


def wants_voice(text: str, config: dict, source: str, voice_input: bool) -> bool:
    return voice_requested(config, source, voice_input) and (
        0 < len(text) <= 300 and not re.search(r"```|https?://|\|", text))
