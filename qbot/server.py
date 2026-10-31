import asyncio
import json
import hashlib
from typing import Literal
import logging
import time
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, ValidationError

from qbot.timing import measure
from qbot.chat import ChatService
from qbot.config import Settings
from qbot.model import create_model
from qbot.qq import QQAPIError, QQDeliveryUnknown, QQClient, signing_key, verify_signature
from qbot.storage import Store, StorageError, mysql_engine
from qbot.recording import RecordedChat
from qbot.multimodal import Attachment, normalize_faces, prepare_message

logger = logging.getLogger(__name__)


class Author(BaseModel):
    member_openid: str = Field(min_length=1, max_length=256, pattern=r"^[\x21-\x7e]+$")
    bot: bool = False
    username: str | None = None


class GroupMessage(BaseModel):
    source: Literal["qq_group", "qq_c2c"] = "qq_group"
    id: str = Field(min_length=1, max_length=512, pattern=r"^[\x21-\x7e]+$")
    group_openid: str = Field(min_length=1, max_length=191, pattern=r"^[\x21-\x7e]+$")
    author: Author
    content: str = Field(default="", max_length=50000)
    timestamp: datetime
    attachments: list[Attachment] = Field(default_factory=list, max_length=20)


class PrivateAuthor(BaseModel):
    user_openid: str = Field(min_length=1, max_length=256, pattern=r"^[\x21-\x7e]+$")
    bot: bool = False
    username: str | None = None


class PrivateMessage(BaseModel):
    id: str = Field(min_length=1, max_length=512, pattern=r"^[\x21-\x7e]+$")
    author: PrivateAuthor
    content: str = Field(default="", max_length=50000)
    timestamp: datetime
    attachments: list[Attachment] = Field(default_factory=list, max_length=20)


def incoming_message(event_type: str, data: dict) -> GroupMessage:
    if event_type == "C2C_MESSAGE_CREATE":
        message = PrivateMessage.model_validate(data)
        # 私聊没有群 ID，使用稳定的用户摘要作为存储范围，兼容旧版索引长度。
        scope = "c2c:" + hashlib.sha256(message.author.user_openid.encode()).hexdigest()
        return GroupMessage(source="qq_c2c", id=message.id, group_openid=scope,
            author=Author(member_openid=message.author.user_openid, bot=message.author.bot, username=message.author.username),
            content=message.content, timestamp=message.timestamp, attachments=message.attachments)
    return GroupMessage.model_validate({**data, "source": "qq_group"})


def message_scope(message: GroupMessage) -> str:
    return message.source + ":" + message.group_openid


@dataclass
class Job:
    message: GroupMessage
    received_at: float
    record_id: int | None = None


class Dispatcher:
    """单进程有界 FIFO，先确认接收，再在后台生成和发送。"""

    def __init__(self, settings: Settings, chat: ChatService, qq: QQClient, store: Store | None = None, bot_id: int = 1):
        self.bot_id = bot_id
        self.settings, self.chat, self.qq = settings, chat, qq
        self.queue: asyncio.Queue[Job] = asyncio.Queue(settings.queue_max_size)
        self.seen: dict[tuple[str, str], float] = {}
        self.pending: set[tuple[str, str]] = set()
        self.requests: deque[tuple[float, tuple[str, str]]] = deque()
        self.store = store
        self.admission_lock = asyncio.Lock()

    def enqueue(self, message: GroupMessage, record_id: int | None = None, *, check_only: bool = False) -> bool:
        now = time.monotonic()
        key = (message_scope(message), message.id)
        self.seen = {k: v for k, v in self.seen.items() if now - v < 600}
        if key in self.seen or key in self.pending:
            return False
        if message.timestamp.tzinfo is None:
            raise HTTPException(400, "消息时间必须包含时区")
        age = (datetime.now(timezone.utc) - message.timestamp).total_seconds()
        if age > 150 or age < -60 or message.author.bot:
            return False
        while self.requests and now - self.requests[0][0] >= 60:
            self.requests.popleft()
        session_key = (message_scope(message), message.author.member_openid)
        if (len(self.requests) >= self.settings.global_rate_limit_per_minute
                or sum(k == session_key for _, k in self.requests)
                >= self.settings.rate_limit_per_minute):
            raise HTTPException(429, "请求过于频繁", headers={"Retry-After": "60"})
        if self.queue.full() or len(self.seen) >= 10000:
            raise HTTPException(503, "处理队列已满", headers={"Retry-After": "5"})
        if check_only:
            return True
        self.queue.put_nowait(Job(message, now, record_id))
        self.pending.add(key)
        self.requests.append((now, session_key))
        return True

    async def accept(self, message: GroupMessage) -> None:
        async with self.admission_lock:
            if not self.enqueue(message, check_only=True):
                return
            record_id = None
            if self.store:
                record_id, created = await self.store.call("receive", message.source, message.group_openid,
                    message.author.member_openid, message.id,
                    normalize_faces(message.content) + (f"\n【附件 {len(message.attachments)} 个】" if message.attachments else ""),
                    message.timestamp, bot_id=self.bot_id, platform_nickname=message.author.username)
                if not created:
                    return
            logger.info("收到 QQ 消息，机器人：%s，来源：%s", self.bot_id, message.source)
            if not self.enqueue(message, record_id) and self.store and record_id:
                await self.store.call("update_record", record_id, status="expired",
                                      error_message="消息在入库期间超过处理时限")

    async def expired(self, job: Job) -> None:
        if self.store and job.record_id:
            await self.store.call("update_record", job.record_id, status="expired",
                                  error_message="等待时间过长，未调用模型")

    async def run(self) -> None:
        while True:
            job = await self.queue.get()
            message = job.message
            key = (message_scope(message), message.id)
            sending = False
            processing_started = time.monotonic()
            diagnostics = {"attachments": [], "reply_kind": "text", "timings": {
                "queue": {"elapsed_ms": round((processing_started - job.received_at) * 1000), "status": "success"}
            }}
            try:
                async def send(text: str) -> None:
                    nonlocal sending
                    sending = True
                    with measure(diagnostics, "send"):
                        try:
                            async with asyncio.timeout(25):
                                if message.source == "qq_c2c":
                                    await self.qq.send_private(message.author.member_openid, message.id, text)
                                else:
                                    await self.qq.send_group(message.group_openid, message.id, text)
                        except TimeoutError:
                            raise QQDeliveryUnknown("QQ 发送总时限已到，送达状态未知") from None

                # 为模型和发送预留时间，不能在队列中耗尽 QQ 被动回复窗口。
                age = (datetime.now(timezone.utc) - message.timestamp).total_seconds()
                if age > 270:
                    await self.expired(job)
                    logger.warning("队列消息已过期，跳过发送")
                    continue
                if age > 150 or time.monotonic() - job.received_at > 120:
                    await self.expired(job)
                    await send("等待时间过长，请重新发送问题。")
                    continue

                try:
                    deadline = min(job.received_at + 120, time.monotonic() + max(0, 270 - age))
                    async with asyncio.timeout_at(deadline):
                        if self.store and isinstance(self.chat, RecordedChat):
                            config = await self.store.call("multimodal_config", self.bot_id)
                            from qbot.memory_config import memory_command
                            control = (message.content.strip() == "/reset" or memory_command(message.content) is not None
                                       or len(message.content.strip()) > self.settings.max_input_chars)
                            with measure(diagnostics, "preprocess"):
                                prepared = await prepare_message(message.content, [] if control else message.attachments,
                                                                 config, self.store, self.bot_id, details=diagnostics)
                            diagnostics.update(prepared["details"])
                            await self.store.call("update_record", job.record_id,
                                media_details=json.dumps(diagnostics, ensure_ascii=False))
                            if not prepared["usable"] and message.attachments:
                                error = ("未能识别附件。" + "；".join(diagnostics["warnings"]))[:self.settings.max_reply_chars]
                                await self.store.call("update_record", job.record_id, answer=error, status="processing")
                                await send(error[:self.settings.max_reply_chars])
                                await self.store.call("update_record", job.record_id, status="rejected", error_message=error[:1000])
                                continue

                            async def respond(value: str) -> None:
                                nonlocal sending
                                from qbot.auto_voice import voice_file, wants_voice
                                if diagnostics.get("warnings"):
                                    notice = "\n（部分附件未完整识别：" + "；".join(diagnostics["warnings"]) + "）"
                                    notice = notice[:min(500, self.settings.max_reply_chars)]
                                    value = value[:max(0, self.settings.max_reply_chars - len(notice))] + notice
                                    await self.store.call("update_record", job.record_id, answer=value)
                                remaining = deadline - time.monotonic()
                                if (not control and not diagnostics.get("warnings") and remaining >= 80
                                        and wants_voice(value, config, message.source, prepared["voice_input"])):
                                    target = message.author.member_openid if message.source == "qq_c2c" else message.group_openid
                                    try:
                                        # 只包围尚未发送的准备阶段；媒体发送开始后绝不回退。
                                        async with asyncio.timeout(min(65, remaining - 15)):
                                            with measure(diagnostics, "voice_prepare"):
                                                info = await voice_file(value, config, self.store, message.source, self.qq, target, diagnostics)
                                    except Exception:
                                        diagnostics.setdefault("fallback_reason", "语音准备超时或不可用")
                                        notice = "\n（语音暂不可用，已改用文字）"
                                        fallback = value[:max(0, self.settings.max_reply_chars - len(notice))] + notice
                                        await self.store.call("update_record", job.record_id, answer=fallback)
                                        await send(fallback)
                                        return
                                    diagnostics["reply_kind"] = "audio"
                                    sending = True
                                    with measure(diagnostics, "send"):
                                        try:
                                            async with asyncio.timeout(10):
                                                await self.qq.send_media(message.source, target, info, message.id)
                                        except TimeoutError:
                                            raise QQDeliveryUnknown("语音回复超时，送达未知") from None
                                else:
                                    if wants_voice(value, config, message.source, prepared["voice_input"]) and remaining < 80:
                                        diagnostics["fallback_reason"] = "剩余回复时间不足，改用文字"
                                    await send(value)

                            from qbot.auto_voice import voice_requested
                            await self.chat.reply((message_scope(message), message.author.member_openid),
                                prepared["text"], respond, record_id=job.record_id, image_blocks=prepared["blocks"],
                                vision_provider_id=config["vision_provider_id"], diagnostics=diagnostics,
                                audio_unrecognized=bool(diagnostics.get("audio_unrecognized")),
                                speech_style=(not control and not diagnostics.get("warnings")
                                              and voice_requested(config, message.source, prepared["voice_input"])))
                        else:
                            await self.chat.reply((message_scope(message), message.author.member_openid),
                                                  message.content, send)
                except (QQAPIError, StorageError):
                    raise
                except Exception as exc:
                    # 第三方异常可能包含用户输入或密钥，只记录异常类型。
                    logger.warning("模型调用失败：%s", type(exc).__name__)
                    if self.store and job.record_id:
                        await self.store.call("update_record", job.record_id,
                            status="delivery_unknown" if sending else "model_failed",
                            error_message="请求超时或处理失败" + ("，送达未知" if sending else ""))
                    if not sending:
                        async with asyncio.timeout(max(0.01, min(10, 270 - (datetime.now(timezone.utc) - message.timestamp).total_seconds()))):
                            from qbot.vision import VisionError
                            if diagnostics.get("audio_unrecognized"):
                                fallback = "刚才那段语音我没听清，可以再说一遍，或者发文字给我吗？"
                                if self.store and job.record_id:
                                    await self.store.call("update_record", job.record_id, answer=fallback)
                                await send(fallback)
                            else:
                                await send(str(exc) if isinstance(exc, VisionError) else "模型服务暂时不可用或请求超时，请稍后重试。")
            except Exception as exc:
                logger.error("消息处理或发送失败：%s", type(exc).__name__)
                if self.store and job.record_id:
                    try:
                        await self.store.call("update_record", job.record_id,
                            status="delivery_unknown" if sending and isinstance(exc, (QQDeliveryUnknown, TimeoutError)) else "send_failed",
                            error_message="处理或发送失败，请核对 QQ 和服务日志")
                    except StorageError:
                        logger.error("发送状态保存失败")
            except asyncio.CancelledError:
                if self.store and job.record_id:
                    try:
                        await self.store.call("update_record", job.record_id,
                            status="delivery_unknown" if sending else "model_failed",
                            error_message="机器人停止，消息处理被中断" + ("，送达未知" if sending else ""))
                    except StorageError:
                        logger.error("中断状态保存失败")
                raise
            finally:
                finished = time.monotonic()
                diagnostics["timings"]["processing"] = {"elapsed_ms": round((finished - processing_started) * 1000), "status": "finished"}
                diagnostics["timings"]["total"] = {"elapsed_ms": round((finished - job.received_at) * 1000), "status": "finished"}
                self.pending.discard(key)
                self.seen[key] = time.monotonic()
                self.queue.task_done()
                if self.store and job.record_id:
                    try:
                        await self.store.call("update_record", job.record_id,
                            media_details=json.dumps(diagnostics, ensure_ascii=False))
                    except StorageError:
                        logger.error("多模态处理详情保存失败")


def create_app(settings: Settings | None = None, *, chat: ChatService | None = None,
               qq: QQClient | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or Settings()
    if settings.admin_enabled:
        settings.require_admin()
    else:
        settings.require_credentials(qq=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = store
        engine = None
        if settings.admin_enabled and db is None:
            engine = mysql_engine(settings)
            try:
                db = await asyncio.to_thread(Store, engine, settings.provider_encryption_key.get_secret_value())
            except Exception:
                engine.dispose()
                raise
        app.state.store = db
        from qbot.bots import BotManager
        manager = BotManager(settings, db, chat, qq)
        app.state.bots = manager
        from qbot.proactive import ProactiveSender
        app.state.proactive = ProactiveSender(manager, db, settings)
        from qbot.bot_profiles import BotProfiles
        app.state.bot_profiles = BotProfiles(settings, db)
        scheduler = None
        memory_task = None
        try:
            await manager.start_all()
            if settings.admin_enabled:
                scheduler = asyncio.create_task(app.state.proactive.run_schedules(), name="scheduled-messages")
                from qbot.memory import MemoryWorker
                app.state.memory = MemoryWorker(settings, db)
                memory_task = asyncio.create_task(app.state.memory.run(), name="memory-worker")
            yield
        finally:
            if memory_task is not None:
                memory_task.cancel()
                with suppress(asyncio.CancelledError):
                    await memory_task
            if scheduler is not None:
                scheduler.cancel()
                with suppress(asyncio.CancelledError):
                    await scheduler
            await manager.close()
            if engine:
                await asyncio.to_thread(engine.dispose)

    app = FastAPI(title="qBot", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.exception_handler(StorageError)
    async def database_error(request: Request, exc: StorageError):
        return JSONResponse({"detail": "数据库不可用，请检查连接和表结构"}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        route_path = getattr(request.scope.get("route"), "path", "")
        if request.method in {"POST", "PUT"} and route_path in {
            "/api/admin/bots/{bot_id}/roles", "/api/admin/bots/{bot_id}/roles/{role_id}",
        }:
            # 仅使用固定文案，避免 Pydantic 的 input/ctx 泄露用户提交内容。
            rules = {
                "value": "角色编号须为 1～16 位小写字母、数字或下划线，且以字母开头",
                "label": "显示名称必填，最多 100 字，不能包含控制字符",
                "relationship": "关系说明最多 500 字，不能包含控制字符",
                "member_name": "预设称呼最多 100 字，不能包含控制字符",
            }
            messages = []
            for error in exc.errors():
                loc = error.get("loc", ())
                message = rules.get(loc[-1]) if loc and loc[0] == "body" else None
                message = message or "角色请求格式不正确，请刷新页面后重试"
                if message not in messages:
                    messages.append(message)
            return JSONResponse({"detail": "；".join(messages)}, status_code=422)
        # 禁止默认验证响应回显输入的密码或 API Key。
        return JSONResponse({"detail": "字段格式不正确，请检查必填项、长度和 URL"}, status_code=422)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/admin", "/api/admin")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            # 部分浏览器不会将 WebSocket 视为 CSP 的 self，显式放行当前站点。
            socket_origin = f"{'wss' if request.url.scheme == 'https' else 'ws'}://{request.url.netloc}"
            if any(char in socket_origin for char in "'\"; \r\n\t"):
                socket_origin = ""
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: blob:; "
                f"connect-src 'self' {socket_origin}; media-src 'self' blob:; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            response.headers["Permissions-Policy"] = "microphone=(self)"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    if settings.admin_enabled:
        from qbot.admin import admin_router
        app.include_router(admin_router(settings))
        app.mount("/admin-assets", StaticFiles(directory=str(files("qbot").joinpath("static"))), name="admin-assets")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/qq/webhook")
    @app.post("/qq/webhook/{bot_id}")
    async def webhook(request: Request, bot_id: int = 1) -> dict:
        runtime = app.state.bots.runtimes.get(bot_id)
        if runtime is None or not runtime.accepting:
            raise HTTPException(503, "机器人不存在或未启动")
        qq_settings = runtime.settings
        if qq_settings.qq_transport != "webhook":
            raise HTTPException(409, "当前使用 WebSocket，请勿同时推送 Webhook")
        if not qq_settings.qq_app_id or not qq_settings.qq_app_secret.get_secret_value():
            raise HTTPException(503, "QQ 凭证尚未配置")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                raise HTTPException(413, "请求体过大")
        raw = bytes(body)
        secret = qq_settings.qq_app_secret.get_secret_value()
        if not verify_signature(secret, request.headers.get("X-Signature-Timestamp", ""),
                                request.headers.get("X-Signature-Ed25519", ""), raw):
            raise HTTPException(401, "签名无效或已过期")
        app_id = request.headers.get("X-Bot-Appid")
        if app_id is not None and app_id != qq_settings.qq_app_id:
            raise HTTPException(403, "AppID 不匹配")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
            if payload.get("op") == 13:
                data = payload["d"]
                token, timestamp = data["plain_token"], data["event_ts"]
                if not isinstance(token, str) or not isinstance(timestamp, str):
                    raise ValueError
                signature = signing_key(secret).sign((timestamp + token).encode("utf-8")).hex()
                return {"plain_token": token, "signature": signature}
            if payload.get("op") == 0 and payload.get("t") in ("GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"):
                message = incoming_message(payload["t"], payload["d"])
                await app.state.bots.accept(bot_id, runtime, message)
        except (ValueError, KeyError, TypeError, ValidationError):
            raise HTTPException(400, "事件格式无效") from None
        return {"op": 12}

    return app
