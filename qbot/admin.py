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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from qbot.config import Settings
from qbot.security import verify_password
from qbot.storage import Store


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: SecretStr = Field(max_length=256)


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
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


class DefaultInput(BaseModel):
    provider_id: int | None = Field(default=None, gt=0)


def admin_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    # 单进程内存登录会话，退出即撤销，重启全部失效。
    sessions: dict[str, tuple[float, str]] = {}
    attempts: deque[float] = deque()
    ttl = 3600

    def sign(token: str) -> str:
        return hmac.new(settings.admin_session_secret.get_secret_value().encode(), token.encode(), hashlib.sha256).hexdigest()

    def cleanup() -> None:
        now = time.monotonic()
        for token in [key for key, value in sessions.items() if value[0] <= now]:
            del sessions[token]

    def browser_write(request: Request) -> None:
        # 跨站表单无法发送该自定义头；本服务不开放跨域授权。
        if request.headers.get("X-QBot-Admin") != "1":
            raise HTTPException(403, "请求来源无效")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "跨站请求已拒绝")

    def require_admin(request: Request) -> str:
        cleanup()
        value = request.cookies.get("qbot_admin", "")
        token, _, signature = value.partition(".")
        session = sessions.get(token)
        if not session or not hmac.compare_digest(sign(token), signature):
            raise HTTPException(401, "请先登录")
        if request.method not in ("GET", "HEAD"):
            browser_write(request)
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session[1]):
                raise HTTPException(403, "会话校验失败，请刷新后重试")
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
        cleanup()
        if len(sessions) >= 100:
            sessions.pop(next(iter(sessions)))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sessions[token] = (time.monotonic() + ttl, csrf)
        response.set_cookie("qbot_admin", token + "." + sign(token), max_age=ttl,
                            httponly=True, secure=settings.admin_cookie_secure, samesite="strict", path="/")
        return {"csrf_token": csrf, "username": settings.admin_username}

    @router.get("/api/admin/session")
    async def session(token: str = Depends(require_admin)) -> dict:
        return {"csrf_token": sessions[token][1], "username": settings.admin_username,
                "qq_configured": bool(settings.qq_app_id and settings.qq_app_secret.get_secret_value()),
                "environment_model_configured": bool(settings.deepseek_api_key.get_secret_value())}

    @router.post("/api/admin/logout")
    async def logout(response: Response, token: str = Depends(require_admin)) -> dict:
        sessions.pop(token, None)
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

    @router.put("/api/admin/default-provider", dependencies=[Depends(require_admin)])
    async def default(data: DefaultInput, db: Store = Depends(store)) -> dict:
        try:
            await db.call("set_default", data.provider_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True}

    @router.get("/api/admin/records", dependencies=[Depends(require_admin)])
    async def records(page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100),
                      source: str = Query("", max_length=16), group_id: str = Query("", max_length=256),
                      user_id: str = Query("", max_length=256), status: str = Query("", max_length=24),
                      start: datetime | None = None, end: datetime | None = None,
                      db: Store = Depends(store)) -> dict:
        for value in (start, end):
            if value and value.tzinfo is None:
                raise HTTPException(400, "筛选时间必须包含时区")
        if start and end and start > end:
            raise HTTPException(400, "开始时间不能晚于结束时间")
        return await db.call("query_records", page=page, page_size=page_size, source=source,
            group_id=group_id, user_id=user_id, status=status,
            start=start.astimezone(timezone.utc).replace(tzinfo=None) if start else None,
            end=end.astimezone(timezone.utc).replace(tzinfo=None) if end else None)

    @router.get("/api/admin/records/{record_id}", dependencies=[Depends(require_admin)])
    async def detail(record_id: int, db: Store = Depends(store)) -> dict:
        result = await db.call("record_detail", record_id)
        if result is None:
            raise HTTPException(404, "记录不存在")
        return result

    return router
