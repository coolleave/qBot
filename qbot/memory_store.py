"""记忆事务；与聊天共用 Store，模型调用不持有数据库锁。"""
import json
import logging
from datetime import timedelta, timezone

from sqlalchemy import and_, func, or_, select

from qbot.memory_config import MemoryConfig, MemoryInput, clip_bytes, digest, json_text, utc_now
from qbot.memory_jobs import MemoryJobStore

logger = logging.getLogger(__name__)


class MemoryStore(MemoryJobStore):
    def _memory_settings(self, conn, bot_id: int) -> dict:
        row = conn.execute(select(self.memory_settings).where(self.memory_settings.c.bot_id == bot_id)).mappings().first()
        if row:
            return dict(row)
        values = dict(bot_id=bot_id, config=json_text(MemoryConfig().model_dump()), version=1,
                      owner_version=1, enabled_after=0, paused_reason="", updated_at=utc_now())
        conn.execute(self.memory_settings.insert().values(**values))
        return values

    def _memory_bot_lock(self, conn, bot_id: int) -> dict:
        bot = conn.execute(select(self.bots).where(self.bots.c.id == bot_id).with_for_update()).mappings().first()
        if not bot:
            raise ValueError("机器人不存在")
        return dict(bot)

    def _memory_conversation(self, conn, bot_id: int, conversation_id: int) -> dict:
        row = conn.execute(select(self.conversations).where(self.conversations.c.bot_id == bot_id,
            self.conversations.c.id == conversation_id, self.conversations.c.source.in_(("qq_c2c", "qq_group")))).mappings().first()
        if not row:
            raise ValueError("该机器人下不存在此 QQ 会话")
        return dict(row)

    def _memory_owner(self, conn, bot_id: int) -> str | None:
        i, r = self.identities, self.roles
        row = conn.execute(select(i.c.user_id).select_from(i.join(r,
            and_(i.c.bot_id == r.c.bot_id, i.c.role_id == r.c.id))).where(i.c.bot_id == bot_id,
            i.c.owner_slot == 1, i.c.family_role == r.c.role_key,
            or_(r.c.member_name == "", r.c.member_name.is_(None), i.c.member_name == r.c.member_name))).first()
        return row[0] if row else None

    def _memory_state(self, conn, bot_id: int, cid: int) -> dict:
        t = self.memory_states
        row = conn.execute(select(t).where(t.c.bot_id == bot_id, t.c.conversation_id == cid)).mappings().first()
        if row:
            return dict(row)
        config = self._memory_settings(conn, bot_id)
        values = dict(bot_id=bot_id, conversation_id=cid, generation=1, boundary_id=config["enabled_after"],
                      summary="", summary_through=0, reason="", next_run_at=None, last_run_at=None, checked_at=None)
        values["id"] = conn.execute(t.insert().values(**values)).inserted_primary_key[0]
        return values

    def _memory_latest(self, conn, bot_id: int) -> int:
        return conn.execute(select(func.max(self.records.c.id)).where(self.records.c.bot_id == bot_id)).scalar_one() or 0

    def _memory_invalidate(self, conn, bot_id: int, *, conversation_ids: list[int] | None = None,
                           owner_changed: bool = False, boundary: int | None = None) -> None:
        """调用方已持有机器人行锁；作废候选正文但保留实际费用账本。"""
        config = self._memory_settings(conn, bot_id)
        if owner_changed:
            version = config["owner_version"] + 1
            conn.execute(self.memory_settings.update().where(self.memory_settings.c.bot_id == bot_id)
                         .values(owner_version=version, updated_at=utc_now()))
            m = self.memory_items
            conn.execute(m.update().where(m.c.bot_id == bot_id, m.c.scope == "group")
                         .values(scope="personal", status=func.if_(and_(m.c.category == "request",
                             m.c.status != "forgotten"), "superseded", m.c.status)))
        boundary = self._memory_latest(conn, bot_id) if boundary is None else boundary
        for table in (self.memory_states, self.memory_sources, self.memory_runs):
            conditions = [table.c.bot_id == bot_id]
            if conversation_ids is not None:
                if not conversation_ids:
                    continue
                conditions.append(table.c.conversation_id.in_(conversation_ids))
            if table is self.memory_states:
                conn.execute(table.update().where(*conditions).values(generation=table.c.generation + 1,
                    boundary_id=boundary, summary="", summary_through=0, next_run_at=None, reason="上下文已重置"))
            elif table is self.memory_sources:
                conn.execute(table.update().where(*conditions, table.c.status != "done").values(status="cancelled"))
            else:
                # 已发送的调用仍保留计量；未发出的预留可以安全释放。
                conn.execute(table.update().where(*conditions, table.c.status == "reserved")
                             .values(status="invalidated", result=None, charged_tokens=0, call_count=0, finished_at=utc_now()))
                conn.execute(table.update().where(*conditions, table.c.status.in_(("calling", "result", "failed", "call_unknown")))
                             .values(status="invalidated", result=None, finished_at=utc_now()))
                conn.execute(table.update().where(*conditions).values(result=None))

    def memory_config(self, bot_id: int) -> dict:
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            row = self._memory_settings(conn, bot_id)
        return {**MemoryConfig.model_validate_json(row["config"]).model_dump(),
                "paused_reason": row["paused_reason"], "global_daily_calls": 60, "global_daily_tokens": 180000}

    def save_memory_config(self, bot_id: int, values: dict) -> None:
        config = MemoryConfig.model_validate(values)
        if config.automatic and (not config.provider_id or not config.budget_compatible):
            raise ValueError("自动整理需选择独立聊天模型，并确认支持字节级输入预算及输出上限")
        with self.engine.begin() as conn:
            conn.execute(select(self.settings.c.id).where(self.settings.c.id == 1).with_for_update()).one()
            self._memory_bot_lock(conn, bot_id)
            old = self._memory_settings(conn, bot_id)
            if config.provider_id:
                provider = conn.execute(select(self.providers).where(self.providers.c.id == config.provider_id)).mappings().first()
                if not provider or provider["model_type"] != "chat" or not provider["enabled"] or not provider["api_key_encrypted"]:
                    raise ValueError("记忆整理模型须为已启用且配置密钥的聊天模型")
            previous = MemoryConfig.model_validate_json(old["config"])
            boundary = self._memory_latest(conn, bot_id) if config.enabled and not previous.enabled else old["enabled_after"]
            self._memory_invalidate(conn, bot_id, boundary=boundary if not previous.enabled else None)
            conn.execute(self.memory_settings.update().where(self.memory_settings.c.bot_id == bot_id)
                .values(config=json_text(config.model_dump()), version=old["version"] + 1,
                        enabled_after=boundary, paused_reason="", updated_at=utc_now()))

    def check_memory_provider_references(self, conn, provider_id: int, values: dict) -> None:
        for row in conn.execute(select(self.memory_settings.c.config)).scalars():
            config = MemoryConfig.model_validate_json(row)
            if config.provider_id == provider_id and (not values["enabled"] or values["model_type"] != "chat"):
                raise ValueError("供应商被记忆整理引用，请先调整记忆配置")

    def _memory_receive(self, conn, bot_id: int, cid: int, rid: int, text: str, source: str,
                        message_id: str, user_id: str) -> None:
        from qbot.memory_config import memory_command
        if source not in ("qq_group", "qq_c2c") or message_id.startswith(("admin-send:", "scheduled:", "scheduled-manual:")):
            return
        row = self._memory_settings(conn, bot_id)
        config = MemoryConfig.model_validate_json(row["config"])
        if not config.enabled:
            return
        state = self._memory_state(conn, bot_id, cid)
        if text.strip() == "/reset" or memory_command(text):
            return
        conn.execute(self.memory_sources.insert().values(bot_id=bot_id, conversation_id=cid, record_id=rid,
            generation=state["generation"], config_version=row["version"], owner_version=row["owner_version"],
            is_owner=self._memory_owner(conn, bot_id) == user_id, status="pending", position=0, created_at=utc_now()))

    def _memory_available(self, conn, bot_id: int, conversation: dict, *, own_only: bool = False):
        m = self.memory_items
        scopes = [and_(m.c.conversation_id == conversation["id"], m.c.scope == "personal")]
        config = self._memory_settings(conn, bot_id)
        if conversation["source"] == "qq_group":
            shared = and_(m.c.group_id == conversation["group_id"], m.c.scope == "group",
                          m.c.owner_version == config["owner_version"])
            if own_only:
                shared = and_(shared, m.c.subject_id == conversation["user_id"])
            scopes.append(shared)
        return [m.c.bot_id == bot_id, or_(*scopes), m.c.status == "active",
                or_(m.c.expires_at.is_(None), m.c.expires_at > utc_now())]

    def _memory_put(self, conn, bot_id: int, values: dict, *, origin: str, sources: list[int],
                    owner_version: int = 0, item_id: int | None = None, status: str = "active") -> int:
        m = self.memory_items
        conversation = self._memory_conversation(conn, bot_id, values["conversation_id"])
        if values["scope"] == "group" and conversation["source"] != "qq_group":
            raise ValueError("私聊不能创建群共享记忆")
        hashed = digest(" ".join(values["content"].split()))
        expiry = values.get("expires_at")
        if expiry is not None and expiry.tzinfo is not None:
            expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        if origin in ("explicit", "admin"):
            # 只有重新明确提供的相同正文/事实键解除相应墓碑，不扩大到其他属性。
            matching = m.c.content_hash == hashed
            if values.get("fact_key"):
                matching = or_(matching, m.c.fact_key == values["fact_key"])
            conn.execute(m.update().where(m.c.bot_id == bot_id, m.c.conversation_id == conversation["id"],
                m.c.scope == values["scope"], m.c.status == "forgotten", matching).values(status="superseded"))
        existing = conn.execute(select(m).where(m.c.bot_id == bot_id, m.c.conversation_id == conversation["id"],
            m.c.scope == values["scope"], m.c.content_hash == hashed, m.c.status == "active")).mappings().first()
        if existing and item_id is None:
            merged = sorted(set(json.loads(existing["sources"]) + sources))[-40:]
            conn.execute(m.update().where(m.c.bot_id == bot_id, m.c.id == existing["id"])
                         .values(sources=json_text(merged), updated_at=utc_now(),
                                 expires_at=existing["expires_at"] if origin == "automatic" and existing["origin"] in ("explicit", "admin") else expiry,
                                 origin=origin if origin in ("explicit", "admin") else existing["origin"]))
            return existing["id"]
        data = dict(bot_id=bot_id, conversation_id=conversation["id"], scope=values["scope"],
            group_id=conversation["group_id"], subject_id=conversation["user_id"], category=values["category"],
            fact_key=values.get("fact_key", ""), content=values["content"], content_hash=hashed, sources=json_text(sources),
            origin=origin, owner_version=owner_version, status=status, expires_at=expiry, updated_at=utc_now())
        if item_id is not None:
            old = conn.execute(select(m).where(m.c.bot_id == bot_id, m.c.id == item_id,
                m.c.conversation_id == conversation["id"])).mappings().first()
            if not old or old["status"] == "forgotten":
                raise ValueError("记忆不存在或已遗忘")
            self._memory_clear_scope(conn, bot_id, dict(old))
            conn.execute(m.update().where(m.c.bot_id == bot_id, m.c.id == item_id).values(**data))
            return item_id
        return conn.execute(m.insert().values(**data, created_at=utc_now())).inserted_primary_key[0]

    def save_memory_item(self, bot_id: int, values: dict, item_id: int | None = None) -> int:
        parsed = MemoryInput.model_validate(values).model_dump()
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            settings = self._memory_settings(conn, bot_id)
            return self._memory_put(conn, bot_id, parsed, origin="admin", sources=[],
                owner_version=settings["owner_version"], item_id=item_id)

    def _memory_clear_scope(self, conn, bot_id: int, item: dict) -> None:
        c = self.conversations
        ids = [item["conversation_id"]]
        if item["scope"] == "group":
            ids = list(conn.execute(select(c.c.id).where(c.c.bot_id == bot_id, c.c.source == "qq_group",
                                                       c.c.group_id == item["group_id"])).scalars())
        for cid in ids:
            self._memory_state(conn, bot_id, cid)
        self._memory_invalidate(conn, bot_id, conversation_ids=ids)

    def forget_memory_item(self, bot_id: int, item_id: int) -> None:
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            self._memory_forget(conn, bot_id, item_id)

    def _memory_forget(self, conn, bot_id: int, item_id: int) -> None:
        m = self.memory_items
        item = conn.execute(select(m).where(m.c.bot_id == bot_id, m.c.id == item_id)).mappings().first()
        if not item:
            raise ValueError("记忆不存在")
        self._memory_clear_scope(conn, bot_id, dict(item))
        # 保留无正文的事实键/摘要哈希墓碑，防止自动提取再次引入已遗忘属性。
        same_fact = m.c.id == item_id
        if item["fact_key"]:
            same_fact = or_(same_fact, and_(m.c.conversation_id == item["conversation_id"],
                m.c.scope == item["scope"], m.c.fact_key == item["fact_key"]))
        else:
            same_fact = or_(same_fact, and_(m.c.conversation_id == item["conversation_id"],
                m.c.scope == item["scope"], m.c.content_hash == item["content_hash"]))
        conn.execute(m.update().where(m.c.bot_id == bot_id, same_fact)
                     .values(content="", status="forgotten", updated_at=utc_now()))

    def list_memory_items(self, bot_id: int, conversation_id: int | None = None, status: str = "active",
                          page: int = 1, query: str = "") -> dict:
        m = self.memory_items
        filters = [m.c.bot_id == bot_id]
        if conversation_id:
            filters.append(m.c.conversation_id == conversation_id)
        if status:
            filters.append(m.c.status == status)
        if query:
            filters.append(m.c.content.contains(query, autoescape=True))
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(m).where(*filters)).scalar_one()
            rows = conn.execute(select(m).where(*filters).order_by(m.c.updated_at.desc(), m.c.id.desc())
                                .offset((page - 1) * 20).limit(20)).mappings().all()
        return {"items": [dict(row) for row in rows], "total": total, "page": page}

    def memory_conversations(self, bot_id: int) -> list[dict]:
        c = self.conversations
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(c.c.id, c.c.source, c.c.group_id, c.c.user_id,
                c.c.contact_name, c.c.platform_nickname).where(c.c.bot_id == bot_id,
                c.c.source.in_(("qq_c2c", "qq_group"))).order_by(c.c.last_active_at.desc()).limit(300)).mappings()]

    def memory_context(self, bot_id: int, record_id: int, text: str) -> dict | None:
        from qbot.memory_config import memory_command
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            config = self._memory_settings(conn, bot_id)
            record = conn.execute(select(self.records).where(self.records.c.id == record_id,
                self.records.c.bot_id == bot_id)).mappings().first()
            if not record or record["source"] not in ("qq_c2c", "qq_group"):
                return None
            conversation = self._memory_conversation(conn, bot_id, record["conversation_id"])
            state = self._memory_state(conn, bot_id, conversation["id"])
            if text.strip() == "/reset":
                self._memory_invalidate(conn, bot_id, conversation_ids=[conversation["id"]], boundary=record_id)
                return {"command": "当前会话已清空，长期记忆仍保留。"}
            command = memory_command(text)
            if not MemoryConfig.model_validate_json(config["config"]).enabled:
                return {"command": "当前机器人的记忆功能尚未开启。"} if command else None
            if command:
                return {"command": self._memory_command(conn, bot_id, conversation, record_id, command, config)}
            rows = [dict(row) for row in conn.execute(select(self.memory_items).where(
                *self._memory_available(conn, bot_id, conversation)).order_by(self.memory_items.c.updated_at.desc()).limit(150)).mappings()]
            words = set(text.lower().split())
            grams = {text[i:i + 2] for i in range(max(0, len(text) - 1)) if text[i:i + 2].strip()}
            def score(row: dict) -> tuple:
                content = row["content"].lower()
                overlap = sum(w in content for w in words if len(w) > 1) + sum(w in content for w in grams)
                return (overlap, row["origin"] in ("explicit", "admin"), row["category"] in ("preference", "request"), row["updated_at"])
            rows.sort(key=score, reverse=True)
            selected = [row for row in rows if score(row)[0] or row["origin"] in ("explicit", "admin")
                        or row["category"] in ("preference", "request")][:8]
            parts = ["【当前会话可用的记忆资料】"]
            if state["summary"]:
                parts.append("会话摘要：" + clip_bytes(state["summary"], 1800))
            for row in selected:
                value = f"#{row['id']} 主体={row['subject_id']} 时间={row['updated_at']}：{row['content']}"
                if len("\n".join(parts + [value]).encode("utf-8")) > 4300:
                    break
                parts.append(value)
            r = self.records
            if conversation["source"] == "qq_group":
                owner = self._memory_owner(conn, bot_id)
                c, s, st = self.conversations, self.memory_sources, self.memory_states
                reference = conn.execute(select(r.c.question).select_from(r.join(c, r.c.conversation_id == c.c.id)
                    .join(s, and_(s.c.bot_id == r.c.bot_id, s.c.record_id == r.c.id))
                    .join(st, and_(st.c.bot_id == s.c.bot_id, st.c.conversation_id == s.c.conversation_id)))
                    .where(r.c.bot_id == bot_id, c.c.source == "qq_group", c.c.group_id == conversation["group_id"],
                        c.c.user_id == owner, r.c.status == "replied", r.c.record_type == "chat", r.c.id < record_id,
                        r.c.id > st.c.boundary_id, s.c.generation == st.c.generation, s.c.is_owner.is_(True),
                        s.c.owner_version == config["owner_version"], r.c.received_at >= utc_now() - timedelta(minutes=30))
                    .order_by(r.c.id.desc()).limit(5)).scalars().all()
                for value in reversed(reference):
                    if value and not any(value in part for part in parts):
                        budget = 4500 - len("\n".join(parts).encode("utf-8")) - 80
                        if budget <= 0:
                            break
                        parts.append("同群主人近期原话（用户资料）：" + clip_bytes(value, min(800, budget)))
            history = conn.execute(select(r.c.id, r.c.question, r.c.answer, r.c.received_at, r.c.media_details).where(
                r.c.bot_id == bot_id, r.c.conversation_id == conversation["id"], r.c.id < record_id,
                r.c.id > state["boundary_id"], r.c.record_type == "chat", r.c.status == "replied",
                ~r.c.external_message_id.like("admin-send:%"), ~r.c.external_message_id.like("scheduled:%"),
                ~r.c.external_message_id.like("scheduled-manual:%"), r.c.received_at >= utc_now() - timedelta(hours=24)
            ).order_by(r.c.id.desc()).limit(10)).mappings().all()
            history_rows, budget = [], 9000
            for row in history:
                if memory_command(row["question"]):
                    continue
                question = self._memory_record_text(dict(row))
                try:
                    visual = json.loads(row["media_details"] or "{}").get("vision", {}).get("summary", "")
                    if isinstance(visual, str) and visual:
                        question += "\n[历史视觉说明，仅作资料，未保留原图]\n" + visual[:3000]
                except (ValueError, TypeError, AttributeError):
                    logger.warning("记忆恢复跳过无效视觉元数据 record_id=%s", row["id"])
                pair = {"question": f"[UTC {row['received_at']}] {question}", "answer": row["answer"] or ""}
                size = len(json_text(pair).encode("utf-8"))
                if size > budget:
                    break
                history_rows.append(pair)
                budget -= size
            return {"text": "\n".join(parts), "history": list(reversed(history_rows)),
                    "version": [config["version"], config["owner_version"], state["generation"]],
                    "conversation_id": conversation["id"], "boundary_id": state["boundary_id"]}

    def _memory_command(self, conn, bot_id: int, conversation: dict, record_id: int,
                        command: tuple[str, str], config: dict) -> str:
        kind, value = command
        m = self.memory_items
        if kind == "list":
            rows = conn.execute(select(m.c.id, m.c.content).where(*self._memory_available(
                conn, bot_id, conversation, own_only=True)).order_by(m.c.id.desc()).limit(10)).all()
            return "记忆（最近10条）：\n" + "\n".join(f"#{row.id} {row.content[:100]}" for row in rows) if rows else "当前范围没有有效的个人记忆。"
        if kind in ("add", "group"):
            if not value or len(value) > 600:
                return "请提供1～600字，例如：记住：我喜欢清淡食物。"
            if kind == "group" and (conversation["source"] != "qq_group" or self._memory_owner(conn, bot_id) != conversation["user_id"]):
                return "当前不能创建群共享记忆；可用 /remember 内容 保存本人记忆。"
            values = dict(conversation_id=conversation["id"], content=value, scope="group" if kind == "group" else "personal", category="note")
            item_id = self._memory_put(conn, bot_id, values, origin="explicit", sources=[record_id], owner_version=config["owner_version"])
            return f"已记住，编号 #{item_id}。"
        if kind in ("forget", "edit"):
            chunks = value.split(maxsplit=1)
            if not chunks or not chunks[0].lstrip("#").isdigit():
                return "请先用 /memory 查看编号，再使用 /forget 编号 或 /memory-edit 编号 新内容。"
            item_id = int(chunks[0].lstrip("#"))
            item = conn.execute(select(m).where(*self._memory_available(conn, bot_id, conversation, own_only=True),
                m.c.id == item_id)).mappings().first()
            if not item:
                return "当前范围未找到你可修改的有效记忆。"
            if kind == "forget":
                self._memory_forget(conn, bot_id, item_id)
                return "已忘记；为避免旧内容再次出现，相关近期对话也已重新开始。审计聊天记录仍保留。"
            if len(chunks) < 2 or not 1 <= len(chunks[1]) <= 600:
                return "请在编号后填写1～600字的新内容。"
            values = dict(conversation_id=item["conversation_id"], content=chunks[1], scope=item["scope"],
                          category=item["category"], fact_key=item["fact_key"])
            self._memory_put(conn, bot_id, values, origin="explicit", sources=[record_id],
                             owner_version=config["owner_version"], item_id=item_id)
            return "已更新记忆，相关近期对话已重新开始。"
        return "用 /memory 查看；用 /remember 内容 或 记住：内容 保存；用 /memory-edit 编号 新内容 修改；用 /forget 编号 遗忘。主人可用 /remember-group 内容 保存当前群共享记忆。"

    def memory_version_valid(self, bot_id: int, context: dict) -> bool:
        with self.engine.connect() as conn:
            config = conn.execute(select(self.memory_settings).where(self.memory_settings.c.bot_id == bot_id)).mappings().first()
            state = conn.execute(select(self.memory_states).where(self.memory_states.c.bot_id == bot_id,
                self.memory_states.c.conversation_id == context["conversation_id"])).mappings().first()
            return bool(config and state and context["version"] == [config["version"], config["owner_version"], state["generation"]])
