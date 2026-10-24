import asyncio
import logging
import uuid

from qbot.chat import ChatService, Sender, SessionKey
from qbot.config import Settings
from qbot.model import provider_model
from qbot.qq import QQDeliveryUnknown
from qbot.storage import Store, StorageError

logger = logging.getLogger(__name__)


class RecordedChat:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store
        self.chat = ChatService(settings, None)

    async def reply(self, key: SessionKey, text: str, sender: Sender | None = None,
                    *, record_id: int | None = None) -> str:
        if record_id is None:
            record_id, _ = await self.store.call("receive", "local", "local", key[1], str(uuid.uuid4()), text)
        status, answer, send_started, delivered = "processing", None, False, False
        model = None
        try:
            await self.store.call("update_record", record_id, status="processing")
            if text.strip() and text.strip() != "/reset" and len(text.strip()) <= self.settings.max_input_chars:
                provider = await self.store.call("active_provider")
                await self.store.call("update_record", record_id,
                    provider_id=provider["id"] if provider else None,
                    provider_name_snapshot=provider["name"] if provider else "DeepSeek (.env)",
                    model_name_snapshot=provider["model_name"] if provider else self.settings.deepseek_model)
                model = provider_model(self.settings, provider)

            async def deliver(value: str) -> None:
                nonlocal answer, send_started, delivered
                answer = value
                # 先存生成结果，再发送；若存储失败则不发送，防止误报成功。
                await self.store.call("update_record", record_id, answer=value)
                if sender:
                    send_started = True
                    await sender(value)
                delivered = True

            answer = await self.chat.reply(key, text, deliver, model=model)
            status = "replied" if text.strip() and len(text.strip()) <= self.settings.max_input_chars else "rejected"
            await self.store.call("update_record", record_id, status=status)
            return answer
        except StorageError:
            # 发送后存库失败不能再给用户发一遍回复；只留下安全诊断。
            logger.error("聊天记录更新失败，delivered=%s", delivered)
            raise
        except Exception as exc:
            status = ("delivery_unknown" if isinstance(exc, QQDeliveryUnknown) else "send_failed") if send_started else "model_failed"
            await self.store.call("update_record", record_id, status=status, answer=answer,
                                  error_message={"delivery_unknown": "网络异常，送达状态未知",
                                                 "send_failed": "发送失败，请检查 QQ 权限或接口状态",
                                                 "model_failed": "模型请求失败，请检查配置、网络及额度"}[status])
            raise
        finally:
            # 每次读取最新配置创建客户端，调用结束后释放其连接池。
            if model is not None and hasattr(model, "root_async_client"):
                try:
                    await model.root_async_client.close()
                    await asyncio.to_thread(model.root_client.close)
                except Exception as exc:
                    logger.warning("模型连接池关闭失败：%s", type(exc).__name__)
