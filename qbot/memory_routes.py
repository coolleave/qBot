"""记忆管理接口，全部复用管理员认证和 CSRF。"""
from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from qbot.memory_config import MemoryConfig, MemoryInput


class MemoryAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: int = Field(gt=0)


class MemoryBackfill(MemoryAction):
    after_id: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class MemoryRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept_possible_charge: bool = Field(default=False, strict=True)


def install_memory_routes(router, require_admin) -> None:
    async def invoke(request, method, bot_id, *args, write=False):
        db = request.app.state.store
        try:
            if write:
                async with db.memory_locks[bot_id]:
                    value = await db.call(method, bot_id, *args)
                db.memory_wakeup.set()
                return value
            return await db.call(method, bot_id, *args)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/api/admin/memory/{bot_id}/config", dependencies=[Depends(require_admin)])
    async def config(bot_id: int, request: Request):
        return await invoke(request, "memory_config", bot_id)

    @router.put("/api/admin/memory/{bot_id}/config", dependencies=[Depends(require_admin)])
    async def save_config(bot_id: int, data: MemoryConfig, request: Request):
        await invoke(request, "save_memory_config", bot_id, data.model_dump(), write=True)
        return {"saved": True}

    @router.get("/api/admin/memory/{bot_id}/conversations", dependencies=[Depends(require_admin)])
    async def conversations(bot_id: int, request: Request):
        return {"items": await invoke(request, "memory_conversations", bot_id)}

    @router.get("/api/admin/memory/{bot_id}/items", dependencies=[Depends(require_admin)])
    async def items(bot_id: int, request: Request, conversation_id: int | None = Query(default=None, gt=0),
                    status: str = Query(default="active", pattern="^(active|pending|superseded|forgotten)?$"),
                    page: int = Query(default=1, ge=1, le=100000), query: str = Query(default="", max_length=100)):
        return await invoke(request, "list_memory_items", bot_id, conversation_id, status, page, query)

    @router.post("/api/admin/memory/{bot_id}/items", dependencies=[Depends(require_admin)])
    async def add(bot_id: int, data: MemoryInput, request: Request):
        return {"id": await invoke(request, "save_memory_item", bot_id, data.model_dump(mode="json"), write=True)}

    @router.put("/api/admin/memory/{bot_id}/items/{item_id}", dependencies=[Depends(require_admin)])
    async def edit(bot_id: int, item_id: int, data: MemoryInput, request: Request):
        return {"id": await invoke(request, "save_memory_item", bot_id, data.model_dump(mode="json"), item_id, write=True)}

    @router.delete("/api/admin/memory/{bot_id}/items/{item_id}", dependencies=[Depends(require_admin)])
    async def forget(bot_id: int, item_id: int, request: Request):
        await invoke(request, "forget_memory_item", bot_id, item_id, write=True)
        return {"forgotten": True, "history_reset": True}

    @router.get("/api/admin/memory/{bot_id}/status", dependencies=[Depends(require_admin)])
    async def status(bot_id: int, request: Request):
        return await invoke(request, "memory_dashboard", bot_id)

    @router.post("/api/admin/memory/{bot_id}/organize", dependencies=[Depends(require_admin)])
    async def organize(bot_id: int, data: MemoryAction, request: Request):
        await invoke(request, "request_memory_run", bot_id, data.conversation_id, write=True)
        return {"queued": True, "message": "已请求整理，仍受冷却、预算和可用来源限制"}

    @router.post("/api/admin/memory/{bot_id}/backfill", dependencies=[Depends(require_admin)])
    async def backfill(bot_id: int, data: MemoryBackfill, request: Request):
        count = await invoke(request, "backfill_memory", bot_id, data.conversation_id, data.after_id, data.limit, write=True)
        return {"queued_sources": count}

    @router.post("/api/admin/memory/{bot_id}/runs/{run_id}/retry", dependencies=[Depends(require_admin)])
    async def retry(bot_id: int, run_id: int, data: MemoryRetry, request: Request):
        if not data.accept_possible_charge:
            raise HTTPException(400, "请确认重新调用可能重复计费")
        await invoke(request, "retry_memory_run", bot_id, run_id, write=True)
        return {"queued": True}
