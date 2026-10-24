"""单进程机器人生命周期管理，每个机器人独立队列与模型上下文。"""
import asyncio
import logging
from contextlib import suppress

import httpx
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError

from qbot.chat import ChatService
from qbot.gateway import Gateway
from qbot.model import create_model
from qbot.qq import QQClient
from qbot.recording import RecordedChat

logger = logging.getLogger(__name__)


class BotRuntime:
    def __init__(self, settings, store, config: dict, chat=None, qq=None):
        from qbot.server import Dispatcher
        self.bot_id = config["id"]
        self.settings = settings.model_copy(deep=True)
        self.settings.qq_app_id = config["app_id"] or ""
        self.settings.qq_app_secret = SecretStr(config["app_secret"])
        self.settings.qq_transport = config["transport"]
        active_chat = chat or (RecordedChat(settings, store, self.bot_id, config.get("system_prompt"))
                              if store else ChatService(settings, create_model(settings)))
        self.client = httpx.AsyncClient()
        self.dispatcher = Dispatcher(settings, active_chat, qq or QQClient(self.settings, self.client), store, self.bot_id)
        self.worker = None
        self.gateway = None
        self.gateway_task = None
        self.accepting = False

    async def start(self) -> None:
        self.worker = asyncio.create_task(self.dispatcher.run())
        self.accepting = True
        if self.settings.qq_transport == "websocket":
            self.gateway = Gateway(self.dispatcher.qq, self.receive)
            self.gateway_task = asyncio.create_task(self.gateway.run())

    async def receive(self, event: dict) -> None:
        from qbot.server import incoming_message
        if not self.accepting or event.get("t") not in ("GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"):
            return
        try:
            await self.dispatcher.accept(incoming_message(event["t"], event["d"]))
        except (ValidationError, KeyError):
            logger.warning("机器人 %s 消息格式无效，已跳过", self.bot_id)
        except HTTPException as exc:
            if exc.status_code == 503:
                raise
            logger.warning("机器人 %s 消息被拒绝，状态码：%s", self.bot_id, exc.status_code)

    async def stop(self) -> None:
        self.accepting = False
        if self.gateway_task:
            self.gateway_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.gateway_task
        if self.worker:
            # 正在执行的模型有总超时。优先让已有消息完成，不把旧凭证队列交给新机器人。
            try:
                await asyncio.wait_for(self.dispatcher.queue.join(), timeout=10)
            except TimeoutError:
                logger.warning("机器人 %s 停止时仍有待处理消息，队列不会自动重放", self.bot_id)
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        await self.client.aclose()

    def status(self) -> str:
        if self.worker and self.worker.done():
            return "处理任务已停止，请重新启动"
        return self.gateway.status if self.gateway else "等待 Webhook 回调"


class BotManager:
    def __init__(self, settings, store, chat=None, qq=None):
        self.settings, self.store = settings, store
        self.chat, self.qq = chat, qq
        self.runtimes: dict[int, BotRuntime] = {}
        self.errors: dict[int, str] = {}
        self.lock = asyncio.Lock()

    async def start_all(self) -> None:
        if self.store:
            for bot in await self.store.call("list_bots"):
                if bot["enabled"]:
                    await self.apply(bot["id"])
        else:
            config = dict(id=1, app_id=self.settings.qq_app_id,
                          app_secret=self.settings.qq_app_secret.get_secret_value(),
                          transport=self.settings.qq_transport)
            runtime = BotRuntime(self.settings, None, config, self.chat, self.qq)
            await runtime.start()
            self.runtimes[1] = runtime

    async def apply(self, bot_id: int) -> dict:
        async with self.lock:
            entries = await self.store.call("list_bots")
            saved = next((entry for entry in entries if entry["id"] == bot_id), None)
            if saved is None:
                raise ValueError("机器人不存在")
            if not saved["enabled"]:
                old = self.runtimes.pop(bot_id, None)
                if old:
                    await old.stop()
                self.errors.pop(bot_id, None)
                return {"running": False, "status": "已停止"}
            try:
                config = await self.store.call("bot_config", bot_id)
            except ValueError:
                self.errors[bot_id] = "配置加载失败，请检查机器人凭证和加密主密钥"
                return {"running": bot_id in self.runtimes, "status": self.errors[bot_id]}
            old = self.runtimes.pop(bot_id, None)
            if old:
                await old.stop()
            self.errors.pop(bot_id, None)
            if not config["enabled"]:
                return {"running": False, "status": "已停止"}
            runtime = None
            try:
                if not config["app_id"] or not config["app_secret"]:
                    raise ValueError("机器人凭证未配置")
                runtime = BotRuntime(self.settings, self.store, config)
                await runtime.start()
                self.runtimes[bot_id] = runtime
                return {"running": True, "status": runtime.status()}
            except Exception as exc:
                if runtime:
                    await runtime.stop()
                logger.error("机器人 %s 启动失败：%s", bot_id, type(exc).__name__)
                self.errors[bot_id] = "启动失败，请检查凭证和配置后重试"
                return {"running": False, "status": self.errors[bot_id]}

    async def close(self) -> None:
        async with self.lock:
            for runtime in list(self.runtimes.values()):
                await runtime.stop()
            self.runtimes.clear()

    def state(self, bot_id: int) -> dict:
        runtime = self.runtimes.get(bot_id)
        return {"running": bool(runtime), "status": runtime.status() if runtime else self.errors.get(bot_id, "已停止")}

    async def accept(self, bot_id: int, runtime: BotRuntime, message) -> None:
        async with self.lock:
            if self.runtimes.get(bot_id) is not runtime or not runtime.accepting:
                raise HTTPException(503, "机器人正在重启，请稍后重试")
            await runtime.dispatcher.accept(message)
