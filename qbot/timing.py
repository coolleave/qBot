"""单调时钟阶段计时；异常照常传播，只记录耗时和状态。"""
import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager


@contextmanager
def measure(details: dict | None, stage: str) -> Iterator[None]:
    started = time.monotonic()
    status = "success"
    try:
        yield
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        raise
    finally:
        if details is not None:
            timings = details.setdefault("timings", {})
            previous = timings.get(stage, {})
            timings[stage] = {
                "elapsed_ms": previous.get("elapsed_ms", 0) + round((time.monotonic() - started) * 1000),
                "status": status if status != "success" else previous.get("status", status),
            }


@asynccontextmanager
async def measured_slot(slot: asyncio.Semaphore, details: dict, stage: str) -> AsyncIterator[None]:
    with measure(details, stage):
        await slot.acquire()
    try:
        yield
    finally:
        slot.release()
