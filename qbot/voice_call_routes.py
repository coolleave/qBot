"""后台通话 API 与一次性 WebSocket 票据。"""
import asyncio
import hmac
import logging
import secrets
import time
from datetime import datetime
from uuid import uuid4

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from qbot.voice_call_config import VoiceCallConfig, validate_voice_provider
from qbot.voice_calls import VoiceCall
from qbot.voice_call_realtime import RealtimeVoiceCall

logger = logging.getLogger(__name__)


class StartCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_id: int = Field(gt=0)


class VoiceMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=600)


def install_voice_routes(router, settings, require_admin, sign, token_hash, auth_version) -> None:
    # 项目部署约定为单进程；只保留一个待接通票据和一条活动通话。
    lock = asyncio.Lock()
    pending = {}
    active = {}
    # 固定管理账号对应稳定网页身份；登录票据轮换不改变记忆归属。
    memory_owner = token_hash("voice-admin:" + settings.admin_username)

    async def bundle_for(db, bot_id: int) -> dict:
        config = await db.call("voice_config", bot_id)
        if not config["enabled"]:
            raise ValueError("请先保存并启用该机器人的语音通话配置")
        bot = next((item for item in await db.call("list_bots") if item["id"] == bot_id), None)
        if not bot:
            raise ValueError("机器人不存在")
        if config["mode"] == "realtime":
            realtime = await db.call("capability_provider", config["realtime_provider_id"], "realtime")
            validate_voice_provider(realtime, "realtime")
            return dict(config=config, bot=bot, realtime=realtime, memory_owner=memory_owner)
        chat = (await db.call("capability_provider", config["chat_provider_id"], "chat")
                if config["chat_provider_id"] else await db.call("active_provider", bot_id))
        if not chat:
            raise ValueError("请先配置聊天模型")
        asr = await db.call("capability_provider", config["asr_provider_id"], "asr_realtime")
        tts = await db.call("capability_provider", config["tts_provider_id"], "tts")
        for provider, kind in ((chat, "chat"), (asr, "asr_realtime"), (tts, "tts")):
            validate_voice_provider(provider, kind)
        return dict(config=config, bot=bot, chat=chat, asr=asr, tts=tts)

    @router.get("/api/admin/voice-calls/config/{bot_id}", dependencies=[Depends(require_admin)])
    async def get_config(bot_id: int, request: Request) -> dict:
        try:
            return await request.app.state.store.call("voice_config", bot_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/api/admin/voice-calls/memories/{bot_id}", dependencies=[Depends(require_admin)])
    async def memories(bot_id: int, request: Request) -> dict:
        return {"items": await request.app.state.store.call("voice_memory_list", bot_id, memory_owner, "", 100)}

    @router.post("/api/admin/voice-calls/memories/{bot_id}", dependencies=[Depends(require_admin)])
    async def save_memory(bot_id: int, data: VoiceMemoryInput, request: Request) -> dict:
        try:
            await request.app.state.store.call("save_voice_memory", bot_id, memory_owner, data.content)
            return {"saved": True}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.delete("/api/admin/voice-calls/memories/{bot_id}/{memory_id}", dependencies=[Depends(require_admin)])
    async def delete_memory(bot_id: int, memory_id: int, request: Request) -> dict:
        async with lock:
            for key in [key for key, value in pending.items() if value["expires"] < time.monotonic()]:
                del pending[key]
            if active or pending:
                raise HTTPException(409, "请先挂断通话再删除记忆，避免当前上下文继续引用")
            await request.app.state.store.call("delete_voice_memory", bot_id, memory_owner, memory_id)
        return {"deleted": True}

    @router.put("/api/admin/voice-calls/config/{bot_id}", dependencies=[Depends(require_admin)])
    async def save_config(bot_id: int, data: VoiceCallConfig, request: Request) -> dict:
        try:
            await request.app.state.store.call("save_voice_config", bot_id, data.model_dump())
            return {"saved": True}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/admin/voice-calls")
    async def create(data: StartCall, request: Request, token: str = Depends(require_admin)) -> dict:
        async with lock:
            now = time.monotonic()
            for key in [key for key, value in pending.items() if value["expires"] < now]:
                del pending[key]
            if pending or active:
                raise HTTPException(409, "当前已有通话或正在接通，请先挂断；未接通票据30秒后释放")
            try:
                await bundle_for(request.app.state.store, data.bot_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            call_id, ticket = uuid4().hex, secrets.token_urlsafe(32)
            pending[call_id] = {"bot_id": data.bot_id, "owner": token_hash(token),
                                "ticket": ticket, "expires": time.monotonic() + 30}
            return {"call_id": call_id, "ticket": ticket}

    @router.delete("/api/admin/voice-calls/{call_id}")
    async def end(call_id: str, token: str = Depends(require_admin)) -> dict:
        owner = token_hash(token)
        async with lock:
            item = pending.get(call_id)
            if item and item["owner"] == owner:
                del pending[call_id]
            item = active.get(call_id)
            if item and item["owner"] == owner:
                item["stop"].set()
        return {"requested": True}

    def effective_status(row: dict) -> dict:
        if row["status"] in ("connecting", "active") and row["id"] not in active:
            row["status"] = "interrupted"
            row["reason"] = "服务重启或异常退出，结束时间未知"
        return row

    @router.get("/api/admin/voice-calls/history/{bot_id}", dependencies=[Depends(require_admin)])
    async def history(bot_id: int, request: Request, before: datetime | None = None) -> dict:
        rows = await request.app.state.store.call("list_voice_calls", bot_id, before)
        return {"items": [effective_status(row) for row in rows]}

    @router.get("/api/admin/voice-calls/history/{bot_id}/{call_id}", dependencies=[Depends(require_admin)])
    async def detail(bot_id: int, call_id: str, request: Request) -> dict:
        try:
            return effective_status(await request.app.state.store.call("voice_call_detail", bot_id, call_id))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.websocket("/api/admin/voice-calls/connect")
    async def connect_call(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        expected = str(ws.base_url).rstrip("/").replace("wss://", "https://", 1).replace("ws://", "http://", 1)
        value = ws.cookies.get("qbot_admin", "")
        token, _, signature = value.partition(".")
        if (origin != expected or len(token) != 43 or len(signature) != 64
                or not hmac.compare_digest(sign(token), signature)):
            await ws.close(code=1008)
            return
        db = ws.app.state.store
        owner = token_hash(token)

        async def authorized() -> bool:
            return await db.call("admin_session_csrf", owner, auth_version) is not None

        if not await authorized():
            await ws.close(code=1008)
            return
        await ws.accept()
        call_id = None
        try:
            async with asyncio.timeout(5):
                raw = await ws.receive_text()
            if len(raw) > 512:
                raise ValueError("通话票据无效")
            import json
            hello = json.loads(raw)
            if not isinstance(hello, dict):
                raise ValueError("通话票据无效")
            candidate = hello.get("call_id")
            ticket = hello.get("ticket")
            if not isinstance(candidate, str) or not isinstance(ticket, str):
                raise ValueError("通话票据无效")
            async with lock:
                item = pending.get(candidate)
                if (not item or item["owner"] != owner or item["expires"] < time.monotonic()
                        or not hmac.compare_digest(item["ticket"], ticket) or active):
                    raise ValueError("通话票据过期或已使用，请重新拨号")
                del pending[candidate]
                call_id = candidate
                active[call_id] = {"owner": owner, "stop": asyncio.Event()}
            bundle = await bundle_for(db, item["bot_id"])
            call_type = RealtimeVoiceCall if bundle["config"]["mode"] == "realtime" else VoiceCall
            call = call_type(ws, db, settings, call_id, bundle, authorized)
            call.stop = active[call_id]["stop"]
            snapshot = {kind: {key: bundle[kind][key] for key in ("id", "name", "model_name")}
                        for kind in (("realtime",) if call_type is RealtimeVoiceCall else ("chat", "asr", "tts"))}
            snapshot["mode"] = bundle["config"]["mode"]
            snapshot["voice"] = bundle["config"]["realtime_voice" if call_type is RealtimeVoiceCall else "voice"]
            await db.call("create_voice_call", call_id, item["bot_id"], snapshot)
            await call.run()
        except WebSocketDisconnect:
            logger.debug("通话接通前客户端断开")
        except Exception as exc:
            logger.warning("通话接通失败：%s", type(exc).__name__)
            try:
                await ws.send_json({"type": "ended", "message": "通话接通失败，请检查配置或重新拨号"})
                await ws.close(code=1011)
            except (RuntimeError, WebSocketDisconnect, OSError):
                logger.debug("通话连接已经关闭")
        finally:
            if call_id:
                active.pop(call_id, None)
