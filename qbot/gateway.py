"""QQ 官方 WebSocket 网关：单连接接入、心跳和短线恢复。"""
import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from qbot.qq import QQAPIError, QQClient, response_data

logger = logging.getLogger(__name__)


class Gateway:
    def __init__(self, qq: QQClient, dispatch: Callable[[dict], Awaitable[None]]):
        self.qq, self.dispatch = qq, dispatch
        self.session_id: str | None = None
        self.sequence: int | None = None
        self.received_sequence: int | None = None
        self.status = "未连接"
        self.connected_at = 0.0

    async def endpoint(self) -> str:
        token = await self.qq.access_token()
        response = await self.qq.client.get(
            self.qq.settings.qq_api_base_url + "/gateway/bot",
            headers={"Authorization": f"QQBot {token}"},
            timeout=self.qq.settings.qq_timeout_seconds,
        )
        if response.status_code == 401:
            self.qq.expires_at = 0
        if response.is_error:
            raise QQAPIError("QQ 网关地址获取失败")
        data = response_data(response)
        if data.get("code") not in (None, 0):
            raise QQAPIError("QQ 网关业务错误")
        limits = data.get("session_start_limit", {})
        if not self.session_id and limits.get("remaining", 1) <= 0:
            self.status = "登录次数已耗尽，等待平台重置"
            await asyncio.sleep(max(5, min(float(limits.get("reset_after", 60000)) / 1000, 86400)))
            raise QQAPIError("QQ 登录额度暂不可用")
        if data.get("shards", 1) != 1:
            self.status = "平台要求多分片，当前仅支持单分片"
            raise QQAPIError("不支持多分片")
        url = data.get("url", "")
        parsed = urlsplit(url)
        if parsed.scheme != "wss" or not parsed.hostname or parsed.username or parsed.password:
            raise QQAPIError("QQ 返回无效 WSS 地址")
        return url

    async def run(self) -> None:
        failures = 0
        try:
            while failures < 10:
                started = time.monotonic()
                try:
                    self.status = "连接中"
                    await self.connection(await self.endpoint())
                    raise QQAPIError("网关连接结束")
                except Exception as exc:
                    # 不输出异常正文，避免协议报文、Token 或用户消息进入日志。
                    logger.warning("QQ WebSocket 连接中断：%s", type(exc).__name__)
                    if isinstance(exc, ConnectionClosed):
                        code = exc.rcvd.code if exc.rcvd else None
                        if code in (4004, 4006, 4007, 4009):
                            self.session_id, self.sequence = None, None
                            self.qq.expires_at = 0
                        if code in (4010, 4011, 4013, 4014):
                            self.status = f"平台拒绝连接（{code}），请检查事件权限和分片配置后重启"
                            return
                    if self.connected_at >= started and time.monotonic() - self.connected_at >= 60:
                        failures = 0
                    failures += 1
                    if failures >= 10:
                        break
                    delay = min(60, 2 ** failures) + random.random()
                    self.status = f"连接异常，准备重连（{failures}/10）"
                    await asyncio.sleep(delay)
            self.status = "连续连接失败 10 次，请检查凭证、网络和平台接入方式后重启"
            logger.error(self.status)
        finally:
            if asyncio.current_task().cancelling():
                self.status = "已停止"

    async def connection(self, url: str) -> None:
        async with connect(url, open_timeout=15, close_timeout=5, ping_interval=None,
                           max_size=262144, max_queue=32, proxy=None) as socket:
            hello = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
            if hello.get("op") != 10:
                raise QQAPIError("QQ 网关缺少 Hello")
            interval = float(hello["d"]["heartbeat_interval"]) / 1000
            if not 0 < interval <= 300:
                raise QQAPIError("QQ 心跳间隔无效")
            token = "QQBot " + await self.qq.access_token()
            resume = bool(self.session_id and self.sequence is not None)
            auth = ({"op": 6, "d": {"token": token, "session_id": self.session_id, "seq": self.sequence}}
                    if resume else {"op": 2, "d": {"token": token, "intents": 1 << 25, "shard": [0, 1]}})
            await socket.send(json.dumps(auth))
            self.received_sequence = self.sequence
            acknowledged = True
            ready = asyncio.Event()

            async def heartbeat() -> None:
                nonlocal acknowledged
                while True:
                    await asyncio.sleep(interval)
                    if not acknowledged:
                        raise QQAPIError("QQ 心跳确认超时")
                    acknowledged = False
                    await socket.send(json.dumps({"op": 1, "d": self.received_sequence}))

            async def receive() -> None:
                nonlocal acknowledged
                async for raw in socket:
                    event = json.loads(raw)
                    op = event.get("op")
                    if op == 11:
                        acknowledged = True
                    elif op == 1:
                        await socket.send(json.dumps({"op": 1, "d": self.received_sequence}))
                    elif op == 7:
                        return
                    elif op == 9:
                        # 会话无效时退回 Identify，刷新鉴权 Token。
                        self.session_id, self.sequence = None, None
                        self.qq.expires_at = 0
                        return
                    elif op == 0:
                        sequence = event.get("s")
                        if not isinstance(sequence, int):
                            raise QQAPIError("QQ 事件序号无效")
                        self.received_sequence = sequence
                        if event.get("t") == "READY":
                            session = event.get("d", {}).get("session_id")
                            if not isinstance(session, str) or not session:
                                raise QQAPIError("QQ 会话标识无效")
                            self.session_id = session
                        if event.get("t") in ("READY", "RESUMED"):
                            self.status = "已连接"
                            self.connected_at = time.monotonic()
                            ready.set()
                            logger.info("QQ WebSocket 已连接")
                        # 业务成功接收后才推进恢复游标；失败时重连请求重放。
                        await self.dispatch(event)
                        self.sequence = sequence

            async def login_deadline() -> None:
                await asyncio.wait_for(ready.wait(), timeout=30)
                await asyncio.Future()

            tasks = [asyncio.create_task(heartbeat()), asyncio.create_task(receive()),
                     asyncio.create_task(login_deadline())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
