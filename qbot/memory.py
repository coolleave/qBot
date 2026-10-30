"""单进程异步整理器；只在后台明确启用后调用独立模型。"""
import asyncio
import logging
from importlib.resources import files

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser

from qbot.memory_config import MemoryResult, json_text
from qbot.model import provider_model
from qbot.token_usage import extract_token_usage

logger = logging.getLogger(__name__)


class MemoryWorker:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.prompt = files("qbot").joinpath("prompts/memory.txt").read_text("utf-8")

    async def run(self) -> None:
        recovered = False
        while True:
            try:
                if not recovered:
                    await self.store.call("memory_recover")
                    recovered = True
                for result in await self.store.call("memory_ready_results"):
                    await self.apply(result["bot_id"], result["id"])
                candidates = await self.store.call("memory_candidates")
                for item in candidates:
                    try:
                        await self.process(item["bot_id"], item["conversation_id"])
                    except Exception as exc:
                        logger.warning("记忆整理范围处理失败 bot_id=%s error=%s", item["bot_id"], type(exc).__name__)
                self.store.memory_wakeup.clear()
                try:
                    await asyncio.wait_for(self.store.memory_wakeup.wait(), timeout=60)
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("记忆整理循环失败 error=%s", type(exc).__name__)
                await asyncio.sleep(60)

    async def apply(self, bot_id: int, run_id: int) -> None:
        try:
            async with self.store.memory_locks[bot_id]:
                await self.store.call("apply_memory_result", bot_id, run_id)
        except ValueError:
            # 结构/归属错误不再次付费请求；保留已经记录的实际用量。
            await self.store.call("memory_reject_result", bot_id, run_id)

    async def process(self, bot_id: int, cid: int, *, force: bool = False) -> bool:
        batch = await self.store.call("prepare_memory_batch", bot_id, cid, self.prompt, force=force)
        if not batch:
            return False
        client = None
        run_id = None
        sent = False
        usage = None
        result_saved = False
        try:
            provider = await self.store.call("capability_provider", batch["provider_id"], "chat")
            config = self.settings.model_copy(update={"model_max_retries": 0, "model_timeout_seconds": 30,
                                                     "model_max_tokens": 800})
            client = provider_model(config, provider)
            run_id = await self.store.call("reserve_memory_run", batch, provider["model_name"])
            if run_id is None:
                return False
            if not await self.store.call("memory_start_call", bot_id, run_id):
                return False
            sent = True
            async with asyncio.timeout(30):
                output = await client.ainvoke([SystemMessage(self.prompt), HumanMessage(json_text(batch["payload"]))])
            usage = extract_token_usage(output)
            # 不发起额外的 JSON 修复调用，格式错误也保留实际消耗。
            result = MemoryResult.model_validate_json(StrOutputParser().invoke(output))
            await self.store.call("memory_finish_call", bot_id, run_id,
                                  result=result.model_dump(mode="json"), usage=usage)
            result_saved = True
            await self.apply(bot_id, run_id)
            return True
        except asyncio.CancelledError:
            if run_id and not result_saved:
                try:
                    await asyncio.shield(self.store.call("memory_finish_call", bot_id, run_id, usage=usage,
                        error="整理中断，未自动重试", unknown=sent, not_sent=not sent))
                except Exception as exc:
                    logger.warning("记忆中断状态保存失败 run_id=%s error=%s", run_id, type(exc).__name__)
            raise
        except Exception as exc:
            if result_saved:
                # 保留 result 状态，下轮只提交数据库；不覆盖已持久化候选。
                logger.warning("记忆候选提交失败，等待恢复 run_id=%s error=%s", run_id, type(exc).__name__)
                return False
            known_failure = usage is not None or getattr(exc, "status_code", None) is not None
            if run_id:
                await self.store.call("memory_finish_call", bot_id, run_id, usage=usage,
                    error="整理结果无效或请求失败，需人工处理" if known_failure else "调用结果未知，未自动重试",
                    unknown=sent and not known_failure, not_sent=not sent)
            if not sent or getattr(exc, "status_code", None) in (400, 401, 403, 404, 429):
                await self.store.call("memory_pause", bot_id, "整理模型不可用，请检查配置与额度后重新保存设置")
            logger.warning("记忆模型整理失败 bot_id=%s run_id=%s error=%s", bot_id, run_id, type(exc).__name__)
            return False
        finally:
            if client:
                try:
                    async with asyncio.timeout(5):
                        await client.root_async_client.close()
                        await asyncio.to_thread(client.root_client.close)
                except Exception as exc:
                    logger.warning("记忆模型连接关闭失败 error=%s", type(exc).__name__)
