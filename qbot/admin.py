import asyncio
import hashlib
import hmac
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from importlib.resources import files
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from qbot.config import Settings
from qbot.greetings import GreetingInput
from qbot.security import verify_password
from qbot.storage import Store
from qbot.multimodal_config import MultimodalConfig


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: SecretStr = Field(max_length=256)


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    model_type: Literal["chat", "image", "asr", "tts"] = "chat"
    supports_images: bool = False
    api_type: Literal["deepseek", "openai_compatible"]
    base_url: str = Field(max_length=2048)
    model_name: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = SecretStr("")
    enabled: bool = False

    @field_validator("api_key")
    @classmethod
    def check_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value()
        if len(key) > 4096 or any(c in key for c in "\r\n") or key in ("••••••••", "********"):
            raise ValueError("无效密钥")
        return value

    @field_validator("base_url")
    @classmethod
    def check_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(c.isspace() for c in value)):
            raise ValueError("必须使用不含凭证、查询参数的 HTTPS 地址")
        _ = url.port
        return value.rstrip("/")


class QQInput(BaseModel):
    transport: Literal["websocket", "webhook"] = "websocket"
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    app_id: str = Field(min_length=1, max_length=64, pattern=r"^[0-9]+$")
    app_secret: SecretStr = SecretStr("")

    @field_validator("app_secret")
    @classmethod
    def check_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) > 4096 or secret in ("••••••••", "********") or any(not 33 <= ord(c) <= 126 for c in secret):
            raise ValueError("无效的 AppSecret")
        return value


class BotInput(QQInput):
    name: str = Field(min_length=1, max_length=100)
    provider_id: int | None = Field(default=None, gt=0)
    system_prompt: str = Field(default="", max_length=8000)
    enabled: bool = False
    multimodal: MultimodalConfig = Field(default_factory=MultimodalConfig)


class FamilyBindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    member_name: str = Field(min_length=1, max_length=100)
    family_role: Literal["father", "mother", "friend"]
    qq_number_note: str = Field(default="", max_length=20, pattern=r"^[0-9]*$")


class UserBindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    contact_name: str = Field(min_length=1, max_length=100)
    contact_qq: str = Field(default="", max_length=20, pattern=r"^[0-9]*$")
    source: Literal["qq_c2c", "qq_group"]
    user_id: str = Field(min_length=1, max_length=256, pattern=r"^[\x21-\x7e]+$")
    group_id: str = Field(default="", max_length=191, pattern=r"^[\x21-\x7e]*$")


class DefaultInput(BaseModel):
    provider_id: int | None = Field(default=None, gt=0)


class ProactiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=10000)
    request_id: UUID


class SpeechInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=500)
    voice: str = Field(default="Bunny", min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_ -]+$")


def admin_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    attempts: deque[float] = deque()
    ttl = 3600
    probe_lock = asyncio.Lock()
    media_lock = asyncio.Lock()
    last_probe = 0.0

    def sign(token: str) -> str:
        return hmac.new(settings.admin_session_secret.get_secret_value().encode(), token.encode(), hashlib.sha256).hexdigest()

    # 将会话绑定到当前账号配置，修改账号、密码或签名密钥后旧登录失效。
    auth_version = sign(settings.admin_username + "\0" + settings.admin_password_hash.get_secret_value())

    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def browser_write(request: Request) -> None:
        # 跨站表单无法发送该自定义头；本服务不开放跨域授权。
        if request.headers.get("X-QBot-Admin") != "1":
            raise HTTPException(403, "请求来源无效")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "跨站请求已拒绝")

    async def require_admin(request: Request) -> str:
        value = request.cookies.get("qbot_admin", "")
        token, _, signature = value.partition(".")
        if len(token) != 43 or len(signature) != 64 or not hmac.compare_digest(sign(token), signature):
            raise HTTPException(401, "请先登录")
        csrf = await request.app.state.store.call("admin_session_csrf", token_hash(token), auth_version)
        if csrf is None:
            raise HTTPException(401, "请先登录")
        if request.method not in ("GET", "HEAD"):
            browser_write(request)
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), csrf):
                raise HTTPException(403, "会话校验失败，请刷新后重试")
        request.state.admin_csrf = csrf
        return token

    def store(request: Request) -> Store:
        return request.app.state.store

    @router.get("/admin", response_class=HTMLResponse)
    async def page() -> str:
        return files("qbot").joinpath("static/admin.html").read_text("utf-8")

    @router.post("/api/admin/login")
    async def login(data: Login, request: Request, response: Response) -> dict:
        browser_write(request)
        now = time.monotonic()
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "尝试过于频繁，请一分钟后重试")
        attempts.append(now)
        valid = await asyncio.to_thread(verify_password, data.password.get_secret_value(),
                                        settings.admin_password_hash.get_secret_value())
        if not valid or not hmac.compare_digest(data.username.encode(), settings.admin_username.encode()):
            raise HTTPException(401, "账号或密码错误")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        await request.app.state.store.call("create_admin_session", token_hash(token), csrf, auth_version, ttl)
        response.set_cookie("qbot_admin", token + "." + sign(token), max_age=ttl,
                            httponly=True, secure=settings.admin_cookie_secure, samesite="strict", path="/")
        return {"csrf_token": csrf, "username": settings.admin_username}

    @router.get("/api/admin/session")
    async def session(request: Request, token: str = Depends(require_admin)) -> dict:
        return {"csrf_token": request.state.admin_csrf, "username": settings.admin_username,
                "qq_configured": bool(request.app.state.bots.runtimes)}

    @router.get("/api/admin/bots", dependencies=[Depends(require_admin)])
    async def bots(request: Request, db: Store = Depends(store)) -> dict:
        items = await db.call("list_bots")
        for item in items:
            item.update(request.app.state.bots.state(item["id"]))
        return {"items": items}

    @router.get("/api/admin/bots/{bot_id}/profile", dependencies=[Depends(require_admin)])
    async def bot_profile(bot_id: int, request: Request, refresh: bool = False) -> dict:
        try:
            return await request.app.state.bot_profiles.get(bot_id, refresh)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    async def save_bot(data: BotInput, request: Request, db: Store, bot_id: int | None = None) -> dict:
        values = data.model_dump()
        values["app_secret"] = data.app_secret.get_secret_value()
        try:
            bot_id = await db.call("save_bot", values, bot_id)
            state = await request.app.state.bots.apply(bot_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"id": bot_id, **state}

    @router.post("/api/admin/bots", dependencies=[Depends(require_admin)])
    async def create_bot(data: BotInput, request: Request, db: Store = Depends(store)) -> dict:
        return await save_bot(data, request, db)

    @router.put("/api/admin/bots/{bot_id}", dependencies=[Depends(require_admin)])
    async def edit_bot(bot_id: int, data: BotInput, request: Request, db: Store = Depends(store)) -> dict:
        return await save_bot(data, request, db, bot_id)

    @router.post("/api/admin/bots/{bot_id}/{action}", dependencies=[Depends(require_admin)])
    async def control_bot(bot_id: int, action: Literal["start", "stop"], request: Request,
                          db: Store = Depends(store)) -> dict:
        try:
            await db.call("set_bot_enabled", bot_id, action == "start")
            return await request.app.state.bots.apply(bot_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/admin/logout")
    async def logout(request: Request, response: Response, token: str = Depends(require_admin)) -> dict:
        await request.app.state.store.call("delete_admin_session", token_hash(token))
        response.delete_cookie("qbot_admin", path="/")
        return {"ok": True}

    @router.get("/api/admin/providers", dependencies=[Depends(require_admin)])
    async def providers(db: Store = Depends(store)) -> dict:
        return await db.call("list_providers")

    async def save(data: ProviderInput, db: Store, provider_id: int | None = None) -> dict:
        values = data.model_dump()
        values["api_key"] = data.api_key.get_secret_value()
        try:
            return {"id": await db.call("save_provider", values, provider_id)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/admin/providers", dependencies=[Depends(require_admin)])
    async def create(data: ProviderInput, db: Store = Depends(store)) -> dict:
        return await save(data, db)

    @router.put("/api/admin/providers/{provider_id}", dependencies=[Depends(require_admin)])
    async def edit(provider_id: int, data: ProviderInput, db: Store = Depends(store)) -> dict:
        return await save(data, db, provider_id)

    @router.post("/api/admin/providers/{provider_id}/test", dependencies=[Depends(require_admin)])
    async def test_provider(provider_id: int, db: Store = Depends(store)) -> dict:
        nonlocal last_probe
        if probe_lock.locked() or time.monotonic() - last_probe < 10:
            raise HTTPException(429, "检查正在进行或操作过于频繁，请稍后再试")
        async with probe_lock:
            last_probe = time.monotonic()
            try:
                provider = await db.call("provider_for_probe", provider_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            from qbot.provider_probe import probe_provider
            if provider["model_type"] != "chat":
                raise HTTPException(400, "TTS 请使用文本试听接口 /speech，提交 text 和 voice")
            return await probe_provider(settings, provider)

    @router.post("/api/admin/providers/{provider_id}/speech", dependencies=[Depends(require_admin)])
    async def provider_speech(provider_id: int, data: SpeechInput, db: Store = Depends(store)) -> Response:
        nonlocal last_probe
        if probe_lock.locked() or time.monotonic() - last_probe < 10:
            raise HTTPException(429, "检查或合成正在进行，或操作过于频繁，请稍后再试")
        async with probe_lock:
            last_probe = time.monotonic()
            try:
                provider = await db.call("provider_for_probe", provider_id)
                from qbot.tts import synthesize
                audio = await synthesize(provider, data.text, data.voice)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @router.post("/api/admin/providers/{provider_id}/inspect-media", dependencies=[Depends(require_admin)])
    async def inspect_media(provider_id: int, request: Request, kind: Literal["image", "audio"],
                            db: Store = Depends(store)) -> dict:
        nonlocal last_probe
        if probe_lock.locked() or time.monotonic() - last_probe < 10:
            raise HTTPException(429, "检查正在进行或操作过于频繁，请稍后再试")
        from qbot.multimodal import MAX_FILE, MEDIA_SLOTS, decode_media, transcribe
        import json
        async with probe_lock:
            last_probe = time.monotonic()
            started = time.monotonic()
            try:
                async with asyncio.timeout(80), MEDIA_SLOTS:
                    provider = await db.call("provider_for_probe", provider_id)
                    if kind == "image" and (provider["model_type"] != "chat" or not provider.get("supports_images")
                                             or provider["api_type"] != "openai_compatible"):
                        raise ValueError("请选择支持图片输入的聊天模型")
                    if kind == "audio" and provider["model_type"] != "asr":
                        raise ValueError("请选择 ASR 模型")
                    body = bytearray()
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > MAX_FILE:
                            raise ValueError("文件不能超过8 MB")
                        body.extend(chunk)
                    if kind == "audio":
                        wav = await decode_media(bytes(body), "audio")
                        details = {}
                        reply, usage = await transcribe(provider, wav, details)
                        return {"success": True, "reply": reply, "usage": usage, "details": details,
                                "latency_ms": round((time.monotonic() - started) * 1000)}
                    decoded = json.loads(await decode_media(bytes(body), "gif"))
                    content = [{"type": "text", "text": "请描述图片或表情包的内容。" + decoded["notice"]}]
                    for frame in decoded["frames"]:
                        content.extend([{"type": "text", "text": f"相对时间{frame['time_ms']}ms"},
                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + frame["data"]}}])
                    from qbot.vision import describe_images
                    details = {}
                    summary = await describe_images(settings, provider, "请描述图片或表情包的内容。", content, details,
                                                    slot_acquired=True)
                    return {"success": True, "reply": summary, "usage": details.get("usage", {}),
                            "latency_ms": round((time.monotonic() - started) * 1000)}
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            except Exception:
                raise HTTPException(502, "媒体处理失败或超时，请检查文件、模型配置和网络") from None

    @router.put("/api/admin/default-provider", dependencies=[Depends(require_admin)])
    async def default(data: DefaultInput, db: Store = Depends(store)) -> dict:
        try:
            await db.call("set_default", data.provider_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True}

    @router.get("/api/admin/records", dependencies=[Depends(require_admin)])
    async def records(page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100),
                      bot_id: int | None = Query(None, gt=0), source: str = Query("", max_length=16), group_id: str = Query("", max_length=256),
                      user_id: str = Query("", max_length=256), status: str = Query("", max_length=24),
                      start: datetime | None = None, end: datetime | None = None,
                      db: Store = Depends(store)) -> dict:
        for value in (start, end):
            if value and value.tzinfo is None:
                raise HTTPException(400, "筛选时间必须包含时区")
        if start and end and start > end:
            raise HTTPException(400, "开始时间不能晚于结束时间")
        return await db.call("query_records", page=page, page_size=page_size, source=source, bot_id=bot_id,
            group_id=group_id, user_id=user_id, status=status,
            start=start.astimezone(timezone.utc).replace(tzinfo=None) if start else None,
            end=end.astimezone(timezone.utc).replace(tzinfo=None) if end else None)

    @router.get("/api/admin/records/{record_id}", dependencies=[Depends(require_admin)])
    async def detail(record_id: int, db: Store = Depends(store)) -> dict:
        result = await db.call("record_detail", record_id)
        if result is None:
            raise HTTPException(404, "记录不存在")
        return result

    @router.put("/api/admin/bots/{bot_id}/records/{record_id}/binding", dependencies=[Depends(require_admin)])
    async def bind_member(bot_id: int, record_id: int, data: FamilyBindingInput,
                          db: Store = Depends(store)) -> dict:
        try:
            return await db.call("save_family_binding", bot_id, record_id, data.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.delete("/api/admin/bots/{bot_id}/records/{record_id}/binding", dependencies=[Depends(require_admin)])
    async def unbind_member(bot_id: int, record_id: int, db: Store = Depends(store)) -> dict:
        try:
            return await db.call("save_family_binding", bot_id, record_id, None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/api/admin/bots/{bot_id}/records/{record_id}/greetings", dependencies=[Depends(require_admin)])
    async def get_greetings(bot_id: int, record_id: int, db: Store = Depends(store)) -> dict:
        try:
            return await db.call("greeting_settings", bot_id, record_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.put("/api/admin/bots/{bot_id}/records/{record_id}/greetings", dependencies=[Depends(require_admin)])
    async def put_greetings(bot_id: int, record_id: int, data: GreetingInput, db: Store = Depends(store)) -> dict:
        try:
            await db.call("save_greeting_settings", bot_id, record_id, data.model_dump())
        except ValueError:
            raise HTTPException(400, "任务设置无效，请确认接收人名称绑定、时间段和星期") from None
        return {"ok": True}

    @router.post("/api/admin/bots/{bot_id}/records/{record_id}/greetings/{slot}/run", dependencies=[Depends(require_admin)])
    async def run_greeting(bot_id: int, record_id: int, slot: Literal["morning", "evening"],
                           request: Request, db: Store = Depends(store)) -> dict:
        try:
            rule_id = await db.call("greeting_for_execution", bot_id, record_id, slot)
            return await request.app.state.proactive.send_scheduled(rule_id, manual=True)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/api/admin/bots/{bot_id}/send-targets", dependencies=[Depends(require_admin)])
    async def send_targets(bot_id: int, source: Literal["qq_group", "qq_c2c"],
                           page: int = Query(1, ge=1, le=100000), db: Store = Depends(store)) -> dict:
        return await db.call("send_targets", bot_id, source, page)

    @router.post("/api/admin/bots/{bot_id}/records/{record_id}/send", dependencies=[Depends(require_admin)])
    async def send_proactive(bot_id: int, record_id: int, data: ProactiveInput, request: Request) -> dict:
        return await request.app.state.proactive.send(bot_id, record_id, data.content, str(data.request_id))

    @router.get("/api/admin/greeting-targets", dependencies=[Depends(require_admin)])
    async def greeting_targets(bot_id: int | None = Query(None, gt=0),
                               page: int = Query(1, ge=1, le=100000), db: Store = Depends(store)) -> dict:
        return await db.call("greeting_targets", bot_id=bot_id, page=page)

    @router.get("/api/admin/user-bindings", dependencies=[Depends(require_admin)])
    async def user_bindings(bot_id: int | None = Query(None, gt=0), user_id: str = Query("", max_length=256),
                            page: int = Query(1, ge=1, le=100000), db: Store = Depends(store)) -> dict:
        return await db.call("list_user_bindings", bot_id=bot_id, user_id=user_id.strip(), page=page)

    @router.put("/api/admin/bots/{bot_id}/user-bindings", dependencies=[Depends(require_admin)])
    async def bind_user_id(bot_id: int, data: UserBindingInput, db: Store = Depends(store)) -> dict:
        try:
            return await db.call("bind_user_id", bot_id, data.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.delete("/api/admin/bots/{bot_id}/records/{record_id}/user-binding", dependencies=[Depends(require_admin)])
    async def unbind_contact(bot_id: int, record_id: int, db: Store = Depends(store)) -> dict:
        try:
            return await db.call("save_contact", bot_id, record_id, None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/admin/bots/{bot_id}/records/{record_id}/send-media", dependencies=[Depends(require_admin)])
    async def send_media(bot_id: int, record_id: int, request: Request,
                         request_id: UUID, kind: Literal["image", "audio"],
                         filename: str = Query(..., min_length=1, max_length=150)) -> dict:
        from qbot.media import MAX_MEDIA_BYTES, validate_media
        if media_lock.locked():
            raise HTTPException(429, "已有文件正在上传或发送，请稍后再试")
        async with media_lock:
            body = bytearray()
            try:
                async with asyncio.timeout(30):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > MAX_MEDIA_BYTES:
                            raise HTTPException(413, "文件不能超过 3.5 MB")
                        body.extend(chunk)
            except TimeoutError:
                raise HTTPException(408, "文件上传超时，请重新选择文件") from None
            try:
                media = validate_media(kind, filename, bytes(body))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            return await request.app.state.proactive.send(bot_id, record_id, "", str(request_id), media=media)

    return router
