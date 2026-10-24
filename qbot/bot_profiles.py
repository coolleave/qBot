"""官方机器人资料短期缓存，不持久化密钥，也不影响消息处理。"""
import asyncio
import hashlib
import logging
import time

import httpx
from pydantic import SecretStr

from qbot.qq import QQClient

logger = logging.getLogger(__name__)


class BotProfiles:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.cache: dict[int, tuple[str, float, dict]] = {}
        self.locks: dict[int, asyncio.Lock] = {}

    async def get(self, bot_id: int, refresh: bool = False) -> dict:
        # 先查存在性，避免任意 ID 生成无限锁。
        config = await self.store.call("bot_config", bot_id)
        async with self.locks.setdefault(bot_id, asyncio.Lock()):
            identity = hashlib.sha256(((config["app_id"] or "") + ":" + config["app_secret"]).encode()).hexdigest()
            cached = self.cache.get(bot_id)
            if cached and cached[0] == identity:
                ttl = 10 if refresh else (300 if cached[2]["success"] else 30)
                if time.monotonic() - cached[1] < ttl:
                    return cached[2]
            result = {"success": False, "profile": None, "message": "官方资料暂不可用，请检查凭证或稍后刷新"}
            if not config["app_id"] or not config["app_secret"]:
                result["message"] = "请先保存 AppID 和 AppSecret"
            else:
                settings = self.settings.model_copy(update={"qq_app_id": config["app_id"],
                    "qq_app_secret": SecretStr(config["app_secret"])})
                try:
                    async with asyncio.timeout(20):
                        async with httpx.AsyncClient() as client:
                            profile = await QQClient(settings, client).profile()
                    result = {"success": True, "profile": profile, "message": "官方资料已获取"}
                except Exception as exc:
                    logger.warning("机器人 %s 官方资料获取失败：%s", bot_id, type(exc).__name__)
            self.cache[bot_id] = (identity, time.monotonic(), result)
            return result
