"""知识库短事务；网络及文件解析均在事务外进行。"""
from datetime import datetime, timezone

from sqlalchemy import and_, func, select

from qbot.knowledge_config import KnowledgeConfig


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class KnowledgeStore:
    def _knowledge_settings(self, conn, bot_id: int) -> dict:
        self._memory_bot_lock(conn, bot_id)
        row = conn.execute(select(self.knowledge_settings).where(self.knowledge_settings.c.bot_id == bot_id)).mappings().first()
        if row:
            return dict(row)
        values = dict(bot_id=bot_id, enabled=False, mode="assist", base_url="", model_name="",
                      api_key_encrypted=None, version=1, embedding_version=1, updated_at=now())
        conn.execute(self.knowledge_settings.insert().values(**values))
        return values

    def knowledge_config(self, bot_id: int, *, secret: bool = False) -> dict:
        with self.engine.begin() as conn:
            row = self._knowledge_settings(conn, bot_id)
        encrypted = row.pop("api_key_encrypted")
        row["has_api_key"] = bool(encrypted)
        if secret:
            row["api_key"] = self.cipher.decrypt(encrypted.encode()).decode() if encrypted else ""
        return row

    def _knowledge_changed(self, conn, bot_id: int) -> None:
        conn.execute(self.knowledge_settings.update().where(self.knowledge_settings.c.bot_id == bot_id)
                     .values(version=self.knowledge_settings.c.version + 1, updated_at=now()))
        # 旧答案可能派生为摘要，推进全部会话边界；长期用户条目不受影响。
        self._memory_invalidate(conn, bot_id)
        boundary = self._memory_latest(conn, bot_id)
        conn.execute(self.memory_settings.update().where(self.memory_settings.c.bot_id == bot_id)
                     .values(enabled_after=boundary))

    def save_knowledge_config(self, bot_id: int, values: dict) -> None:
        cfg = KnowledgeConfig.model_validate(values)
        key = cfg.api_key.get_secret_value().strip()
        if len(key) > 4096:
            raise ValueError("API Key 过长")
        with self.engine.begin() as conn:
            old = self._knowledge_settings(conn, bot_id)
            if old["api_key_encrypted"] and old["base_url"] != cfg.base_url and not key:
                raise ValueError("更换地址时请重新填写 API Key")
            encrypted = self.cipher.encrypt(key.encode()).decode() if key else old["api_key_encrypted"]
            if cfg.enabled and not (cfg.base_url and cfg.model_name and encrypted):
                raise ValueError("启用前请填写 Embedding 地址、模型及密钥")
            changed = bool(key) or old["base_url"] != cfg.base_url or old["model_name"] != cfg.model_name
            if changed:
                conn.execute(self.knowledge_files.update().where(self.knowledge_files.c.bot_id == bot_id,
                    self.knowledge_files.c.status.in_(("queued", "ready", "failed", "stale")))
                    .values(status="stale", error="Embedding 配置已变更，请重新处理", updated_at=now()))
            conn.execute(self.knowledge_settings.update().where(self.knowledge_settings.c.bot_id == bot_id).values(
                enabled=cfg.enabled, mode=cfg.mode, base_url=cfg.base_url, model_name=cfg.model_name,
                api_key_encrypted=encrypted, embedding_version=old["embedding_version"] + int(changed)))
            self._knowledge_changed(conn, bot_id)

    def knowledge_add_file(self, bot_id: int, values: dict) -> str:
        with self.engine.begin() as conn:
            cfg = self._knowledge_settings(conn, bot_id)
            if not (cfg["base_url"] and cfg["model_name"] and cfg["api_key_encrypted"]):
                raise ValueError("请先保存 Embedding 配置")
            table = self.knowledge_files
            duplicate = conn.execute(select(table.c.id).where(table.c.bot_id == bot_id,
                table.c.sha256 == values["sha256"])).scalar_one_or_none()
            if duplicate:
                raise ValueError("该智能体已存在相同文件，请在列表中查看或重试")
            count, size = conn.execute(select(func.count(), func.coalesce(func.sum(table.c.size_bytes), 0))
                .where(table.c.bot_id == bot_id)).one()
            if count >= 100 or size + values["size_bytes"] > 1024 * 1024 * 1024:
                raise ValueError("每个智能体最多100个文件、总容量1 GB")
            conn.execute(table.insert().values(**values, bot_id=bot_id, status="queued",
                embedding_version=cfg["embedding_version"], created_at=now(), updated_at=now()))
            return values["id"]

    def knowledge_list_files(self, bot_id: int, page: int) -> dict:
        with self.engine.begin() as conn:
            self._knowledge_settings(conn, bot_id)
            table = self.knowledge_files
            total = conn.execute(select(func.count()).select_from(table).where(table.c.bot_id == bot_id)).scalar_one()
            rows = conn.execute(select(table).where(table.c.bot_id == bot_id).order_by(table.c.created_at.desc(), table.c.id)
                                .offset((page - 1) * 20).limit(20)).mappings().all()
            return {"items": [dict(row) for row in rows], "total": total}

    def knowledge_file(self, bot_id: int, file_id: str) -> dict:
        with self.engine.connect() as conn:
            return self._knowledge_file(conn, bot_id, file_id)

    def _knowledge_file(self, conn, bot_id: int, file_id: str) -> dict:
        t = self.knowledge_files
        row = conn.execute(select(t).where(t.c.bot_id == bot_id, t.c.id == file_id)).mappings().first()
        if not row:
            raise ValueError("该智能体下不存在此文件")
        return dict(row)

    def knowledge_file_action(self, bot_id: int, file_id: str, action: str) -> None:
        with self.engine.begin() as conn:
            cfg = self._knowledge_settings(conn, bot_id)
            row = self._knowledge_file(conn, bot_id, file_id)
            if action == "retry":
                if row["status"] not in ("ready", "failed", "stale", "cleanup_failed"):
                    raise ValueError("文件正在处理，请稍后操作")
                status = "deleting" if row["status"] == "cleanup_failed" else "queued"
                if status == "queued" and not (cfg["base_url"] and cfg["model_name"] and cfg["api_key_encrypted"]):
                    raise ValueError("Embedding 配置不完整")
            elif action == "delete":
                status = "deleting"
            else:
                raise ValueError("未知文件操作")
            conn.execute(self.knowledge_files.update().where(self.knowledge_files.c.bot_id == bot_id,
                self.knowledge_files.c.id == file_id).values(status=status, error="", updated_at=now()))
            self._knowledge_changed(conn, bot_id)

    def knowledge_recover(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(self.knowledge_files.update().where(self.knowledge_files.c.status == "processing")
                .values(status="failed", error="处理被中断，可能已产生费用，请手动重试", updated_at=now()))

    def knowledge_pending(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(self.knowledge_files).where(
                self.knowledge_files.c.status.in_(("queued", "deleting")))
                .order_by(self.knowledge_files.c.updated_at).limit(20)).mappings()]

    def knowledge_claim(self, bot_id: int, file_id: str) -> dict | None:
        with self.engine.begin() as conn:
            cfg = self._knowledge_settings(conn, bot_id)
            row = self._knowledge_file(conn, bot_id, file_id)
            if row["status"] != "queued":
                return None
            generation = row["generation"] + 1
            conn.execute(self.knowledge_files.update().where(self.knowledge_files.c.bot_id == bot_id,
                self.knowledge_files.c.id == file_id).values(status="processing", generation=generation,
                    embedding_version=cfg["embedding_version"], error="", updated_at=now()))
            return {**row, "status": "processing", "generation": generation,
                    "embedding_version": cfg["embedding_version"]}

    def knowledge_set_collection(self, row: dict, collection: str) -> bool:
        with self.engine.begin() as conn:
            self._knowledge_settings(conn, row["bot_id"])
            t = self.knowledge_files
            return bool(conn.execute(t.update().where(t.c.bot_id == row["bot_id"], t.c.id == row["id"],
                t.c.status == "processing", t.c.generation == row["generation"])
                .values(collection_name=collection, updated_at=now())).rowcount)

    def knowledge_call_usage(self, row: dict, tokens: int | None = None, *, started: bool = False) -> None:
        with self.engine.begin() as conn:
            t = self.knowledge_files
            values = {"embedding_calls": t.c.embedding_calls + 1, "unknown_calls": t.c.unknown_calls + 1} if started else {
                "embedding_tokens": t.c.embedding_tokens + tokens, "unknown_calls": t.c.unknown_calls - 1}
            conn.execute(t.update().where(t.c.bot_id == row["bot_id"], t.c.id == row["id"],
                t.c.generation == row["generation"]).values(**values))

    def knowledge_finish(self, row: dict, chunks: list[dict]) -> bool:
        with self.engine.begin() as conn:
            cfg = self._knowledge_settings(conn, row["bot_id"])
            current = self._knowledge_file(conn, row["bot_id"], row["id"])
            if (current["status"] != "processing" or current["generation"] != row["generation"]
                    or cfg["embedding_version"] != row["embedding_version"]):
                return False
            c = self.knowledge_chunks
            conn.execute(c.delete().where(c.c.bot_id == row["bot_id"], c.c.file_id == row["id"]))
            conn.execute(c.insert(), [dict(bot_id=row["bot_id"], file_id=row["id"], generation=row["generation"],
                                         ordinal=i, **chunk) for i, chunk in enumerate(chunks)])
            t = self.knowledge_files
            conn.execute(t.update().where(t.c.bot_id == row["bot_id"], t.c.id == row["id"])
                         .values(status="ready", chunk_count=len(chunks), error="", updated_at=now()))
            self._knowledge_changed(conn, row["bot_id"])
            return True

    def knowledge_fail(self, row: dict, error: str, *, cleanup: bool = False) -> None:
        with self.engine.begin() as conn:
            t = self.knowledge_files
            conn.execute(t.update().where(t.c.bot_id == row["bot_id"], t.c.id == row["id"],
                t.c.status == ("deleting" if cleanup else "processing"), t.c.generation == row["generation"])
                .values(status="cleanup_failed" if cleanup else "failed", error=error[:200], updated_at=now()))

    def knowledge_remove(self, bot_id: int, file_id: str) -> None:
        with self.engine.begin() as conn:
            self._knowledge_settings(conn, bot_id)
            row = self._knowledge_file(conn, bot_id, file_id)
            if row["status"] != "deleting":
                raise ValueError("文件不处于删除状态")
            c, t = self.knowledge_chunks, self.knowledge_files
            conn.execute(c.delete().where(c.c.bot_id == bot_id, c.c.file_id == file_id))
            conn.execute(t.delete().where(t.c.bot_id == bot_id, t.c.id == file_id))

    def knowledge_ready_ids(self, bot_id: int, embedding_version: int) -> list[str]:
        with self.engine.connect() as conn:
            t = self.knowledge_files
            return list(conn.execute(select(t.c.id).where(t.c.bot_id == bot_id, t.c.status == "ready",
                t.c.embedding_version == embedding_version)).scalars())

    def knowledge_chunk(self, bot_id: int, file_id: str, generation: int, ordinal: int, embedding_version: int) -> dict | None:
        with self.engine.connect() as conn:
            c, f = self.knowledge_chunks, self.knowledge_files
            row = conn.execute(select(c.c.content, c.c.location, f.c.filename).select_from(c.join(f,
                and_(c.c.bot_id == f.c.bot_id, c.c.file_id == f.c.id, c.c.generation == f.c.generation)))
                .where(c.c.bot_id == bot_id, c.c.file_id == file_id, c.c.generation == generation,
                       c.c.ordinal == ordinal, f.c.status == "ready", f.c.embedding_version == embedding_version)).mappings().first()
            return dict(row) if row else None
