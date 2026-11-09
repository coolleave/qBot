"""Embedding 和 Qdrant HTTP 协议；有界响应、无重试、不跟随重定向。"""
import json
import math
from uuid import NAMESPACE_URL, uuid5

import httpx


class KnowledgeError(RuntimeError):
    pass


async def request_json(method: str, url: str, *, headers: dict, data: dict | None = None,
                       timeout: float = 20, missing_ok: bool = False) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            async with client.stream(method, url, headers=headers, json=data) as response:
                if missing_ok and response.status_code == 404:
                    return None
                if not 200 <= response.status_code < 300:
                    raise KnowledgeError(f"外部服务返回 HTTP {response.status_code}，请检查配置与额度")
                body = bytearray()
                async for piece in response.aiter_bytes():
                    body.extend(piece)
                    if len(body) > 8 * 1024 * 1024:
                        raise KnowledgeError("外部服务响应超过8 MB")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise KnowledgeError("外部服务响应格式不正确")
                return result
    except (httpx.HTTPError, ValueError) as exc:
        raise KnowledgeError("外部服务连接失败或响应无效，请检查地址及网络") from None


async def embed(config: dict, texts: list[str]) -> tuple[list[list[float]], int | None]:
    if not (config["base_url"] and config["model_name"] and config.get("api_key")):
        raise KnowledgeError("Embedding 配置不完整")
    result = await request_json("POST", config["base_url"] + "/embeddings",
        headers={"Authorization": "Bearer " + config["api_key"]},
        data={"model": config["model_name"], "input": texts, "encoding_format": "float"})
    data = result.get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise KnowledgeError("Embedding 返回数量不匹配")
    vectors = [None] * len(texts)
    for item in data:
        index = item.get("index") if isinstance(item, dict) else None
        vector = item.get("embedding") if isinstance(item, dict) else None
        if (type(index) is not int or not 0 <= index < len(texts) or vectors[index] is not None
                or not isinstance(vector, list) or not 1 <= len(vector) <= 8192
                or not all(type(v) in (int, float) and math.isfinite(v) for v in vector)
                or not any(v != 0 for v in vector)):
            raise KnowledgeError("Embedding 向量或序号不合法")
        vectors[index] = vector
    if len({len(v) for v in vectors}) != 1:
        raise KnowledgeError("Embedding 向量维度不一致")
    usage = result.get("usage") or {}
    tokens = usage.get("total_tokens", usage.get("prompt_tokens")) if isinstance(usage, dict) else None
    return vectors, tokens if type(tokens) is int and tokens >= 0 else None


class Qdrant:
    def __init__(self, settings):
        self.base = settings.knowledge_qdrant_url.rstrip("/")
        key = settings.knowledge_qdrant_api_key.get_secret_value()
        self.headers = {"api-key": key} if key else {}

    async def call(self, method: str, path: str, data: dict | None = None, *, missing_ok=False):
        return await request_json(method, self.base + path, headers=self.headers, data=data, missing_ok=missing_ok)

    async def ensure(self, collection: str, size: int) -> None:
        path = "/collections/" + collection
        current = await self.call("GET", path, missing_ok=True)
        if current is None:
            await self.call("PUT", path, {"vectors": {"size": size, "distance": "Cosine"}})
            await self.call("PUT", path + "/index?wait=true", {"field_name": "bot_id", "field_schema": "integer"})
            await self.call("PUT", path + "/index?wait=true", {"field_name": "file_id", "field_schema": "keyword"})
        else:
            vectors = current.get("result", {}).get("config", {}).get("params", {}).get("vectors", {})
            if vectors.get("size") != size or vectors.get("distance") != "Cosine":
                raise KnowledgeError("已有索引的向量维度不匹配，请重新保存 Embedding 配置并重建")

    async def remove(self, collection: str, bot_id: int, file_id: str) -> None:
        if collection:
            await self.call("POST", f"/collections/{collection}/points/delete?wait=true", {"filter": {"must": [
                {"key": "bot_id", "match": {"value": bot_id}},
                {"key": "file_id", "match": {"value": file_id}}]}}, missing_ok=True)

    async def upsert(self, collection: str, row: dict, start: int, vectors: list[list[float]]) -> None:
        points = [{"id": str(uuid5(NAMESPACE_URL, f"qbot:{row['bot_id']}:{row['id']}:{row['generation']}:{start + i}")),
                   "vector": vector, "payload": {"bot_id": row["bot_id"], "file_id": row["id"],
                   "generation": row["generation"], "ordinal": start + i}} for i, vector in enumerate(vectors)]
        await self.call("PUT", f"/collections/{collection}/points?wait=true", {"points": points})

    async def search(self, collection: str, bot_id: int, file_ids: list[str], vector: list[float]) -> list[dict]:
        response = await self.call("POST", f"/collections/{collection}/points/search", {
            "vector": vector, "limit": 5, "with_payload": True, "with_vector": False,
            "score_threshold": 0.3, "filter": {"must": [
                {"key": "bot_id", "match": {"value": bot_id}},
                {"key": "file_id", "match": {"any": file_ids}}]}})
        result = response.get("result")
        if not isinstance(result, list):
            raise KnowledgeError("Qdrant 检索响应格式不正确")
        return result
