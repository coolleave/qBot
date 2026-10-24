import asyncio
import time
from urllib.parse import quote

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from qbot.config import Settings


def signing_key(secret: str) -> Ed25519PrivateKey:
    seed = secret.encode("utf-8")
    if not seed:
        raise ValueError("QQ_APP_SECRET 不能为空")
    seed = (seed * ((32 + len(seed) - 1) // len(seed)))[:32]
    return Ed25519PrivateKey.from_private_bytes(seed)


def verify_signature(secret: str, timestamp: str, signature: str, body: bytes) -> bool:
    try:
        # 同时限制重放窗口；机器时钟需保持同步。
        if abs(time.time() - int(timestamp)) > 300:
            return False
        signing_key(secret).public_key().verify(
            bytes.fromhex(signature), timestamp.encode("utf-8") + body,
        )
        return True
    except (ValueError, InvalidSignature):
        return False


class QQAPIError(RuntimeError):
    """仅携带安全诊断信息，不包含凭证、请求正文或完整响应。"""


class QQDeliveryUnknown(QQAPIError):
    """发送请求发生网络异常，不能判断是否已经送达。"""


def response_data(response: httpx.Response) -> dict:
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except ValueError:
        raise QQAPIError("QQ 返回了无效 JSON 对象") from None


class QQClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
        self.token = ""
        self.expires_at = 0.0
        self.token_lock = asyncio.Lock()

    async def access_token(self) -> str:
        async with self.token_lock:
            if self.token and time.monotonic() < self.expires_at:
                return self.token
            # 凭证请求允许一次短暂重试；业务错误不重试。
            for attempt in range(2):
                try:
                    response = await self.client.post(
                        self.settings.qq_token_url,
                        json={"appId": self.settings.qq_app_id,
                              "clientSecret": self.settings.qq_app_secret.get_secret_value()},
                        timeout=self.settings.qq_timeout_seconds,
                    )
                    if response.status_code >= 500 and attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    if response.is_error:
                        raise QQAPIError(f"QQ token HTTP {response.status_code}")
                    data = response_data(response)
                    if data.get("code") not in (None, 0):
                        raise QQAPIError("QQ token 业务错误，请检查凭证和平台状态")
                    token = data.get("access_token")
                    try:
                        expires = int(data.get("expires_in", 0))
                    except (ValueError, TypeError):
                        raise QQAPIError("QQ token 有效期无效") from None
                    if not isinstance(token, str) or not token or expires <= 0:
                        raise QQAPIError("QQ token 响应缺少有效凭证或有效期")
                    self.token = token
                    self.expires_at = time.monotonic() + max(1, expires - 60)
                    return token
                except httpx.TransportError:
                    if attempt == 1:
                        raise QQAPIError("QQ token 网络请求失败") from None
                    await asyncio.sleep(0.5)
            raise QQAPIError("QQ token 获取失败")

    async def send_group(self, group_id: str, message_id: str, text: str) -> None:
        url = f"{self.settings.qq_api_base_url}/v2/groups/{quote(group_id, safe='')}/messages"
        for attempt in range(2):
            token = await self.access_token()
            try:
                response = await self.client.post(
                    url,
                    headers={"Authorization": f"QQBot {token}"},
                    json={"content": text, "msg_type": 0, "msg_id": message_id, "msg_seq": 1},
                    timeout=self.settings.qq_timeout_seconds,
                )
            except httpx.TransportError:
                # 超时可能已送达，不盲目重发，避免重复回复。
                raise QQDeliveryUnknown("QQ 发送网络异常，送达状态未知") from None
            if response.status_code == 401 and attempt == 0:
                self.expires_at = 0
                continue
            if response.is_error:
                raise QQAPIError(f"QQ 发送 HTTP {response.status_code}")
            data = response_data(response)
            if data.get("code") not in (None, 0) or not data.get("id"):
                raise QQAPIError("QQ 发送失败，请检查平台权限、内容限制及被动回复有效期")
            return
