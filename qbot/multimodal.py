"""QQ 附件预处理：有界下载、隔离解码、专用 ASR；不保留媒体原文。"""
import asyncio
import base64
import ipaddress
import json
import re
import socket
import subprocess
import sys
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field

MEDIA_SLOTS = asyncio.Semaphore(2)
MAX_FILE = 8_000_000
QQ_MEDIA_HOSTS = {"qqbot.ugcimg.cn", "multimedia.nt.qq.com.cn"}


def media_origin(url: str) -> dict[str, str]:
    """只留主机和协议用于诊断，绝不保存媒体路径及签名参数。"""
    try:
        parts = urlsplit("https:" + url if url.startswith("//") else url)
        host = (parts.hostname or "").lower()
        return {"source_host": host if re.fullmatch(r"[a-z0-9.-]{1,253}", host) else "未知",
                "source_scheme": parts.scheme if parts.scheme in ("https", "http") else "未知"}
    except ValueError:
        return {"source_host": "无法解析", "source_scheme": "未知"}


class Attachment(BaseModel):
    url: str = Field(default="", max_length=4096)
    content_type: str = Field(default="", max_length=128)
    filename: str = Field(default="", max_length=300)
    size: int | None = Field(default=None, ge=0)


def normalize_faces(text: str) -> str:
    # ext 中的平台表情名称仍是用户数据，不能提升为系统指令。
    def replace(match: re.Match) -> str:
        tag = match.group(0)
        extra = re.search(r'ext="([A-Za-z0-9+/=]{1,1024})"', tag)
        if extra:
            try:
                name = json.loads(base64.b64decode(extra[1], validate=True)).get("text")
                if isinstance(name, str) and name.strip():
                    return "[QQ表情名称：" + name[:100] + "]"
            except (ValueError, TypeError, AttributeError):
                pass
        identity = re.search(r'faceId="?(\d{1,10})', tag)
        return "[QQ内置表情 ID" + (identity[1] if identity else "未知") + "]"
    return re.sub(r"<faceType=[^<>]{1,1500}>", replace, text)


async def download_attachment(url: str) -> bytes:
    """仅允许 QQ CDN，固定公网 IP 与 TLS 主机名，防止 DNS 重绑定。"""
    if url.startswith("//"):
        url = "https:" + url
    for _ in range(3):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if (parts.scheme not in ("https", "http") or parts.username or parts.password
                or parts.port not in ((None, 80, 443) if parts.scheme == "http" else (None, 443)) or parts.fragment
                or not (host in QQ_MEDIA_HOSTS
                    or any(host == d or host.endswith("." + d) for d in ("qpic.cn", "qq.com", "gtimg.cn")))):
            raise ValueError("附件地址不属于允许的 QQ HTTPS 媒体来源")
        # QQ 事件中的 HTTP CDN 地址仅升级 HTTPS，绝不携带机器人凭证。
        addresses = await asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
        ips = [entry[4][0] for entry in addresses]
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError("附件地址解析到非公网地址，已拒绝")
        ip = ips[0]
        authority = f"[{ip}]" if ":" in ip else ip
        pinned = urlunsplit(("https", authority, parts.path, parts.query, ""))
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10) as client:
            async with client.stream("GET", pinned, headers={"Host": host},
                                     extensions={"sni_hostname": host}) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("附件重定向缺少目标")
                    url = urljoin(urlunsplit(("https", host, parts.path, parts.query, "")), location)
                    continue
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_FILE:
                        raise ValueError("附件超过 8 MB")
                    body.extend(chunk)
                if not body:
                    raise ValueError("附件为空")
                return bytes(body)
    raise ValueError("附件重定向次数过多")


async def decode_media(data: bytes, mode: str) -> bytes:
    def run() -> bytes:
        try:
            result = subprocess.run([sys.executable, "-m", "qbot.media_worker", mode], input=data,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10 if mode == "audio" else 5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except subprocess.TimeoutExpired:
            raise ValueError("附件解码超时") from None
        if result.returncode:
            raise ValueError("附件解码失败或超过限制（语音最多60秒，图片最多2000万像素）")
        return result.stdout
    return await asyncio.to_thread(run)


async def transcribe(provider: dict, wav: bytes, details: dict | None = None) -> tuple[str, dict]:
    """Qwen-Audio-3.x ASR 使用百炼原生 HTTP，不能调用聊天兼容端点。"""
    from qbot.asr_filetrans import FILETRANS_MODELS, transcribe_file
    if provider["model_name"] in FILETRANS_MODELS:
        return await transcribe_file(provider, wav, details)
    if "filetrans" in provider["model_name"] or not provider["model_name"].startswith(("qwen-audio-3.1-asr-flash", "qwen-audio-3.0-asr-flash", "fun-asr-flash")):
        raise ValueError("ASR 请配置 qwen-audio-3.1-asr-flash 或兼容的 Qwen-Audio-3.x/Fun-ASR-Flash")
    parsed = urlsplit(provider["base_url"])
    path = parsed.path.rstrip("/")
    if path in ("", "/api/v1", "/compatible-mode/v1"):
        path = "/api/v1/services/aigc/multimodal-generation/generation"
    url = urlunsplit(("https", parsed.netloc, path, "", ""))
    payload = {"model": provider["model_name"], "input": {"messages": [{"role": "user", "content": [
        {"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + base64.b64encode(wav).decode()}}
    ]}]}, "parameters": {"format": "wav", "sample_rate": "24000"}}
    async with asyncio.timeout(25), httpx.AsyncClient(timeout=25) as client:
        async with client.stream("POST", url, headers={"Authorization": "Bearer " + provider["api_key"]}, json=payload) as response:
            if response.is_error:
                raise ValueError(f"语音识别服务返回 HTTP {response.status_code}，请检查配置和额度")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 1_000_000:
                    raise ValueError("语音识别响应过大")
                body.extend(chunk)
    result = json.loads(body)
    output = result.get("output") or {}
    text = output.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("语音识别未返回有效文字")
    raw_usage = result.get("usage") or {}
    usage = {k: v for k, v in raw_usage.items() if k in
             ("input_tokens", "output_tokens", "total_tokens", "duration") and type(v) in (int, float)} if isinstance(raw_usage, dict) else {}
    return text.replace(provider["api_key"], "[已隐藏密钥]").strip()[:4000], usage


async def prepare_message(text: str, attachments: list[Attachment], config: dict, store, bot_id: int) -> dict:
    result = {"text": normalize_faces(text).strip(), "blocks": [], "voice_input": False,
              "details": {"attachments": [], "warnings": []}}
    total, image_count, audio_count = 0, 0, 0
    downloaded_seconds = 0.0
    async with MEDIA_SLOTS:
        for index, attachment in enumerate(attachments[:4]):
            entry = {"index": index + 1, "status": "failed", **media_origin(attachment.url)}
            result["details"]["attachments"].append(entry)
            started = time.monotonic()
            try:
                hint = attachment.content_type.lower()
                is_image = hint.startswith("image/")
                is_audio = hint.startswith("audio/") or hint in ("voice", "silk")
                if not is_image and not is_audio:
                    raise ValueError("暂不支持此附件类型")
                entry["kind"] = "image" if is_image else "audio"
                if is_image:
                    image_count += 1
                    if not config["images"] or image_count > 3:
                        raise ValueError("图片理解未开启或超过每条3张限制")
                else:
                    audio_count += 1
                    if not config["asr"] or audio_count > 1:
                        raise ValueError("语音识别未开启或超过每条1段限制")
                if attachment.size and attachment.size > MAX_FILE:
                    raise ValueError("附件超过8 MB")
                download_start = time.monotonic()
                try:
                    async with asyncio.timeout(max(0.01, min(10, 20 - downloaded_seconds))):
                        data = await download_attachment(attachment.url)
                finally:
                    downloaded_seconds += time.monotonic() - download_start
                total += len(data)
                if total > 16_000_000:
                    raise ValueError("每条消息附件总大小超过16 MB")
                entry["size"] = len(data)
                if is_image:
                    decoded = json.loads(await decode_media(data, "gif" if config["gifs"] else "image"))
                    frames = decoded["frames"]
                    if sum(b["type"] == "image_url" for b in result["blocks"]) + len(frames) > 8:
                        raise ValueError("图片与动画总帧数超过8张")
                    entry.update(frames=len(frames), notice=decoded["notice"])
                    if "仅识别首帧" in decoded["notice"]:
                        result["details"]["warnings"].append(f"附件{index + 1}：{decoded['notice']}")
                    result["blocks"].append({"type": "text", "text": f"附件{index + 1}：{decoded['notice']}"})
                    for frame in frames:
                        result["blocks"].extend([{"type": "text", "text": f"相对时间 {frame['time_ms']}ms"},
                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + frame["data"]}}])
                else:
                    wav = await decode_media(data, "audio")
                    provider = await store.call("capability_provider", config["asr_provider_id"], "asr")
                    entry.update(model=provider["model_name"], provider=provider["name"])
                    transcript, usage = await transcribe(provider, wav, entry)
                    entry.update(transcript=transcript, model=provider["model_name"], provider=provider["name"], usage=usage)
                    result["text"] += "\n[语音转写]\n" + transcript
                    result["voice_input"] = True
                entry["status"] = "success"
            except Exception as exc:
                # 第三方异常可能包含签名 URL，错误只展示本模块提供的固定说明。
                from qbot.asr_filetrans import ASRError
                error = str(exc) if type(exc) is ValueError or isinstance(exc, ASRError) else "附件处理失败，请检查格式、网络及模型配置"
                entry["error"] = error
                result["details"]["warnings"].append(f"附件{index + 1}：{error}")
            finally:
                entry["latency_ms"] = round((time.monotonic() - started) * 1000)
    if len(attachments) > 4:
        result["details"]["warnings"].append("仅处理前4个附件，其余未识别")
    result["usable"] = bool(result["text"] or result["blocks"])
    if result["blocks"] and not result["text"]:
        result["text"] = "请结合图片或表情包和聊天上下文自然回应；不确定的内容不要猜测。"
    if result["details"]["warnings"] and result["usable"]:
        result["text"] += "\n[部分附件未识别，请在回复中告知用户]\n" + "\n".join(result["details"]["warnings"])
    return result
