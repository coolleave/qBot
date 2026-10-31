"""Store 的通话事务；所有调用仍经 Store.call 进入线程池。"""
import json
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
            if config["enabled"] and config["chat_provider_id"] is None:
                pid = bot["provider_id"]
                if pid is None:
                    pid = conn.execute(select(self.settings.c.default_provider_id).where(self.settings.c.id == 1)).scalar_one()
                row = conn.execute(select(self.providers).where(self.providers.c.id == pid)).mappings().first()
                if not row or not row["enabled"] or not row["api_key_encrypted"] or row["model_type"] != "chat":
                    raise ValueError("启用通话前请配置机器人聊天模型，或单独选择通话聊天模型")
            for field, kind in (("chat_provider_id", "chat"), ("asr_provider_id", "asr_realtime"), ("tts_provider_id", "tts")):
                pid = config[field]
                if pid is None:
                    if config["enabled"] and kind != "chat":
                        raise ValueError("启用通话前请选择实时识别和语音合成模型")
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
        for field, kind in (("chat_provider_id", "chat"), ("asr_provider_id", "asr_realtime"), ("tts_provider_id", "tts")):
            if conn.execute(select(self.voice_configs.c.bot_id).where(self.voice_configs.c[field] == provider_id).limit(1)).first():
                if not values["enabled"]:
                    raise ValueError("供应商已被语音通话引用，请先调整通话配置")
                validate_voice_provider(values, kind)

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
