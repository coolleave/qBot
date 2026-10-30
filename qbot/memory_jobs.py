"""持久化整理批次、预算和恢复规则。"""
import json
import logging
from datetime import timedelta, timezone

from sqlalchemy import and_, func, or_, select

from qbot.memory_config import MemoryConfig, MemoryResult, clip_bytes, digest, json_text, token_bound, useful_text, utc_now

logger = logging.getLogger(__name__)


class MemoryJobStore:
    def memory_reject_result(self, bot_id: int, run_id: int) -> None:
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            result = conn.execute(self.memory_runs.update().where(self.memory_runs.c.bot_id == bot_id,
                self.memory_runs.c.id == run_id, self.memory_runs.c.status == "result")
                .values(status="failed", result=None, error="候选来源、权限或结构校验失败", finished_at=utc_now()))
            if result.rowcount:
                conn.execute(self.memory_sources.update().where(self.memory_sources.c.bot_id == bot_id,
                    self.memory_sources.c.run_id == run_id).values(status="blocked"))

    def memory_recover(self) -> None:
        with self.engine.begin() as conn:
            runs, sources = self.memory_runs, self.memory_sources
            # 只有 reserved 明确尚未发出；calling 不得猜测为失败重试。
            reserved = list(conn.execute(select(runs.c.id).where(runs.c.status == "reserved")).scalars())
            if reserved:
                conn.execute(sources.update().where(sources.c.run_id.in_(reserved), sources.c.status == "claimed")
                             .values(status="pending", run_id=None))
                conn.execute(runs.update().where(runs.c.id.in_(reserved)).values(status="cancelled",
                    charged_tokens=0, call_count=0, finished_at=utc_now(), error="调用前中断，预留已释放"))
            conn.execute(runs.update().where(runs.c.status == "calling").values(status="call_unknown",
                error="服务中断，调用及用量未知，未自动重试", finished_at=utc_now()))
            unknown = select(runs.c.id).where(runs.c.status == "call_unknown")
            conn.execute(sources.update().where(sources.c.run_id.in_(unknown), sources.c.status == "claimed").values(status="blocked"))
            conn.execute(runs.update().where(runs.c.created_at < utc_now() - timedelta(days=1),
                runs.c.status.notin_(("result", "calling"))).values(result=None))

    def memory_candidates(self) -> list[dict]:
        s, st, b = self.memory_sources, self.memory_states, self.bots
        with self.engine.connect() as conn:
            rows = conn.execute(select(s.c.bot_id, s.c.conversation_id, func.max(s.c.is_owner).label("owner"),
                func.min(s.c.created_at).label("oldest"), st.c.last_run_at, st.c.checked_at).select_from(s.join(st,
                and_(st.c.bot_id == s.c.bot_id, st.c.conversation_id == s.c.conversation_id)).join(b, b.c.id == s.c.bot_id))
                .where(s.c.status == "pending", b.c.enabled.is_(True),
                       or_(st.c.next_run_at.is_(None), st.c.next_run_at <= utc_now()))
                .group_by(s.c.bot_id, s.c.conversation_id, st.c.last_run_at, st.c.checked_at)
                .order_by(st.c.checked_at, func.min(s.c.created_at)).limit(60)).mappings().all()
        # 等待超过一小时优先，避免持续的主人消息让普通摘要饥饿。
        return sorted([dict(row) for row in rows], key=lambda row: (
            row["oldest"] > utc_now() - timedelta(hours=1), not row["owner"], row["last_run_at"] or row["oldest"]))

    def _memory_record_text(self, record: dict) -> str:
        text = record["question"] or ""
        try:
            details = json.loads(record.get("media_details") or "{}")
            for attachment in details.get("attachments", []):
                transcript = attachment.get("transcript")
                if isinstance(transcript, str) and transcript.strip():
                    text += "\n[语音转写，仅作用户资料]\n" + transcript
        except (ValueError, TypeError, AttributeError):
            # 历史附件元数据异常时只用原始文本，不猜测识别结果。
            logger.warning("记忆读取跳过无效附件元数据 record_id=%s", record.get("record_id", record.get("id")))
        return text

    def prepare_memory_batch(self, bot_id: int, cid: int, prompt: str, *, force: bool = False) -> dict | None:
        with self.engine.begin() as conn:
            bot = self._memory_bot_lock(conn, bot_id)
            setting = self._memory_settings(conn, bot_id)
            cfg = MemoryConfig.model_validate_json(setting["config"])
            state = self._memory_state(conn, bot_id, cid)
            now = utc_now()
            conn.execute(self.memory_states.update().where(self.memory_states.c.id == state["id"]).values(checked_at=now))
            if not bot["enabled"] or not cfg.enabled or not cfg.automatic or setting["paused_reason"]:
                conn.execute(self.memory_states.update().where(self.memory_states.c.id == state["id"])
                    .values(next_run_at=now + timedelta(minutes=10), reason=setting["paused_reason"] or "自动整理未开启"))
                return None
            force = force or state["reason"] == "管理员请求整理"
            if state["last_run_at"] and now < state["last_run_at"] + timedelta(minutes=cfg.cooldown_minutes):
                conn.execute(self.memory_states.update().where(self.memory_states.c.id == state["id"])
                    .values(next_run_at=state["last_run_at"] + timedelta(minutes=cfg.cooldown_minutes)))
                return None
            if conn.execute(select(self.memory_runs.c.id).where(self.memory_runs.c.bot_id == bot_id,
                self.memory_runs.c.conversation_id == cid, self.memory_runs.c.status.in_(("reserved", "calling", "result"))).limit(1)).first():
                return None
            conversation = self._memory_conversation(conn, bot_id, cid)
            src, r = self.memory_sources, self.records
            rows = conn.execute(select(src, r.c.question, r.c.answer, r.c.status.label("record_status"),
                r.c.media_details, r.c.received_at).select_from(src.join(r, and_(src.c.bot_id == r.c.bot_id, src.c.record_id == r.c.id)))
                .where(src.c.bot_id == bot_id, src.c.conversation_id == cid, src.c.status == "pending")
                .order_by(src.c.id).limit(100)).mappings().all()
            eligible = []
            for row in rows:
                row = dict(row)
                if (row["generation"] != state["generation"] or row["config_version"] != setting["version"]
                        or row["owner_version"] != setting["owner_version"] or row["record_id"] <= state["boundary_id"]):
                    conn.execute(src.update().where(src.c.id == row["id"], src.c.bot_id == bot_id).values(status="cancelled"))
                    continue
                if row["record_status"] in ("pending", "processing") and row["received_at"] > now - timedelta(minutes=10):
                    continue
                text = self._memory_record_text(row)
                if row["record_status"] in ("rejected", "expired") or not useful_text(text):
                    conn.execute(src.update().where(src.c.id == row["id"], src.c.bot_id == bot_id).values(status="skipped"))
                    continue
                row["text"] = text
                eligible.append(row)
            if not eligible:
                conn.execute(self.memory_states.update().where(self.memory_states.c.id == state["id"])
                    .values(next_run_at=now + timedelta(seconds=60), reason="等待可整理的终态消息"))
                return None
            is_owner = self._memory_owner(conn, bot_id) == conversation["user_id"]
            allow_facts = conversation["source"] == "qq_c2c" or (is_owner and all(row["is_owner"] for row in eligible))
            amount = sum(token_bound(row["text"][row["position"]:]) for row in eligible)
            idle = eligible[-1]["received_at"] < now - timedelta(minutes=cfg.idle_minutes)
            # 普通群成员只在上下文够长时摘要，不因少量闲聊单独调用。
            if not force and not (any(row["position"] > 0 for row in eligible) or len(eligible) >= cfg.batch_messages or amount >= 3000
                    or allow_facts and len(eligible) >= 2 and idle):
                conn.execute(self.memory_states.update().where(self.memory_states.c.id == state["id"])
                    .values(next_run_at=now + timedelta(seconds=60), reason="等待增量达到整理条件"))
                return None
            old = [dict(row) for row in conn.execute(select(self.memory_items.c.id, self.memory_items.c.content,
                self.memory_items.c.fact_key).where(self.memory_items.c.bot_id == bot_id,
                self.memory_items.c.conversation_id == cid, self.memory_items.c.status == "active",
                or_(self.memory_items.c.expires_at.is_(None), self.memory_items.c.expires_at > now))
                .order_by(self.memory_items.c.updated_at.desc()).limit(8)).mappings()]
            payload = {"utc_now": str(now), "subject": conversation["user_id"], "allow_facts": allow_facts,
                       "allow_shared": is_owner and conversation["source"] == "qq_group", "summary": clip_bytes(state["summary"], 1200),
                       "old_items": [], "messages": []}
            for item in old:
                if token_bound(json_text(payload["old_items"] + [item])) > 1000:
                    break
                payload["old_items"].append(item)
            segments = []
            for row in eligible[:20]:
                # 按字符记录分段位置，字节预算仅用于裁剪，不会跳过未处理后缀。
                available = 6000 - token_bound(prompt) - token_bound(json_text(payload)) - 512
                if available < 100:
                    break
                user_text = clip_bytes(row["text"][row["position"]:], available)
                if not user_text:
                    continue
                message = {"record_id": row["record_id"], "at": str(row["received_at"]), "user": user_text}
                # JSON 转义也消耗输入预算，最终以实际序列化文本核对。
                while token_bound(prompt) + token_bound(json_text({**payload, "messages": payload["messages"] + [message]})) + 256 > 6000:
                    user_text = user_text[:len(user_text) // 2]
                    if not user_text:
                        break
                    message["user"] = user_text
                if not user_text:
                    break
                end = row["position"] + len(user_text)
                # 摘要可参考已发送回复；预算不足则只总结用户原话。
                if end == len(row["text"]) and row["record_status"] == "replied" and row["answer"]:
                    reply_budget = 6000 - token_bound(prompt) - token_bound(json_text(payload)) - token_bound(json_text(message)) - 512
                    if reply_budget >= token_bound(json_text(row["answer"])):
                        message["delivered_reply"] = row["answer"]
                payload["messages"].append(message)
                segments.append({"id": row["id"], "record_id": row["record_id"], "end": end, "complete": end == len(row["text"])})
            if not segments:
                return None
            return {"bot_id": bot_id, "conversation_id": cid, "generation": state["generation"],
                    "config_version": setting["version"], "owner_version": setting["owner_version"],
                    "provider_id": cfg.provider_id, "payload": payload, "segments": segments,
                    "input_bound": token_bound(prompt) + token_bound(json_text(payload)) + 256}

    def reserve_memory_run(self, batch: dict, model_name: str) -> int | None:
        bot_id, cid = batch["bot_id"], batch["conversation_id"]
        with self.engine.begin() as conn:
            # 固定锁顺序：全局设置 -> 机器人。实际模型调用必须在事务外。
            conn.execute(select(self.settings.c.id).where(self.settings.c.id == 1).with_for_update()).one()
            bot = self._memory_bot_lock(conn, bot_id)
            setting = self._memory_settings(conn, bot_id)
            state = self._memory_state(conn, bot_id, cid)
            cfg = MemoryConfig.model_validate_json(setting["config"])
            if (not bot["enabled"] or not cfg.enabled or not cfg.automatic or setting["paused_reason"]
                    or [batch["generation"], batch["config_version"], batch["owner_version"]]
                    != [state["generation"], setting["version"], setting["owner_version"]]):
                return None
            ids = [part["id"] for part in batch["segments"]]
            count = conn.execute(select(func.count()).select_from(self.memory_sources).where(
                self.memory_sources.c.bot_id == bot_id, self.memory_sources.c.conversation_id == cid,
                self.memory_sources.c.id.in_(ids), self.memory_sources.c.status == "pending")).scalar_one()
            if count != len(ids):
                return None
            now = utc_now()
            day = (now + timedelta(hours=8)).date()
            reserve = batch["input_bound"] + 800
            if batch["input_bound"] > 6000:
                raise ValueError("整理输入超过预算")
            t = self.memory_runs
            for conditions, calls, tokens in (([t.c.budget_date == day], 60, 180000),
                    ([t.c.budget_date == day, t.c.bot_id == bot_id], cfg.daily_calls, cfg.daily_tokens)):
                used = conn.execute(select(func.coalesce(func.sum(t.c.call_count), 0),
                    func.coalesce(func.sum(t.c.charged_tokens), 0)).where(*conditions)).one()
                if used[0] >= calls or used[1] + reserve > tokens:
                    tomorrow = (now + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1, hours=-8)
                    conn.execute(self.memory_states.update().where(self.memory_states.c.id == state["id"])
                        .values(reason="日预算不足，次日继续", next_run_at=tomorrow))
                    return None
            run_id = conn.execute(t.insert().values(bot_id=bot_id, conversation_id=cid, generation=state["generation"],
                config_version=setting["version"], owner_version=setting["owner_version"], budget_date=day,
                status="reserved", source_ids=json_text(batch["segments"]), request_hash=digest(json_text(batch["payload"])),
                provider_id=batch["provider_id"], model_name=model_name, reserved_tokens=reserve,
                charged_tokens=reserve, call_count=1, created_at=now)).inserted_primary_key[0]
            conn.execute(self.memory_sources.update().where(self.memory_sources.c.bot_id == bot_id,
                self.memory_sources.c.id.in_(ids)).values(status="claimed", run_id=run_id))
            conn.execute(self.memory_states.update().where(self.memory_states.c.id == state["id"])
                .values(last_run_at=now, next_run_at=now + timedelta(minutes=cfg.cooldown_minutes), reason="整理中"))
            return run_id

    def memory_start_call(self, bot_id: int, run_id: int) -> bool:
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            result = conn.execute(self.memory_runs.update().where(self.memory_runs.c.bot_id == bot_id,
                self.memory_runs.c.id == run_id, self.memory_runs.c.status == "reserved").values(status="calling"))
            return bool(result.rowcount)

    def memory_finish_call(self, bot_id: int, run_id: int, *, result: dict | None = None,
                            usage: dict | None = None, error: str = "", unknown: bool = False,
                            not_sent: bool = False) -> None:
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            t = self.memory_runs
            row = conn.execute(select(t).where(t.c.bot_id == bot_id, t.c.id == run_id)).mappings().one()
            # 提交确认丢失后再次保存，不能把已经落库的候选降级成失败而丢失结果。
            if row["status"] in ("result", "applied"):
                return
            usage = usage or {}
            total = usage.get("total_tokens")
            invalid = row["status"] == "invalidated"
            state = "invalidated" if invalid else "result" if result is not None else "call_unknown" if unknown else "failed"
            values = dict(status=state, result=json_text(result) if result is not None and not invalid else None,
                input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"), total_tokens=total,
                charged_tokens=0 if not_sent else total if total is not None else row["reserved_tokens"],
                call_count=0 if not_sent else 1, error=error[:200], finished_at=utc_now())
            conn.execute(t.update().where(t.c.id == run_id, t.c.bot_id == bot_id).values(**values))
            if not invalid and result is None:
                conn.execute(self.memory_sources.update().where(self.memory_sources.c.bot_id == bot_id,
                    self.memory_sources.c.run_id == run_id, self.memory_sources.c.status == "claimed")
                    .values(status="pending" if not_sent else "blocked", run_id=None if not_sent else run_id))
            if total is not None and total > row["reserved_tokens"]:
                conn.execute(self.memory_settings.update().where(self.memory_settings.c.bot_id == bot_id)
                    .values(paused_reason="实际用量超过预留，自动整理已暂停，请检查模型预算兼容性"))
            if error and not invalid:
                conn.execute(self.memory_states.update().where(self.memory_states.c.bot_id == bot_id,
                    self.memory_states.c.conversation_id == row["conversation_id"]).values(reason=error[:200]))

    def memory_pause(self, bot_id: int, reason: str) -> None:
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            conn.execute(self.memory_settings.update().where(self.memory_settings.c.bot_id == bot_id)
                         .values(paused_reason=reason[:200]))

    def memory_ready_results(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(self.memory_runs.c.id, self.memory_runs.c.bot_id)
                .where(self.memory_runs.c.status == "result").order_by(self.memory_runs.c.id).limit(20)).mappings()]

    def apply_memory_result(self, bot_id: int, run_id: int) -> None:
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            t, src, m = self.memory_runs, self.memory_sources, self.memory_items
            run = conn.execute(select(t).where(t.c.bot_id == bot_id, t.c.id == run_id)).mappings().one()
            if run["status"] != "result":
                return
            settings = self._memory_settings(conn, bot_id)
            state = self._memory_state(conn, bot_id, run["conversation_id"])
            if [run["generation"], run["config_version"], run["owner_version"]] != [state["generation"], settings["version"], settings["owner_version"]]:
                conn.execute(t.update().where(t.c.id == run_id).values(status="invalidated", result=None))
                conn.execute(src.update().where(src.c.bot_id == bot_id, src.c.run_id == run_id).values(status="cancelled"))
                return
            parsed = MemoryResult.model_validate_json(run["result"])
            segments = json.loads(run["source_ids"])
            records = {part["record_id"] for part in segments}
            conversation = self._memory_conversation(conn, bot_id, run["conversation_id"])
            owner = self._memory_owner(conn, bot_id) == conversation["user_id"]
            source_rows = conn.execute(select(src).where(src.c.bot_id == bot_id, src.c.run_id == run_id)).mappings().all()
            allow_facts = conversation["source"] == "qq_c2c" or owner and all(row["is_owner"] for row in source_rows)
            for item in parsed.items:
                if not allow_facts or not set(item.source_ids).issubset(records) or any(value <= 0 for value in item.source_ids):
                    raise ValueError("候选来源或提取权限不符")
                if item.shared and (not owner or conversation["source"] != "qq_group") or item.category == "request" and not owner:
                    raise ValueError("候选共享范围或主人资格不符")
                scope = "group" if item.shared else "personal"
                if item.expires_at and item.expires_at <= utc_now().replace(tzinfo=timezone.utc):
                    continue
                tombstone = conn.execute(select(m.c.id).where(m.c.bot_id == bot_id,
                    m.c.conversation_id == conversation["id"], m.c.status == "forgotten",
                    or_(m.c.fact_key == item.fact_key, m.c.content_hash == digest(" ".join(item.content.split())))).limit(1)).first()
                if tombstone:
                    continue
                status = "active"
                conflict = conn.execute(select(m).where(m.c.bot_id == bot_id,
                    m.c.conversation_id == conversation["id"], m.c.fact_key == item.fact_key,
                    m.c.scope == scope, m.c.status == "active")).mappings().first()
                if item.replace_id:
                    target = conn.execute(select(m).where(m.c.bot_id == bot_id, m.c.id == item.replace_id,
                        m.c.conversation_id == conversation["id"], m.c.scope == scope,
                        m.c.fact_key == item.fact_key, m.c.status == "active")).mappings().first()
                    if not target:
                        raise ValueError("候选替换目标不属于当前范围")
                    # 模型不能静默覆盖管理员或用户显式确定的内容。
                    if target["origin"] in ("explicit", "admin"):
                        status = "pending"
                    else:
                        conn.execute(m.update().where(m.c.bot_id == bot_id, m.c.id == target["id"]).values(status="superseded"))
                elif conflict and conflict["content_hash"] != digest(" ".join(item.content.split())):
                    status = "pending"
                values = dict(conversation_id=conversation["id"], scope=scope, category=item.category,
                              fact_key=item.fact_key, content=item.content, expires_at=item.expires_at)
                inserted = self._memory_put(conn, bot_id, values, origin="automatic", sources=item.source_ids,
                    owner_version=run["owner_version"] if owner else 0, status=status)
                if item.replace_id:
                    conn.execute(m.update().where(m.c.bot_id == bot_id, m.c.id == inserted).values(replaced_id=item.replace_id))
            for part in segments:
                conn.execute(src.update().where(src.c.bot_id == bot_id, src.c.id == part["id"], src.c.run_id == run_id)
                    .values(status="done" if part["complete"] else "pending", position=part["end"], run_id=None))
            # 有失败空洞或分段未完成时不把旧摘要当成连续历史边界。
            through = max(records)
            hole = conn.execute(select(src.c.id).where(src.c.bot_id == bot_id, src.c.conversation_id == conversation["id"],
                src.c.record_id <= through, src.c.generation == state["generation"],
                src.c.status.in_(("pending", "claimed", "blocked"))).limit(1)).first()
            conn.execute(self.memory_states.update().where(self.memory_states.c.id == state["id"])
                .values(summary=parsed.summary, summary_through=state["summary_through"] if hole else through, reason="整理完成"))
            conn.execute(t.update().where(t.c.bot_id == bot_id, t.c.id == run_id)
                .values(status="applied", result=None, finished_at=utc_now()))

    def memory_dashboard(self, bot_id: int) -> dict:
        t, s = self.memory_runs, self.memory_sources
        day = (utc_now() + timedelta(hours=8)).date()
        with self.engine.connect() as conn:
            def totals(filters):
                row = conn.execute(select(func.coalesce(func.sum(t.c.call_count), 0).label("calls"),
                    func.coalesce(func.sum(t.c.charged_tokens), 0).label("budget_used"),
                    func.sum(t.c.total_tokens).label("actual_tokens"),
                    func.coalesce(func.sum(and_(t.c.call_count > 0, t.c.total_tokens.is_(None))), 0).label("unknown_calls"))
                    .where(t.c.budget_date == day, *filters)).mappings().one()
                return dict(row)
            runs = conn.execute(select(t.c.id, t.c.conversation_id, t.c.status, t.c.model_name, t.c.created_at,
                t.c.reserved_tokens, t.c.input_tokens, t.c.output_tokens, t.c.total_tokens, t.c.error)
                .where(t.c.bot_id == bot_id).order_by(t.c.id.desc()).limit(30)).mappings().all()
            pending = conn.execute(select(s.c.status, func.count().label("count")).where(s.c.bot_id == bot_id)
                                   .group_by(s.c.status)).mappings().all()
            states = conn.execute(select(self.memory_states.c.conversation_id, self.memory_states.c.reason,
                self.memory_states.c.last_run_at, self.memory_states.c.next_run_at).where(self.memory_states.c.bot_id == bot_id)
                .order_by(self.memory_states.c.id.desc()).limit(30)).mappings().all()
            return {"date": day, "bot": totals([t.c.bot_id == bot_id]), "global": totals([]),
                    "runs": [dict(row) for row in runs], "sources": [dict(row) for row in pending], "states": [dict(row) for row in states]}

    def retry_memory_run(self, bot_id: int, run_id: int) -> None:
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            run = conn.execute(select(self.memory_runs).where(self.memory_runs.c.bot_id == bot_id,
                self.memory_runs.c.id == run_id)).mappings().first()
            if not run or run["status"] not in ("failed", "call_unknown"):
                raise ValueError("只能重试失败或结果未知的批次")
            state = self._memory_state(conn, bot_id, run["conversation_id"])
            setting = self._memory_settings(conn, bot_id)
            if [run["generation"], run["config_version"], run["owner_version"]] != [state["generation"], setting["version"], setting["owner_version"]]:
                raise ValueError("批次已过期，不能恢复旧来源")
            conn.execute(self.memory_sources.update().where(self.memory_sources.c.bot_id == bot_id,
                self.memory_sources.c.run_id == run_id, self.memory_sources.c.status == "blocked")
                .values(status="pending", run_id=None))
            conn.execute(self.memory_runs.update().where(self.memory_runs.c.bot_id == bot_id, self.memory_runs.c.id == run_id)
                .values(status="retry_requested", result=None))
            conn.execute(self.memory_settings.update().where(self.memory_settings.c.bot_id == bot_id).values(paused_reason=""))

    def backfill_memory(self, bot_id: int, cid: int, after_id: int, limit: int) -> int:
        from qbot.memory_config import memory_command
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            setting = self._memory_settings(conn, bot_id)
            if not MemoryConfig.model_validate_json(setting["config"]).enabled:
                raise ValueError("请先启用记忆")
            state = self._memory_state(conn, bot_id, cid)
            conversation = self._memory_conversation(conn, bot_id, cid)
            # 初次启用前历史只可个人回填；认主前消息不获群共享资格。
            if state["generation"] > 1 and after_id < state["boundary_id"]:
                raise ValueError("不能回填重置或遗忘边界之前的记录")
            r, s = self.records, self.memory_sources
            rows = conn.execute(select(r.c.id, r.c.question, r.c.received_at).where(r.c.bot_id == bot_id,
                r.c.conversation_id == cid, r.c.id > after_id, r.c.status.in_(("replied", "model_failed", "send_failed", "delivery_unknown")),
                r.c.record_type == "chat", ~r.c.external_message_id.like("admin-send:%"),
                ~r.c.external_message_id.like("scheduled:%"), ~r.c.external_message_id.like("scheduled-manual:%"))
                .order_by(r.c.id).limit(limit)).mappings().all()
            count = 0
            if state["generation"] == 1 and after_id < state["boundary_id"]:
                conn.execute(self.memory_states.update().where(self.memory_states.c.id == state["id"]).values(boundary_id=after_id))
            for row in rows:
                if memory_command(row["question"]) or conn.execute(select(s.c.id).where(s.c.bot_id == bot_id, s.c.record_id == row["id"])).first():
                    continue
                conn.execute(s.insert().values(bot_id=bot_id, conversation_id=cid, record_id=row["id"], generation=state["generation"],
                    config_version=setting["version"], owner_version=setting["owner_version"], is_owner=False,
                    status="pending", position=0, created_at=utc_now()))
                count += 1
            return count

    def request_memory_run(self, bot_id: int, cid: int) -> None:
        with self.engine.begin() as conn:
            self._memory_bot_lock(conn, bot_id)
            self._memory_conversation(conn, bot_id, cid)
            setting = self._memory_settings(conn, bot_id)
            config = MemoryConfig.model_validate_json(setting["config"])
            if not config.enabled or not config.automatic or setting["paused_reason"]:
                raise ValueError("请先启用自动整理，并处理暂停原因")
            state = self._memory_state(conn, bot_id, cid)
            conn.execute(self.memory_states.update().where(self.memory_states.c.id == state["id"])
                .values(reason="管理员请求整理", next_run_at=None))
