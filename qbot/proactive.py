"""管理员手动主动消息：复用聊天记录保存审计和请求去重。"""
import asyncio
import time
from collections import deque

from fastapi import HTTPException

from qbot.qq import QQAPIError, QQDeliveryUnknown


class ProactiveSender:
    def __init__(self, manager, store, settings):
        self.manager, self.store, self.settings = manager, store, settings
        self.requests: deque[tuple[float, int, str, str]] = deque()

    async def send(self, bot_id: int, record_id: int, content: str, request_id: str) -> dict:
        if len(content) > self.settings.max_reply_chars:
            raise HTTPException(400, f"消息最多 {self.settings.max_reply_chars} 字符")
        # 与启停共用锁，发送期间不关闭客户端或替换凭证。
        async with self.manager.lock:
            runtime = self.manager.runtimes.get(bot_id)
            if runtime is None or not runtime.accepting:
                raise HTTPException(409, "请先启动对应机器人")
            target = await self.store.call("record_detail", record_id)
            if not target or target["bot_id"] != bot_id or target["source"] not in ("qq_group", "qq_c2c"):
                raise HTTPException(400, "请选择此机器人已有的 QQ 聊天记录")
            source = target["source"]
            destination = target["group_id"] if source == "qq_group" else target["user_id"]
            now = time.monotonic()
            while self.requests and now - self.requests[0][0] >= 60:
                self.requests.popleft()
            if (len(self.requests) >= self.settings.global_rate_limit_per_minute
                    or sum(entry[1:] == (bot_id, source, destination) for entry in self.requests)
                    >= self.settings.rate_limit_per_minute):
                raise HTTPException(429, "主动发送过于频繁，请一分钟后重试")
            # 不使用历史 QQ 消息 ID 冒充回复。相同请求即使失败也不会自动重发。
            question = "【后台主动发送】\n" + content
            rid, created = await self.store.call(
                "receive", source, target["group_id"], target["user_id"],
                "admin-send:" + request_id, question, bot_id=bot_id,
            )
            if not created:
                previous = await self.store.call("record_detail", rid)
                if previous["question"] != question or previous["user_id"] != target["user_id"]:
                    raise HTTPException(409, "请求标识已用于其他消息，请重新打开主动发送窗口")
                return {"record_id": rid, "status": previous["status"],
                        "message": previous["error_message"] or "", "duplicate": True}
            await self.store.call("update_record", rid, status="processing", answer=content)
            self.requests.append((now, bot_id, source, destination))
            status, error = "replied", ""
            try:
                async with asyncio.timeout(25):
                    await runtime.dispatcher.qq.send_proactive(source, destination, content)
            except (TimeoutError, QQDeliveryUnknown):
                status, error = "delivery_unknown", "QQ 发送超时或网络异常，可能已送达，请勿直接重发"
            except QQAPIError:
                status, error = "send_failed", "QQ 拒绝或未确认发送，请检查主动消息权限、额度、接收范围及内容限制"
            await self.store.call("update_record", rid, status=status, error_message=error)
            return {"record_id": rid, "status": status, "message": error, "duplicate": False}
