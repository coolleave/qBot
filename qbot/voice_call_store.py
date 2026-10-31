"""Store 的通话事务；所有调用仍经 Store.call 进入线程池。"""
import json
import hashlib
from datetime import datetime, timezone

from sqlalchemy import select

from qbot.voice_call_config import VoiceCallConfig, validate_voice_provider


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class VoiceCallStore:
    def voice_config(self, bot_id: int) -> dict:
        with self.engine.connect() as conn:
            if conn.execute(select(self.bots.c.id).where(self.bots.c.id == bot_id)).first() is None:
                raise ValueError("机器人不存在")
            row = conn.execute(select(self.voice_configs).where(self.voice_configs.c.bot_id == bot_id)).mappings().first()
        return {key: row[key] for key in VoiceCallConfig.model_fields} if row else VoiceCallConfig().model_dump()

    def save_voice_config(self, bot_id: int, values: dict) -> None:
        config = VoiceCallConfig.model_validate(values).model_dump()
        with self.engine.begin() as conn:
            # 与供应商更新共用配置行锁，防止并发停用刚绑定的模型。
            conn.execute(select(self.settings.c.id).where(self.settings.c.id == 1).with_for_update()).one()
            bot = conn.execute(select(self.bots.c.id, self.bots.c.provider_id).where(self.bots.c.id == bot_id)).mappings().first()
            if bot is None:
                raise ValueError("机器人不存在")
            realtime = config["mode"] == "realtime"
            if config["enabled"] and not realtime and config["chat_provider_id"] is None:
                pid = bot["provider_id"]
                if pid is None:
                    pid = conn.execute(select(self.settings.c.default_provider_id).where(self.settings.c.id == 1)).scalar_one()
                row = conn.execute(select(self.providers).where(self.providers.c.id == pid)).mappings().first()
                if not row or not row["enabled"] or not row["api_key_encrypted"] or row["model_type"] != "chat":
                    raise ValueError("启用通话前请配置机器人聊天模型，或单独选择通话聊天模型")
            bindings = (("realtime_provider_id", "realtime"),) if realtime else (
                ("chat_provider_id", "chat"), ("asr_provider_id", "asr_realtime"), ("tts_provider_id", "tts"))
            # 非当前模式的引用清空，避免隐藏配置阻止供应商维护。
            for field in ("chat_provider_id", "asr_provider_id", "tts_provider_id", "realtime_provider_id"):
                if field not in {name for name, _ in bindings}:
                    config[field] = None
            for field, kind in bindings:
                pid = config[field]
                if pid is None:
                    if config["enabled"] and kind != "chat":
                        raise ValueError("启用通话前请选择当前模式所需模型")
                    continue
                row = conn.execute(select(self.providers).where(self.providers.c.id == pid)).mappings().first()
                if not row or not row["enabled"] or not row["api_key_encrypted"]:
                    raise ValueError("通话模型需要启用且已配置密钥")
                validate_voice_provider(dict(row), kind)
            exists = conn.execute(select(self.voice_configs.c.bot_id).where(self.voice_configs.c.bot_id == bot_id)).first()
            config["updated_at"] = now_utc()
            if exists:
                conn.execute(self.voice_configs.update().where(self.voice_configs.c.bot_id == bot_id).values(**config))
            else:
                conn.execute(self.voice_configs.insert().values(bot_id=bot_id, **config))

    def check_voice_provider_references(self, conn, provider_id: int, values: dict) -> None:
        for field, kind in (("chat_provider_id", "chat"), ("asr_provider_id", "asr_realtime"), ("tts_provider_id", "tts"),
                            ("realtime_provider_id", "realtime")):
            if conn.execute(select(self.voice_configs.c.bot_id).where(self.voice_configs.c[field] == provider_id).limit(1)).first():
                if not values["enabled"]:
                    raise ValueError("供应商已被语音通话引用，请先调整通话配置")
                validate_voice_provider(values, kind)

    def voice_memory_list(self, bot_id: int, owner_hash: str, query: str = "", limit: int = 8) -> list[dict]:
        """只读取服务端确定的网页身份；不接收模型提供的身份或机器人范围。"""
        table = self.voice_memories
        statement = select(table.c.id, table.c.content, table.c.created_at).where(
            table.c.bot_id == bot_id, table.c.owner_hash == owner_hash)
        if query.strip():
            statement = statement.where(table.c.content.contains(query.strip()[:100], autoescape=True))
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement.order_by(table.c.id.desc()).limit(min(limit, 100))).mappings()]

    def save_voice_memory(self, bot_id: int, owner_hash: str, content: str) -> None:
        content = content.strip()
        if not 1 <= len(content) <= 600:
            raise ValueError("记忆内容需为1～600字")
        table = self.voice_memories
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self.engine.begin() as conn:
            if conn.execute(select(self.bots.c.id).where(self.bots.c.id == bot_id).with_for_update()).first() is None:
                raise ValueError("机器人不存在")
            scope = (table.c.bot_id == bot_id, table.c.owner_hash == owner_hash)
            if conn.execute(select(table.c.id).where(*scope, table.c.content_hash == digest)).first():
                return
            if len(conn.execute(select(table.c.id).where(*scope).limit(100)).all()) >= 100:
                raise ValueError("网页记忆最多100条，请先删除不再需要的条目")
            conn.execute(table.insert().values(bot_id=bot_id, owner_hash=owner_hash, content=content,
                                               content_hash=digest, created_at=now_utc()))

    def delete_voice_memory(self, bot_id: int, owner_hash: str, memory_id: int) -> None:
        table = self.voice_memories
        with self.engine.begin() as conn:
            conn.execute(table.delete().where(table.c.bot_id == bot_id, table.c.owner_hash == owner_hash,
                                              table.c.id == memory_id))

    def create_voice_call(self, call_id: str, bot_id: int, snapshot: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(self.voice_calls.insert().values(id=call_id, bot_id=bot_id, started_at=now_utc(),
                status="connecting", model_snapshot=json.dumps(snapshot, ensure_ascii=False), turns="[]"))

    def update_voice_call(self, call_id: str, turns: list[dict], status: str, reason: str | None = None,
                          ended: bool = False) -> None:
        values = dict(turns=json.dumps(turns, ensure_ascii=False), status=status, reason=reason)
        if ended:
            values["ended_at"] = now_utc()
        with self.engine.begin() as conn:
            conn.execute(self.voice_calls.update().where(self.voice_calls.c.id == call_id).values(**values))

    def list_voice_calls(self, bot_id: int, before: datetime | None = None) -> list[dict]:
        table = self.voice_calls
        query = select(table.c.id, table.c.bot_id, table.c.started_at, table.c.ended_at, table.c.status, table.c.reason)
        query = query.where(table.c.bot_id == bot_id)
        if before is not None:
            query = query.where(table.c.started_at < before)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(query.order_by(table.c.started_at.desc()).limit(20)).mappings()]

    def voice_call_detail(self, bot_id: int, call_id: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.voice_calls).where(self.voice_calls.c.id == call_id,
                self.voice_calls.c.bot_id == bot_id)).mappings().first()
        if not row:
            raise ValueError("通话记录不存在")
        result = dict(row)
        for key in ("turns", "model_snapshot"):
            result[key] = json.loads(result[key])
        return result
