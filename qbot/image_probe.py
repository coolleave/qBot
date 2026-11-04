"""有界内存异步试生图任务，不持久化提示词、凭证或图片。"""
import asyncio
import hashlib
import logging
import time
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from qbot.image_generation import generate_image

logger = logging.getLogger(__name__)


class ImageProbeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    prompt: str = Field(min_length=1, max_length=2000)
    request_id: UUID


def image_probe_router(require_admin) -> APIRouter:
    jobs: dict[str, dict] = {}
    tasks: set[asyncio.Task] = set()
    lock = asyncio.Lock()
    last_started = 0.0

    def prune() -> None:
        now = time.monotonic()
        for key, job in list(jobs.items()):
            if job["status"] != "running" and now - job["finished"] >= 900:
                del jobs[key]

    async def cleanup() -> None:
        while True:
            await asyncio.sleep(60)
            prune()

    @asynccontextmanager
    async def lifespan(app):
        janitor = asyncio.create_task(cleanup())
        try:
            yield
        finally:
            pending = [janitor, *tasks]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            jobs.clear()

    router = APIRouter(lifespan=lifespan)

    def owner_key(token: str, request_id: UUID) -> str:
        return hashlib.sha256((token + str(request_id)).encode()).hexdigest()

    def snapshot(job: dict) -> dict:
        return {key: job[key] for key in ("id", "status", "error")}

    def find(token: str, provider_id: int, request_id: UUID) -> dict:
        prune()
        job = jobs.get(owner_key(token, request_id))
        if job is None or job["provider_id"] != provider_id:
            raise HTTPException(404, "任务不存在或已过期；服务重启后任务会失效")
        return job

    async def run(job: dict, provider: dict, prompt: str) -> None:
        try:
            job["media"] = await generate_image(provider, prompt, timeout_seconds=180)
            job["status"] = "succeeded"
        except asyncio.CancelledError:
            job.update(status="failed", error="服务停止，生成结果未知，请勿直接重复提交")
            raise
        except Exception as exc:
            # 不回传供应商原始异常，避免携带密钥或完整响应。
            logger.warning("异步试生图失败 provider_id=%s error=%s", job["provider_id"], type(exc).__name__)
            job.update(status="failed", error="生成失败或超时，请检查接口、模型和额度；上游可能已计费，不会自动重试")
        finally:
            job["finished"] = time.monotonic()

    @router.post("/api/admin/providers/{provider_id}/image-jobs", status_code=202)
    async def submit(provider_id: int, data: ImageProbeInput, request: Request,
                     token: str = Depends(require_admin)) -> dict:
        nonlocal last_started
        key = owner_key(token, data.request_id)
        fingerprint = hashlib.sha256(data.prompt.encode()).hexdigest()
        async with lock:
            prune()
            if key in jobs:
                job = jobs[key]
                if job["provider_id"] != provider_id or job["fingerprint"] != fingerprint:
                    raise HTTPException(409, "请求标识已用于其他生图请求")
                return snapshot(job)
            running = [job for job in jobs.values() if job["status"] == "running"]
            if len(running) >= 2 or any(job["provider_id"] == provider_id for job in running) or time.monotonic() - last_started < 10:
                raise HTTPException(429, "生图正在进行或提交过于频繁，请稍后重试")
            if len(jobs) >= 8:
                raise HTTPException(429, "试用任务已达上限，请在结果过期后重试")
            try:
                provider = await request.app.state.store.call("provider_for_probe", provider_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            if provider["model_type"] != "image" or provider["api_type"] != "openai_compatible":
                raise HTTPException(400, "请选择兼容接口的图片生成模型")
            job = {"id": str(data.request_id), "provider_id": provider_id, "fingerprint": fingerprint,
                   "status": "running", "error": None, "finished": 0.0, "media": None}
            jobs[key] = job
            last_started = time.monotonic()
            task = asyncio.create_task(run(job, provider, data.prompt))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return snapshot(job)

    @router.get("/api/admin/providers/{provider_id}/image-jobs/{request_id}")
    async def status(provider_id: int, request_id: UUID, response: Response,
                     token: str = Depends(require_admin)) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return snapshot(find(token, provider_id, request_id))

    @router.get("/api/admin/providers/{provider_id}/image-jobs/{request_id}/result")
    async def result(provider_id: int, request_id: UUID, token: str = Depends(require_admin)) -> Response:
        job = find(token, provider_id, request_id)
        if job["status"] != "succeeded":
            raise HTTPException(409, "图片尚未生成成功")
        media = job["media"]
        return Response(media.data, media_type="image/png" if media.extension == ".png" else "image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    return router
