"""后台手动及定时主动消息：复用聊天记录保存审计和请求去重。"""
import asyncio
import time
import logging
from collections import deque

from fastapi import HTTPException

from qbot.qq import QQAPIError, QQDeliveryUnknown
from qbot.media import MediaUpload


class ProactiveSender:
    def __init__(self, manager, store, settings):
        self.manager, self.store, self.settings = manager, store, settings
        self.requests: deque[tuple[float, int, str, str]] = deque()

    async def run_schedules(self) -> None:
        """单进程调度，逐条发送；数据库领取成功后不自动重试。"""
        logger = logging.getLogger(__name__)
        while True:
            try:
                for greeting_id in await self.store.call("due_greetings"):
                    try:
                        await self.send_scheduled(greeting_id)
                    except Exception as exc:
                        logger.error("定时任务执行异常 id=%s type=%s", greeting_id, type(exc).__name__)
            except Exception as exc:
                logger.error("定时任务扫描失败：%s", type(exc).__name__)
            await asyncio.sleep(10)

    async def send_scheduled(self, greeting_id: int, manual: bool = False) -> dict | None:
        async with self.manager.lock:
            task = await self.store.call("claim_scheduled_greeting", greeting_id, manual)
            if task is None:
                return
            if task.get("duplicate"):
                return {"record_id": task["record_id"], "status": task["status"],
                        "message": task["error_message"] or "今天已执行，未重复发送", "duplicate": True}
            bot_id, source = task["bot_id"], task["source"]
            destination = task["group_id"] if source == "qq_group" else task["user_id"]
            status, error, sending = "send_failed", "", False
            try:
                runtime = self.manager.runtimes.get(bot_id)
                if runtime is None or not runtime.accepting:
                    raise HTTPException(409, "机器人未启动，定时消息未发送")
                if len(task["content"]) > self.settings.max_reply_chars:
                    raise HTTPException(400, "定时文案超过消息长度上限，未发送")
                now = time.monotonic()
                while self.requests and now - self.requests[0][0] >= 60:
                    self.requests.popleft()
                if (len(self.requests) >= self.settings.global_rate_limit_per_minute
                        or sum(entry[1:] == (bot_id, source, destination) for entry in self.requests)
                        >= self.settings.rate_limit_per_minute):
                    raise HTTPException(429, "主动发送达到频率限制，定时消息未发送")
                self.requests.append((now, bot_id, source, destination))
                sending = True
                async with asyncio.timeout(25):
                    await runtime.dispatcher.qq.send_proactive(source, destination, task["content"])
                status = "replied"
            except HTTPException as exc:
                error = str(exc.detail)
            except (TimeoutError, QQDeliveryUnknown):
                status, error = "delivery_unknown", "QQ 发送超时或网络异常，可能已送达，不自动重发"
            except QQAPIError:
                error = "QQ 拒绝或未确认发送，请检查主动消息权限、额度及接收范围"
            except asyncio.CancelledError:
                await self.store.call("update_record", task["record_id"],
                    status="delivery_unknown" if sending else "send_failed", error_message="服务停止，任务中断，不自动重发")
                raise
            except Exception as exc:
                logging.getLogger(__name__).error("定时发送异常：%s", type(exc).__name__)
                status = "delivery_unknown" if sending else "send_failed"
                error = "任务执行异常，请核对 QQ 和服务日志，不自动重发"
            await self.store.call("update_record", task["record_id"], status=status, error_message=error)
            return {"record_id": task["record_id"], "status": status, "message": error, "duplicate": False}

    async def send(self, bot_id: int, record_id: int, content: str, request_id: str,
                   media: MediaUpload | None = None) -> dict:
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
            if media:
                try:
                    # 私聊 WAV 在发送前转成 SILK；其他格式仍使用既有平台校验。
                    if not (source == "qq_c2c" and media.kind == "audio" and media.extension == ".wav"):
                        media.check_source(source)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from None
            destination = target["group_id"] if source == "qq_group" else target["user_id"]
            now = time.monotonic()
            while self.requests and now - self.requests[0][0] >= 60:
                self.requests.popleft()
            if (len(self.requests) >= self.settings.global_rate_limit_per_minute
                    or sum(entry[1:] == (bot_id, source, destination) for entry in self.requests)
                    >= self.settings.rate_limit_per_minute):
                raise HTTPException(429, "主动发送过于频繁，请一分钟后重试")
            # 不使用历史 QQ 消息 ID 冒充回复。相同请求即使失败也不会自动重发。
            summary = media.summary if media else content
            question = "【后台主动发送】\n" + summary
            if media:
                question += "\nSHA256: " + media.fingerprint
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
            await self.store.call("update_record", rid, status="processing", answer=summary)
            self.requests.append((now, bot_id, source, destination))
            status, error, stage = "replied", "", "upload" if media else "send"
            try:
                if media:
                    if source == "qq_c2c" and media.kind == "audio" and media.extension == ".wav":
                        stage = "convert"
                        from qbot.audio_codec import to_private_audio
                        media = await to_private_audio(media)
                        media.check_source(source)
                        stage = "upload"
                    async with asyncio.timeout(60):
                        file_info = await runtime.dispatcher.qq.upload_media(source, destination, media)
                    stage = "send"
                    async with asyncio.timeout(25):
                        await runtime.dispatcher.qq.send_media(source, destination, file_info)
                else:
                    async with asyncio.timeout(25):
                        await runtime.dispatcher.qq.send_proactive(source, destination, content)
            except (TimeoutError, QQDeliveryUnknown):
                if stage == "upload":
                    status, error = "send_failed", "媒体上传超时，尚未执行消息发送"
                else:
                    status, error = "delivery_unknown", "QQ 发送超时或网络异常，可能已送达，请勿直接重发"
            except QQAPIError:
                status = "send_failed"
                error = ("媒体上传失败，尚未发送；请检查文件格式、大小、网络和平台权限" if stage == "upload"
                         else "QQ 拒绝或未确认发送，请检查主动消息权限、额度、接收范围及内容限制")
            except (ValueError, OSError) as exc:
                if stage != "convert":
                    raise
                status, error = "send_failed", (str(exc) if isinstance(exc, ValueError)
                    else "无法启动语音转码进程，尚未上传或发送")
            await self.store.call("update_record", rid, status=status, error_message=error)
            return {"record_id": rid, "status": status, "message": error, "duplicate": False}
