"""QQ 与后台试用共用的兼容 Images API，有界读取且不自动重试。"""
import asyncio
import base64
import json

import httpx

from qbot.media import MAX_MEDIA_BYTES, MediaUpload, validate_media


async def generate_image(provider: dict, prompt: str, *, timeout_seconds: float = 65) -> MediaUpload:
    if provider.get("model_type") != "image":
        raise ValueError("请选择图片生成模型")
    prompt = prompt.strip()
    if not prompt or len(prompt) > 2000:
        raise ValueError("提示词需为1～2000字")
    if provider["api_type"] != "openai_compatible":
        raise ValueError("生图模型需使用 OpenAI 兼容 Images 接口。")
    # 有界读取，拒绝 URL 输出，避免把供应商响应变成任意网络下载入口。
    async with asyncio.timeout(timeout_seconds), httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10), follow_redirects=False) as client:
        payload = {"model": provider["model_name"], "prompt": prompt, "n": 1}
        # GPT Image 固定返回 Base64，不接受旧版 response_format 参数。
        if not provider["model_name"].lower().startswith("gpt-image"):
            payload["response_format"] = "b64_json"
        async with client.stream("POST", provider["base_url"].rstrip("/") + "/images/generations",
                headers={"Authorization": "Bearer " + provider["api_key"]},
                json=payload) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 5_000_000:
                    raise ValueError("生图响应超出大小限制")
                body.extend(chunk)
        result = json.loads(body)
        encoded = result.get("data", [{}])[0].get("b64_json")
        if not isinstance(encoded, str):
            raise ValueError("生图接口未返回 Base64 图片；当前不支持仅返回图片 URL 的接口。")
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_MEDIA_BYTES:
            raise ValueError("生成的图片超过大小限制（3.5 MB）。")
        extension = "png" if raw.startswith(b"\x89PNG") else "jpg"
        return validate_media("image", "generated." + extension, raw)
