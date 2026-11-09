"""文件索引 worker 与当前智能体范围的知识检索。"""
import asyncio
import json
import logging
import re
import sys
from importlib.resources import files

from qbot.knowledge_clients import KnowledgeError, Qdrant, embed
from qbot.knowledge_config import file_path
from qbot.timing import measure

logger = logging.getLogger(__name__)


def collection_name(bot_id: int, version: int) -> str:
    return f"qbot_kb_{bot_id}_e{version}"


async def parse_file(path) -> list[dict]:
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "qbot.knowledge_parse", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(60):
            stdout, _ = await process.communicate()
        if process.returncode or len(stdout) > 4 * 1024 * 1024:
            raise KnowledgeError("文件解析进程失败或输出超限")
        data = json.loads(stdout)
        if data.get("error"):
            raise KnowledgeError(data["error"])
        return data["chunks"]
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class KnowledgeWorker:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.vectors = Qdrant(settings)

    async def run(self) -> None:
        while True:
            try:
                # 单worker在循环边界没有活跃任务；也恢复上轮数据库结果未知的领取。
                await self.store.call("knowledge_recover")
                self.store.knowledge_wakeup.clear()
                pending = await self.store.call("knowledge_pending")
                for row in pending:
                    if row["status"] == "deleting":
                        await self.remove(row)
                    else:
                        await self.index(row)
                if pending:
                    continue
                try:
                    await asyncio.wait_for(self.store.knowledge_wakeup.wait(), 15)
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("知识库任务循环失败 error=%s", type(exc).__name__)
                await asyncio.sleep(15)

    async def remove(self, row: dict) -> None:
        try:
            async with asyncio.timeout(45):
                await self.vectors.remove(row["collection_name"], row["bot_id"], row["id"])
                path = file_path(self.settings.knowledge_storage_dir, row["bot_id"], row["id"], row["extension"])
                await asyncio.to_thread(path.unlink, missing_ok=True)
                if path.parent.exists():
                    await asyncio.to_thread(path.parent.rmdir)
                async with self.store.memory_locks[row["bot_id"]]:
                    await self.store.call("knowledge_remove", row["bot_id"], row["id"])
        except Exception as exc:
            logger.warning("知识库清理失败 bot_id=%s file_id=%s error=%s", row["bot_id"], row["id"], type(exc).__name__)
            await self.store.call("knowledge_fail", row, "清理失败，请检查存储和 Qdrant 后重试", cleanup=True)

    async def index(self, pending: dict) -> None:
        row = await self.store.call("knowledge_claim", pending["bot_id"], pending["id"])
        if row is None:
            return
        try:
            async with asyncio.timeout(600):
                cfg = await self.store.call("knowledge_config", row["bot_id"], secret=True)
                if cfg["embedding_version"] != row["embedding_version"]:
                    raise KnowledgeError("配置已变更，请重新处理")
                # 先确认向量服务可达，避免配置错误时仍先支付 Embedding 费用。
                await self.vectors.call("GET", "/collections")
                # 清理上一次失败/旧版本留下的所有点，重试不累积重复索引。
                await self.vectors.remove(row["collection_name"], row["bot_id"], row["id"])
                collection = collection_name(row["bot_id"], row["embedding_version"])
                if not await self.store.call("knowledge_set_collection", row, collection):
                    return
                path = file_path(self.settings.knowledge_storage_dir, row["bot_id"], row["id"], row["extension"])
                chunks = await parse_file(path)
                for start in range(0, len(chunks), 16):
                    current = await self.store.call("knowledge_file", row["bot_id"], row["id"])
                    latest_cfg = await self.store.call("knowledge_config", row["bot_id"])
                    if current["status"] != "processing" or latest_cfg["embedding_version"] != row["embedding_version"]:
                        raise KnowledgeError("处理期间文件或配置已变更，请重新处理")
                    # 发出前登记未知调用；断电/超时不会被记作零用量或自动重试。
                    await self.store.call("knowledge_call_usage", row, started=True)
                    vectors, tokens = await embed(cfg, [item["content"] for item in chunks[start:start + 16]])
                    if tokens is not None:
                        await self.store.call("knowledge_call_usage", row, tokens)
                    if start == 0:
                        await self.vectors.ensure(collection, len(vectors[0]))
                    await self.vectors.upsert(collection, row, start, vectors)
                async with self.store.memory_locks[row["bot_id"]]:
                    applied = await self.store.call("knowledge_finish", row, chunks)
                if not applied:
                    raise KnowledgeError("处理结果已失效，请重新处理")
        except asyncio.CancelledError:
            # 留 processing 供下一次启动标记失败，避免后台重复付费。
            raise
        except Exception as exc:
            logger.warning("知识库索引失败 bot_id=%s file_id=%s error=%s", row["bot_id"], row["id"], type(exc).__name__)
            message = str(exc) if isinstance(exc, KnowledgeError) else "文件处理失败或超时，请检查服务后手动重试"
            await self.store.call("knowledge_fail", row, message)


async def retrieve(settings, store, bot_id: int, query: str, config: dict,
                   metrics: dict | None = None) -> dict:
    result = {"status": "empty", "items": [], "mode": config["mode"], "embedding_tokens": None}
    try:
        async with asyncio.timeout(5):
            with measure(metrics, "knowledge_retrieval"):
                ids = await store.call("knowledge_ready_ids", bot_id, config["embedding_version"])
                if not ids:
                    return result
                cfg = await store.call("knowledge_config", bot_id, secret=True)
                if cfg["embedding_version"] != config["embedding_version"]:
                    raise KnowledgeError("知识库配置已变化")
                vectors, tokens = await embed(cfg, [query])
                result["embedding_tokens"] = tokens
                hits = await Qdrant(settings).search(collection_name(bot_id, cfg["embedding_version"]), bot_id, ids, vectors[0])
                used = 0
                seen = set()
                for hit in hits:
                    payload = hit.get("payload") or {}
                    if payload.get("bot_id") != bot_id or payload.get("file_id") not in ids:
                        continue
                    if type(payload.get("generation")) is not int or type(payload.get("ordinal")) is not int:
                        continue
                    item = await store.call("knowledge_chunk", bot_id, payload["file_id"], payload["generation"],
                                            payload["ordinal"], cfg["embedding_version"])
                    if not item or item["content"] in seen:
                        continue
                    size = len(json.dumps({"资料编号": len(result["items"]) + 1, "文件": item["filename"],
                        "位置": item["location"], "正文": item["content"]}, ensure_ascii=False).encode("utf-8"))
                    if used + size > 6000:
                        continue
                    used += size
                    seen.add(item["content"])
                    result["items"].append({**item, "file_id": payload["file_id"], "score": hit.get("score")})
                result["status"] = "ok" if result["items"] else "empty"
    except asyncio.CancelledError:
        result.update(status="cancelled", items=[])
        raise
    except Exception as exc:
        logger.warning("知识检索失败 bot_id=%s error=%s", bot_id, type(exc).__name__)
        result.update(status="failed", items=[], error="资料暂时不可用，请稍后再试")
    finally:
        if metrics is not None:
            metrics["knowledge"] = {"status": result["status"], "embedding_tokens": result["embedding_tokens"],
                "sources": [{"file_id": v["file_id"], "location": v["location"]} for v in result["items"]]}
    return result


def knowledge_messages(result: dict) -> list[str]:
    return [json.dumps({"资料编号": i + 1, "文件": item["filename"], "位置": item["location"],
                        "正文": item["content"]}, ensure_ascii=False) for i, item in enumerate(result["items"])]


def knowledge_rules(mode: str) -> str:
    rules = files("qbot").joinpath("prompts/knowledge_use.txt").read_text("utf-8")
    if mode == "strict":
        rules += "\n本轮仅依据提供的知识资料回答。资料不足以回答时明确说明，不使用常识、联网或工具补出答案。"
    return rules


def cite_answer(answer: str, selected_messages: list, limit: int, mode: str) -> str:
    """仅允许实际进入预算的引用，来源由服务器补齐并预留回复空间。"""
    sources = {}
    for message in selected_messages:
        item = json.loads(message.content)
        sources[item["资料编号"]] = item
    wanted = list(dict.fromkeys(int(value) for value in re.findall(r"\[资料(\d+)\]", answer)))
    chosen, lines = {}, []
    for number in wanted:
        if number not in sources:
            continue
        item = sources[number]
        title = " ".join(item["文件"].split())[:40]
        location = " ".join(item["位置"].split())[:40]
        line = f"[{number}] {title} · {location}"
        if not lines:
            line = line[:max(16, limit // 2 - 5)]
        if len("\n".join(lines + [line])) + 5 <= limit // 2:
            lines.append(line)
            chosen[number] = item
    if mode == "strict" and not chosen:
        return "现有资料不足以给出可核对的答案，请补充问题或资料。"
    answer = re.sub(r"\[资料(\d+)\]", lambda m: f"[{m[1]}]" if int(m[1]) in chosen else "", answer).strip()
    suffix = "\n来源：\n" + "\n".join(lines) if lines else ""
    room = limit - len(suffix)
    if len(answer) > room:
        answer = answer[:max(0, room - 8)] + "\n（内容已截断）"
    return answer + suffix
