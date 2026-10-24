import asyncio
import base64
import time
from urllib.parse import quote, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from qbot.config import Settings
from qbot.media import MediaUpload


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

    async def profile(self) -> dict:
        """只读取官方资料，不修改 QQ 机器人的昵称或头像。"""
        for attempt in range(2):
            token = await self.access_token()
            try:
                response = await self.client.get(
                    self.settings.qq_api_base_url + "/users/@me",
                    headers={"Authorization": f"QQBot {token}"},
                    timeout=self.settings.qq_timeout_seconds,
                )
            except httpx.TransportError:
                raise QQAPIError("QQ 资料请求网络异常") from None
            if response.status_code == 401 and attempt == 0:
                self.expires_at = 0
                continue
            if response.is_error:
                raise QQAPIError("QQ 资料获取失败，请检查凭证和平台权限")
            data = response_data(response)
            if data.get("code") not in (None, 0) or not data.get("id"):
                raise QQAPIError("QQ 资料响应无效")
            avatar = data.get("avatar")
            if not isinstance(avatar, str):
                avatar = ""
            try:
                url = urlsplit(avatar)
                # QQ 官方资料可能返回 HTTP 头像，仅对官方头像域名升级 HTTPS。
                host = (url.hostname or "").lower()
                if (url.scheme == "http" and (host == "qlogo.cn" or host.endswith(".qlogo.cn"))
                        and url.port in (None, 80) and not url.username and not url.password):
                    avatar = url._replace(scheme="https", netloc=host).geturl()
                    url = urlsplit(avatar)
                if url.scheme != "https" or not url.hostname or url.username or url.password:
                    avatar = ""
            except ValueError:
                avatar = ""
            return {"id": str(data["id"]), "username": str(data.get("username") or ""),
                    "avatar": avatar}
        raise QQAPIError("QQ 资料鉴权失败")

    async def send_group(self, group_id: str, message_id: str, text: str) -> None:
        url = f"{self.settings.qq_api_base_url}/v2/groups/{quote(group_id, safe='')}/messages"
        await self._send_message(url, message_id, text)

    async def send_private(self, user_id: str, message_id: str, text: str) -> None:
        url = f"{self.settings.qq_api_base_url}/v2/users/{quote(user_id, safe='')}/messages"
        await self._send_message(url, message_id, text)

    async def send_proactive(self, source: str, target_id: str, text: str) -> None:
        """主动发送不携带被动回复 msg_id，权限和额度由 QQ 平台判定。"""
        if source not in ("qq_group", "qq_c2c"):
            raise ValueError("仅支持 QQ 群聊或私聊")
        resource = "groups" if source == "qq_group" else "users"
        url = f"{self.settings.qq_api_base_url}/v2/{resource}/{quote(target_id, safe='')}/messages"
        await self._send_message(url, None, text)

    async def _send_message(self, url: str, message_id: str | None, text: str) -> None:
        payload = {"content": text, "msg_type": 0}
        if message_id is not None:
            payload.update(msg_id=message_id, msg_seq=1)
        await self._send_payload(url, payload)

    async def _send_payload(self, url: str, payload: dict) -> None:
        message_id = payload.get("msg_id")
        for attempt in range(2):
            token = await self.access_token()
            try:
                response = await self.client.post(
                    url,
                    headers={"Authorization": f"QQBot {token}"},
                    json=payload,
                    timeout=self.settings.qq_timeout_seconds,
                )
            except httpx.TransportError:
                # 超时可能已送达，不盲目重发，避免重复回复。
                raise QQDeliveryUnknown("QQ 发送网络异常，送达状态未知") from None
            if response.status_code == 401 and attempt == 0:
                self.expires_at = 0
                continue
            if response.status_code >= 500 and message_id is None:
                raise QQDeliveryUnknown("QQ 服务异常，主动消息送达状态未知")
            if response.is_error:
                raise QQAPIError(f"QQ 发送 HTTP {response.status_code}")
            try:
                data = response_data(response)
            except QQAPIError:
                if message_id is None:
                    raise QQDeliveryUnknown("QQ 响应无法解析，主动消息送达状态未知") from None
                raise
            if message_id is None and data.get("code") in (None, 0) and not data.get("id"):
                raise QQDeliveryUnknown("QQ 未返回消息标识，主动消息送达状态未知")
            if data.get("code") not in (None, 0) or not data.get("id"):
                raise QQAPIError("QQ 发送失败，请检查平台权限、内容限制及被动回复有效期")
            return

    async def upload_media(self, source: str, target_id: str, media: MediaUpload) -> str:
        """仅上传，srv_send_msg=false 确保此步骤不会发送消息。"""
        resource = "groups" if source == "qq_group" else "users"
        url = f"{self.settings.qq_api_base_url}/v2/{resource}/{quote(target_id, safe='')}/files"
        token = await self.access_token()
        try:
            response = await self.client.post(url, headers={"Authorization": f"QQBot {token}"},
                json={"file_type": 1 if media.kind == "image" else 3, "file_name": media.filename,
                      "file_data": base64.b64encode(media.data).decode("ascii"), "srv_send_msg": False},
                timeout=45)
        except httpx.TransportError:
            raise QQAPIError("QQ 媒体上传网络异常，尚未执行消息发送") from None
        if response.is_error:
            raise QQAPIError(f"QQ 媒体上传 HTTP {response.status_code}")
        data = response_data(response)
        info = data.get("file_info")
        if data.get("code") not in (None, 0) or not isinstance(info, str) or not info:
            raise QQAPIError("QQ 未确认媒体上传成功，请检查格式、大小与权限")
        return info

    async def send_media(self, source: str, target_id: str, file_info: str) -> None:
        resource = "groups" if source == "qq_group" else "users"
        url = f"{self.settings.qq_api_base_url}/v2/{resource}/{quote(target_id, safe='')}/messages"
        # 富媒体的 content 按官方兼容要求填空格，不携带历史 msg_id 冒充回复。
        await self._send_payload(url, {"msg_type": 7, "content": " ", "media": {"file_info": file_info}})
