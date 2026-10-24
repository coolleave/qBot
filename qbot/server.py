import asyncio
import json
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
from pydantic import BaseModel, Field, ValidationError

from qbot.chat import ChatService
from qbot.config import Settings
from qbot.model import create_model
from qbot.qq import QQAPIError, QQDeliveryUnknown, QQClient, signing_key, verify_signature
from qbot.storage import Store, StorageError, mysql_engine
from qbot.recording import RecordedChat

logger = logging.getLogger(__name__)


class Author(BaseModel):
    member_openid: str = Field(min_length=1, max_length=256)
    bot: bool = False


class GroupMessage(BaseModel):
    id: str = Field(min_length=1, max_length=512)
    group_openid: str = Field(min_length=1, max_length=256)
    author: Author
    content: str = Field(default="", max_length=50000)
    timestamp: datetime


@dataclass
class Job:
    message: GroupMessage
    received_at: float
    record_id: int | None = None


class Dispatcher:
    """单进程有界 FIFO，先确认接收，再在后台生成和发送。"""

    def __init__(self, settings: Settings, chat: ChatService, qq: QQClient, store: Store | None = None):
        self.settings, self.chat, self.qq = settings, chat, qq
        self.queue: asyncio.Queue[Job] = asyncio.Queue(settings.queue_max_size)
        self.seen: dict[tuple[str, str], float] = {}
        self.pending: set[tuple[str, str]] = set()
        self.requests: deque[tuple[float, tuple[str, str]]] = deque()
        self.store = store
        self.admission_lock = asyncio.Lock()

    def enqueue(self, message: GroupMessage, record_id: int | None = None, *, check_only: bool = False) -> bool:
        now = time.monotonic()
        key = (message.group_openid, message.id)
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
        session_key = (message.group_openid, message.author.member_openid)
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
                record_id, created = await self.store.call("receive", "qq_group", message.group_openid,
                    message.author.member_openid, message.id, message.content, message.timestamp)
                if not created:
                    return
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
            key = (message.group_openid, message.id)
            try:
                async def send(text: str) -> None:
                    try:
                        async with asyncio.timeout(25):
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
                    kwargs = {"record_id": job.record_id} if self.store else {}
                    await self.chat.reply((message.group_openid, message.author.member_openid),
                                          message.content, send, **kwargs)
                except (QQAPIError, StorageError):
                    raise
                except Exception as exc:
                    # 第三方异常可能包含用户输入或密钥，只记录异常类型。
                    logger.warning("模型调用失败：%s", type(exc).__name__)
                    await send("模型服务暂时不可用或请求超时，请稍后重试。")
            except Exception as exc:
                logger.error("消息处理或发送失败：%s", type(exc).__name__)
            finally:
                self.pending.discard(key)
                self.seen[key] = time.monotonic()
                self.queue.task_done()


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
        async with httpx.AsyncClient() as client:
            active_chat = chat or (RecordedChat(settings, db) if db else ChatService(settings, create_model(settings)))
            dispatcher = Dispatcher(settings, active_chat, qq or QQClient(settings, client), db)
            app.state.dispatcher = dispatcher
            worker = asyncio.create_task(dispatcher.run())
            try:
                yield
            finally:
                # 退出时最多等 10 秒，内存队列不提供崩溃恢复保证。
                try:
                    await asyncio.wait_for(dispatcher.queue.join(), timeout=10)
                except TimeoutError:
                    logger.warning("关闭时仍有未完成消息，内存队列将丢失")
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
                if engine:
                    await asyncio.to_thread(engine.dispose)

    app = FastAPI(title="qBot", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.exception_handler(StorageError)
    async def database_error(request: Request, exc: StorageError):
        return JSONResponse({"detail": "数据库不可用，请检查连接和表结构"}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # 禁止默认验证响应回显输入的密码或 API Key。
        return JSONResponse({"detail": "字段格式不正确，请检查必填项、长度和 URL"}, status_code=422)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/admin", "/api/admin")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
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
    async def webhook(request: Request) -> dict:
        if not settings.qq_app_id or not settings.qq_app_secret.get_secret_value():
            raise HTTPException(503, "QQ 凭证尚未配置")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                raise HTTPException(413, "请求体过大")
        raw = bytes(body)
        secret = settings.qq_app_secret.get_secret_value()
        if not verify_signature(secret, request.headers.get("X-Signature-Timestamp", ""),
                                request.headers.get("X-Signature-Ed25519", ""), raw):
            raise HTTPException(401, "签名无效或已过期")
        app_id = request.headers.get("X-Bot-Appid")
        if app_id is not None and app_id != settings.qq_app_id:
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
            if payload.get("op") == 0 and payload.get("t") == "GROUP_AT_MESSAGE_CREATE":
                message = GroupMessage.model_validate(payload["d"])
                await app.state.dispatcher.accept(message)
        except (ValueError, KeyError, TypeError, ValidationError):
            raise HTTPException(400, "事件格式无效") from None
        return {"op": 12}

    return app
