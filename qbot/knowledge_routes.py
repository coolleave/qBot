"""文件知识库管理；所有路由要求管理员，写请求沿用 CSRF。"""
import asyncio
import hashlib
import logging
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from qbot.knowledge import retrieve
from qbot.knowledge_config import EXTENSIONS, MAX_FILE_BYTES, KnowledgeConfig, file_path

logger = logging.getLogger(__name__)


async def disk_call(function, *args, **kwargs):
    """取消上传时先等当前磁盘操作完成，不能在线程写入中关闭/删除文件。"""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except OSError:
            logger.warning("上传取消时磁盘操作失败")
        raise


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)


def install_knowledge_routes(router, settings, require_admin) -> None:
    # 单进程限制上传和试用并发，不占用机器人聊天锁等待请求体。
    upload_lock = asyncio.Lock()
    search_lock = asyncio.Lock()

    async def invoke(request, method: str, bot_id: int, *args, write=False):
        db = request.app.state.store
        try:
            if write:
                async with db.memory_locks[bot_id]:
                    result = await db.call(method, bot_id, *args)
                db.knowledge_wakeup.set()
                return result
            return await db.call(method, bot_id, *args)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/api/admin/knowledge/{bot_id}/config", dependencies=[Depends(require_admin)])
    async def config(bot_id: int, request: Request):
        return await invoke(request, "knowledge_config", bot_id)

    @router.put("/api/admin/knowledge/{bot_id}/config", dependencies=[Depends(require_admin)])
    async def save_config(bot_id: int, data: KnowledgeConfig, request: Request):
        await invoke(request, "save_knowledge_config", bot_id, data.model_dump(), write=True)
        return {"saved": True}

    @router.get("/api/admin/knowledge/{bot_id}/files", dependencies=[Depends(require_admin)])
    async def list_files(bot_id: int, request: Request, page: int = Query(default=1, ge=1, le=10000)):
        return await invoke(request, "knowledge_list_files", bot_id, page)

    @router.post("/api/admin/knowledge/{bot_id}/files", dependencies=[Depends(require_admin)])
    async def upload(bot_id: int, request: Request, filename: str = Query(min_length=1, max_length=200)):
        filename = filename.strip()
        if not filename or any(ord(c) < 32 or c in '/\\' for c in filename):
            raise HTTPException(400, "文件名不合法")
        extension = Path(filename).suffix.lower()
        if extension not in EXTENSIONS:
            raise HTTPException(400, "支持 PDF、DOCX、XLSX、TXT、MD、CSV")
        if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
            raise HTTPException(415, "请使用二进制文件上传")
        length = request.headers.get("content-length")
        if length and (not length.isdecimal() or int(length) > MAX_FILE_BYTES):
            raise HTTPException(413, "单文件最多50 MB")
        await invoke(request, "knowledge_config", bot_id)
        if upload_lock.locked():
            raise HTTPException(429, "有文件正在上传，请稍后重试")
        async with upload_lock:
            fid = str(uuid4())
            path = file_path(settings.knowledge_storage_dir, bot_id, fid, extension)
            temporary = path.with_suffix(".part")
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            size, digest = 0, hashlib.sha256()
            try:
                async with asyncio.timeout(120):
                    with temporary.open("xb") as stream:
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > MAX_FILE_BYTES:
                                raise HTTPException(413, "单文件最多50 MB")
                            digest.update(chunk)
                            await disk_call(stream.write, chunk)
                    if not size:
                        raise HTTPException(400, "不能上传空文件")
                    await disk_call(temporary.replace, path)
                try:
                    await invoke(request, "knowledge_add_file", bot_id, dict(id=fid, filename=filename,
                        extension=extension, sha256=digest.hexdigest(), size_bytes=size), write=True)
                except HTTPException:
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                    raise
                return {"id": fid, "status": "queued"}
            except TimeoutError:
                raise HTTPException(408, "上传超时，请重试") from None
            except OSError:
                raise HTTPException(503, "文件写入失败，请检查磁盘空间及目录权限") from None
            finally:
                # 数据库结果未知时保留正式文件，不能误删可能已入账的上传。
                try:
                    await disk_call(temporary.unlink, missing_ok=True)
                    if not path.exists():
                        await disk_call(path.parent.rmdir)
                except OSError:
                    logger.warning("上传临时文件清理失败 bot_id=%s file_id=%s", bot_id, fid)

    @router.get("/api/admin/knowledge/{bot_id}/files/{file_id}/download", dependencies=[Depends(require_admin)])
    async def download(bot_id: int, file_id: UUID, request: Request):
        row = await invoke(request, "knowledge_file", bot_id, str(file_id))
        if row["status"] in ("deleting", "cleanup_failed"):
            raise HTTPException(404, "文件已进入删除流程")
        path = file_path(settings.knowledge_storage_dir, bot_id, str(file_id), row["extension"])
        if not path.is_file():
            raise HTTPException(404, "原文件不存在")
        return FileResponse(path, filename=row["filename"], media_type="application/octet-stream")

    @router.post("/api/admin/knowledge/{bot_id}/files/{file_id}/retry", dependencies=[Depends(require_admin)])
    async def retry(bot_id: int, file_id: UUID, request: Request):
        await invoke(request, "knowledge_file_action", bot_id, str(file_id), "retry", write=True)
        return {"queued": True}

    @router.delete("/api/admin/knowledge/{bot_id}/files/{file_id}", dependencies=[Depends(require_admin)])
    async def delete(bot_id: int, file_id: UUID, request: Request):
        await invoke(request, "knowledge_file_action", bot_id, str(file_id), "delete", write=True)
        return {"deleting": True}

    @router.post("/api/admin/knowledge/{bot_id}/search", dependencies=[Depends(require_admin)])
    async def search(bot_id: int, data: SearchInput, request: Request):
        if not data.query.strip():
            raise HTTPException(400, "请输入检索问题")
        if search_lock.locked():
            raise HTTPException(429, "正在检索，请稍后再试")
        async with search_lock:
            async with request.app.state.store.memory_locks[bot_id]:
                cfg = await invoke(request, "knowledge_config", bot_id)
                return await retrieve(settings, request.app.state.store, bot_id, data.query.strip(), cfg)
