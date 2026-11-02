"""百炼音色复刻：固定能力边界、有界请求、不重发创建操作。"""
import asyncio
import base64
import io
import json
import logging
import re
import wave
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)
CLONE_MODELS = frozenset({"qwen-audio-3.1-tts-flash", "qwen-audio-3.1-tts-plus"})
MAX_CLONE_BYTES = 8_000_000
VOICE_ID_PATTERN = r"[A-Za-z0-9_. -]{1,100}"


def audio_tts_endpoint(provider: dict, websocket: bool = False) -> str:
    """新协议使用独立 inference 路径，不能套用旧版 realtime 事件。"""
    url = urlsplit(provider["base_url"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.port not in (None, 443)):
        raise ValueError("千问语音服务地址必须使用无凭证的 HTTPS 默认端口")
    if url.path.rstrip("/") not in ("", "/api/v1", "/compatible-mode/v1", "/api-ws/v1/inference"):
        raise ValueError("新千问 TTS 请填写服务基础域名，路径留空或使用 /api/v1")
    path = "/api-ws/v1/inference" if websocket else "/api/v1/services/audio/tts/customization"
    return urlunsplit(("wss" if websocket else "https", url.netloc, path, "", ""))


def validate_clone_provider(provider: dict) -> None:
    if (provider.get("model_type") != "tts" or provider.get("api_type") != "openai_compatible"
            or provider.get("model_name") not in CLONE_MODELS):
        raise ValueError("音色复刻仅支持千问原生接口的 qwen-audio-3.1-tts-flash/plus")
    endpoint = audio_tts_endpoint(provider)
    host = urlsplit(endpoint).hostname
    if host not in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"} and not re.fullmatch(
            r"[a-zA-Z0-9-]+[.](cn-beijing|ap-southeast-1)[.]maas[.]aliyuncs[.]com", host or ""):
        raise ValueError("音色复刻请使用百炼北京或新加坡服务域名")


async def prepare_clone_audio(data: bytes) -> bytes:
    from qbot.multimodal import decode_media
    if not data or len(data) > MAX_CLONE_BYTES:
        raise ValueError("录音不能为空或超过8 MB")
    # 只接收当前隔离解码器已支持的 WAV/MP3，不把文件扩展名当作格式证据。
    if not (data.startswith(b"RIFF") and data[8:12] == b"WAVE"
            or data.startswith(b"ID3") or len(data) >= 2 and data[0] == 255 and data[1] & 224 == 224):
        raise ValueError("请上传 WAV 或 MP3 录音")
    wav = await decode_media(data, "audio")
    with wave.open(io.BytesIO(wav), "rb") as audio:
        seconds = audio.getnframes() / audio.getframerate()
        if not 5 <= seconds <= 60:
            raise ValueError("录音需为5～60秒，建议10～20秒清晰单人说话")
    return wav


async def enrollment_request(provider: dict, values: dict) -> dict:
    validate_clone_provider(provider)
    if not provider.get("supports_voice_cloning"):
        raise ValueError("请先在 TTS 模型配置中开启支持音色复刻")
    try:
        async with asyncio.timeout(35), httpx.AsyncClient(timeout=30, trust_env=False,
                                                         follow_redirects=False) as client:
            async with client.stream("POST", audio_tts_endpoint(provider),
                    headers={"Authorization": "Bearer " + provider["api_key"]},
                    json={"model": "voice-enrollment", "input": values}) as response:
                if response.status_code != 200:
                    raise ValueError(f"音色服务请求失败（HTTP {response.status_code}），请检查权限、地域和录音")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 65536:
                        raise ValueError("音色服务响应过大")
                    body.extend(chunk)
                result = json.loads(body)
                if not isinstance(result, dict) or not isinstance(result.get("output"), dict):
                    raise ValueError("音色服务未返回有效结果")
                return result["output"]
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("音色服务请求失败 error=%s", type(exc).__name__)
        raise ValueError("音色服务超时或连接失败；创建结果可能未知，请勿重复创建") from None


async def create_voice(provider: dict, wav: bytes, prefix: str) -> str:
    result = await enrollment_request(provider, {"action": "create_voice", "target_model": provider["model_name"],
        "prefix": prefix, "audio": "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii")})
    voice_id = result.get("voice_id")
    if not isinstance(voice_id, str) or not re.fullmatch(VOICE_ID_PATTERN, voice_id):
        raise ValueError("音色服务未返回有效音色ID，创建结果未知，请在百炼核对")
    return voice_id


async def query_voice(provider: dict, voice_id: str) -> str:
    result = await enrollment_request(provider, {"action": "query_voice", "voice_id": voice_id})
    status = {"OK": "ready", "DEPLOYING": "deploying", "UNDEPLOYED": "failed"}.get(result.get("status"))
    if status is None:
        raise ValueError("音色服务返回未知状态，请稍后重新查询")
    return status
