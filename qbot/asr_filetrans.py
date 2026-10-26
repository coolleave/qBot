"""百炼异步文件转写：临时上传、单次提交、有界轮询；适用于本地开发试用。"""
import asyncio
import json
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx


class ASRError(ValueError):
    """仅包含可以安全展示的错误，不回传签名地址、密钥和上游响应。"""


class NoSpeechError(ASRError):
    """音频为空白或服务成功但没有转写正文，区别于服务故障。"""


FILETRANS_MODELS = {"qwen-audio-3.0-asr-flash-filetrans", "qwen-audio-3.1-asr-flash-filetrans"}


def api_root(base_url: str) -> str:
    url = urlsplit(base_url)
    allowed = ("", "/api/v1", "/compatible-mode/v1", "/api/v1/services/audio/asr/transcription")
    if url.scheme != "https" or url.path.rstrip("/") not in allowed:
        raise ASRError("Filetrans 服务地址请填写百炼 HTTPS 域名或 /api/v1 根地址")
    return urlunsplit((url.scheme, url.netloc, "/api/v1", "", ""))


def oss_url(value: str) -> str:
    """上传和结果地址来自供应商，仍只允许阿里云公网 OSS，不携带 API Key。"""
    url = urlsplit(value)
    if (url.scheme != "https" or url.username or url.password or url.port not in (None, 443)
            or url.fragment or not re.fullmatch(r"[a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com", url.hostname or "")
            or "-internal." in (url.hostname or "")):
        raise ASRError("转写服务返回了不支持的 OSS 地址")
    return value


async def request_json(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> dict:
    async with client.stream(method, url, **kwargs) as response:
        if not response.is_success:
            raise ASRError(f"文件转写服务返回 HTTP {response.status_code}，请检查地址、密钥、模型权限及额度")
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > 1_000_000:
                raise ASRError("文件转写响应超过大小限制")
            data.extend(chunk)
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ASRError("文件转写响应格式不正确")
    return result


async def transcribe_file(provider: dict, wav: bytes, details: dict | None = None) -> tuple[str, dict]:
    details = details if details is not None else {}
    details.update(asr_protocol="dashscope_filetrans", asr_stage="upload")
    root = api_root(provider["base_url"])
    headers = {"Authorization": "Bearer " + provider["api_key"], "Content-Type": "application/json"}
    try:
        # 不自动重试提交，避免请求超时后重复创建计费任务。所有等待受总预算约束。
        async with asyncio.timeout(55), httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
            policy = (await request_json(client, "GET", root + "/uploads", headers=headers,
                params={"action": "getPolicy", "model": provider["model_name"]}))["data"]
            upload_host = oss_url(policy["upload_host"])
            object_key = policy["upload_dir"].rstrip("/") + "/" + uuid4().hex + ".wav"
            form = {"OSSAccessKeyId": policy["oss_access_key_id"], "policy": policy["policy"],
                "Signature": policy["signature"], "key": object_key,
                "x-oss-object-acl": "private", "x-oss-forbid-overwrite": "true", "success_action_status": "200"}
            async with client.stream("POST", upload_host, data=form,
                    files={"file": ("audio.wav", wav, "audio/wav")}, timeout=15) as uploaded:
                if uploaded.status_code != 200:
                    raise ASRError(f"音频临时上传失败（HTTP {uploaded.status_code}）")
            details["asr_stage"] = "submit"
            submitted = await request_json(client, "POST", root + "/services/audio/asr/transcription",
                headers={**headers, "X-DashScope-Async": "enable", "X-DashScope-OssResourceResolve": "enable"},
                json={"model": provider["model_name"], "input": {"file_urls": ["oss://" + object_key]},
                      "parameters": {"channel_id": [0]}})
            task_id = submitted.get("output", {}).get("task_id")
            if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
                raise ASRError("文件转写未返回有效任务 ID，未自动重试")
            details.update(asr_task_id=task_id, asr_stage="poll", asr_task_status="PENDING")
            for _ in range(20):
                await asyncio.sleep(2)
                result = await request_json(client, "GET", root + "/tasks/" + task_id, headers=headers)
                output = result.get("output", {})
                status = output.get("task_status")
                details["asr_task_status"] = status if status in ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN") else "UNKNOWN"
                if status in ("PENDING", "RUNNING"):
                    continue
                if status != "SUCCEEDED":
                    raise ASRError("文件转写任务失败或已取消，请检查音频、模型及账号配置")
                results = output.get("results", [])
                if len(results) != 1 or results[0].get("subtask_status") != "SUCCEEDED":
                    raise ASRError("音频转写子任务失败，请检查文件格式和临时上传支持情况")
                details["asr_stage"] = "download_result"
                transcription = await request_json(client, "GET", oss_url(results[0]["transcription_url"]))
                paragraphs = transcription.get("transcripts", [])
                text = "\n".join(item["text"] for item in paragraphs
                    if isinstance(item, dict) and isinstance(item.get("text"), str)).strip()
                if not text:
                    raise NoSpeechError("音频转写未识别出有效文字")
                usage = result.get("usage") or {}
                duration = usage.get("duration") if isinstance(usage, dict) else None
                details["asr_stage"] = "complete"
                return text.replace(provider["api_key"], "[已隐藏密钥]")[:4000], (
                    {"duration": duration} if type(duration) in (int, float) else {})
            raise ASRError("文件转写等待超时，上游任务可能仍在运行；未重新提交任务")
    except ASRError:
        raise
    except TimeoutError:
        raise ASRError("文件转写超时，上游可能已接收任务；未自动重试") from None
    except Exception:
        raise ASRError("文件转写请求失败，请检查网络、百炼地址、密钥和模型权限；未自动重试") from None
