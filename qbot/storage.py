import asyncio
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import MetaData, create_engine, func, select
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from qbot.config import Settings


class StorageError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mysql_engine(settings: Settings) -> Engine:
    return create_engine(
        URL.create("mysql+pymysql", username=settings.mysql_user,
                   password=settings.mysql_password.get_secret_value(),
                   host=settings.mysql_host, port=settings.mysql_port,
                   database=settings.mysql_database, query={"charset": "utf8mb4"}),
        pool_pre_ping=True, pool_recycle=1800, hide_parameters=True,
        connect_args={"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10,
                      "init_command": "SET time_zone = '+00:00'"},
    )


class Store:
    """同步事务集中管理，异步调用方通过 call 在线程池执行。"""

    def __init__(self, engine: Engine, encryption_key: str):
        self.engine = engine
        self.cipher = Fernet(encryption_key.encode())
        metadata = MetaData()
        try:
            metadata.reflect(engine, only=["model_providers", "app_settings", "conversations", "chat_records", "bots",
                                           "interaction_greetings", "interaction_greeting_runs"])
            self.greetings = metadata.tables["interaction_greetings"]
            self.greeting_runs = metadata.tables["interaction_greeting_runs"]
            self.bots = metadata.tables["bots"]
            self.providers = metadata.tables["model_providers"]
            self.settings = metadata.tables["app_settings"]
            self.conversations = metadata.tables["conversations"]
            self.records = metadata.tables["chat_records"]
            with engine.connect() as conn:
                if conn.execute(select(self.settings.c.id).where(self.settings.c.id == 1)).first() is None:
                    raise StorageError("请先执行 python -m alembic upgrade head 初始化配置行")
        except SQLAlchemyError:
            raise StorageError("数据库连接或表结构不可用，请检查配置并执行 python -m alembic upgrade head") from None

    async def call(self, method: str, *args, **kwargs):
        try:
            return await asyncio.to_thread(getattr(self, method), *args, **kwargs)
        except SQLAlchemyError:
            raise StorageError("数据库操作失败，请检查连接及表结构") from None

    def list_bots(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.bots).order_by(self.bots.c.id)).mappings()
            items = []
            for row in rows:
                item = dict(row)
                encrypted = item.pop("app_secret_encrypted")
                item.update(has_secret=bool(encrypted), secret_mask="••••••••" if encrypted else "未配置")
                items.append(item)
            return items

    def bot_config(self, bot_id: int) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.bots).where(self.bots.c.id == bot_id)).mappings().first()
        if row is None:
            raise ValueError("机器人不存在")
        item = dict(row)
        encrypted = item.pop("app_secret_encrypted")
        try:
            item["app_secret"] = self.cipher.decrypt(encrypted.encode()).decode() if encrypted else ""
        except InvalidToken:
            raise ValueError("机器人密钥解密失败，请检查加密主密钥") from None
        return item

    def save_bot(self, values: dict, bot_id: int | None = None) -> int:
        values = dict(values)
        secret = values.pop("app_secret", "")
        try:
            with self.engine.begin() as conn:
                # 和供应商修改共用设置行锁，避免并发绑定已被停用的供应商。
                conn.execute(select(self.settings.c.id).where(self.settings.c.id == 1).with_for_update()).one()
                old = None
                if bot_id is not None:
                    old = conn.execute(select(self.bots).where(self.bots.c.id == bot_id).with_for_update()).mappings().first()
                    if old is None:
                        raise ValueError("机器人不存在")
                if not secret and (old is None or old["app_id"] != values["app_id"] or not old["app_secret_encrypted"]):
                    raise ValueError("首次配置或更换 AppID 时必须填写 AppSecret")
                if values.get("provider_id") is not None:
                    provider = conn.execute(select(self.providers).where(self.providers.c.id == values["provider_id"])).mappings().first()
                    if not provider or not provider["enabled"] or not provider["api_key_encrypted"]:
                        raise ValueError("请选择已启用且已配置密钥的供应商")
                    if provider["model_type"] != "chat":
                        raise ValueError("机器人只能绑定 chat 类型模型")
                if values["enabled"] and values.get("provider_id") is None:
                    default = conn.execute(select(self.settings.c.default_provider_id).where(self.settings.c.id == 1)).scalar_one()
                    if default is None:
                        raise ValueError("启用前请选择供应商或设置全局默认供应商")
                if secret:
                    values["app_secret_encrypted"] = self.cipher.encrypt(secret.encode()).decode()
                values["updated_at"] = utc_now()
                if bot_id is None:
                    values["created_at"] = utc_now()
                    return conn.execute(self.bots.insert().values(**values)).inserted_primary_key[0]
                if old["app_id"] != values["app_id"]:
                    conn.execute(self.greetings.update().where(self.greetings.c.bot_id == bot_id).values(enabled=False))
                conn.execute(self.bots.update().where(self.bots.c.id == bot_id).values(**values))
                return bot_id
        except IntegrityError:
            raise ValueError("AppID 已被其他机器人使用或供应商配置无效") from None

    def set_bot_enabled(self, bot_id: int, enabled: bool) -> None:
        if enabled:
            config = self.bot_config(bot_id)
            if not config["app_id"] or not config["app_secret"]:
                raise ValueError("请先填写机器人 AppID 和 AppSecret")
            if self.active_provider(bot_id) is None:
                raise ValueError("请先选择模型供应商")
        with self.engine.begin() as conn:
            if conn.execute(select(self.bots.c.id).where(self.bots.c.id == bot_id)).first() is None:
                raise ValueError("机器人不存在")
            conn.execute(self.bots.update().where(self.bots.c.id == bot_id).values(enabled=enabled, updated_at=utc_now()))

    def list_providers(self) -> dict:
        with self.engine.connect() as conn:
            default_id = conn.execute(select(self.settings.c.default_provider_id)).scalar_one()
            items = []
            for row in conn.execute(select(self.providers).order_by(self.providers.c.id)).mappings():
                item = dict(row)
                encrypted = item.pop("api_key_encrypted")
                item["has_api_key"] = bool(encrypted)
                item["api_key_mask"] = "••••••••" if encrypted else "未配置"
                items.append(item)
            return {"items": items, "default_provider_id": default_id}

    def provider_for_probe(self, provider_id: int) -> dict:
        """仅供服务端显式连通性检查使用，允许检查尚未启用的供应商。"""
        with self.engine.connect() as conn:
            row = conn.execute(select(self.providers).where(self.providers.c.id == provider_id)).mappings().first()
        if row is None:
            raise ValueError("供应商不存在")
        if row["model_type"] != "chat":
            raise ValueError("当前连接测试仅支持 chat 类型模型")
        result = dict(row)
        encrypted = result.pop("api_key_encrypted")
        if not encrypted:
            raise ValueError("请先保存 API Key")
        try:
            result["api_key"] = self.cipher.decrypt(encrypted.encode()).decode()
        except InvalidToken:
            raise ValueError("密钥解密失败，请检查加密主密钥") from None
        return result

    def save_provider(self, values: dict, provider_id: int | None = None) -> int:
        values = dict(values)
        key = values.pop("api_key", "")
        values.setdefault("model_type", "chat")
        if values["model_type"] not in ("chat", "image", "asr", "tts"):
            raise ValueError("无效的模型类型")
        try:
            with self.engine.begin() as conn:
                # 所有默认项和启停修改先锁同一设置行，避免并发停用默认项。
                default_id = conn.execute(select(self.settings.c.default_provider_id).with_for_update()).scalar_one()
                old = None
                if provider_id is not None:
                    old = conn.execute(select(self.providers).where(self.providers.c.id == provider_id)).mappings().first()
                    if old is None:
                        raise ValueError("供应商不存在")
                encrypted = self.cipher.encrypt(key.encode()).decode() if key else (old["api_key_encrypted"] if old else None)
                if values["enabled"] and not encrypted:
                    raise ValueError("启用供应商前必须配置 API Key")
                if provider_id == default_id and default_id is not None and values["model_type"] != "chat":
                    raise ValueError("请先切换默认聊天供应商，再修改模型类型")
                if provider_id == default_id and default_id is not None and not values["enabled"]:
                    raise ValueError("请先切换默认供应商，再停用该供应商")
                # 已绑定机器人的模型必须保持可用的聊天类型。
                if provider_id is not None and (not values["enabled"] or values["model_type"] != "chat"):
                    bound = conn.execute(select(self.bots.c.id).where(self.bots.c.provider_id == provider_id).limit(1)).first()
                    if bound:
                        raise ValueError("该供应商已绑定机器人，请先调整机器人配置")
                values.update(api_key_encrypted=encrypted, updated_at=utc_now())
                if provider_id is None:
                    values["created_at"] = utc_now()
                    return conn.execute(self.providers.insert().values(**values)).inserted_primary_key[0]
                conn.execute(self.providers.update().where(self.providers.c.id == provider_id).values(**values))
                return provider_id
        except IntegrityError:
            raise ValueError("供应商名称重复或配置不符合数据库约束") from None

    def set_default(self, provider_id: int | None) -> None:
        with self.engine.begin() as conn:
            conn.execute(select(self.settings.c.id).with_for_update()).scalar_one()
            if provider_id is None:
                following = conn.execute(select(self.bots.c.id).where(
                    (self.bots.c.provider_id.is_(None)) & (self.bots.c.enabled == 1)).limit(1)).first()
                if following:
                    raise ValueError("仍有启用的机器人跟随默认供应商，请先调整其配置")
            if provider_id is not None:
                provider = conn.execute(select(self.providers).where(self.providers.c.id == provider_id)).mappings().first()
                if not provider or not provider["enabled"] or not provider["api_key_encrypted"]:
                    raise ValueError("只能选择已启用且已配置密钥的供应商")
                if provider["model_type"] != "chat":
                    raise ValueError("默认聊天供应商必须是 chat 类型")
            conn.execute(self.settings.update().where(self.settings.c.id == 1).values(
                default_provider_id=provider_id, updated_at=utc_now()))

    def active_provider(self, bot_id: int | None = None) -> dict | None:
        with self.engine.connect() as conn:
            provider_id = None
            if bot_id is not None:
                provider_id = conn.execute(select(self.bots.c.provider_id).where(self.bots.c.id == bot_id)).scalar_one()
            if provider_id is None:
                provider_id = conn.execute(select(self.settings.c.default_provider_id)).scalar_one()
            if provider_id is None:
                return None
            row = conn.execute(select(self.providers).where(self.providers.c.id == provider_id)).mappings().first()
            if not row or not row["enabled"] or not row["api_key_encrypted"]:
                raise ValueError("默认供应商无效，请在后台修复配置")
            if row["model_type"] != "chat":
                raise ValueError("聊天供应商必须是 chat 类型，请在后台修复配置")
            result = dict(row)
            try:
                result["api_key"] = self.cipher.decrypt(result.pop("api_key_encrypted").encode()).decode()
            except InvalidToken:
                raise ValueError("供应商密钥解密失败，请检查加密主密钥") from None
            return result

    def receive(self, source: str, group_id: str, user_id: str, message_id: str,
                question: str, sent_at: datetime | None = None, *, bot_id: int = 1) -> tuple[int, bool]:
        """接收记录和会话同事务提交；唯一索引阻止重复推送创建新记录。"""
        if source not in ("qq_group", "qq_c2c", "local"):
            raise ValueError("无效的聊天来源")
        for value, limit in ((group_id, 191), (user_id, 256), (message_id, 512)):
            if not value or len(value) > limit or any(not 33 <= ord(char) <= 126 for char in value):
                raise ValueError("消息标识必须是长度范围内的可打印 ASCII 字符")
        r, c = self.records, self.conversations
        identity = (r.c.bot_id == bot_id) & (r.c.source == source) & (r.c.group_id == group_id) & (r.c.external_message_id == message_id)
        for attempt in range(3):
            try:
                with self.engine.begin() as conn:
                    old = conn.execute(select(r.c.id).where(identity)).scalar_one_or_none()
                    if old is not None:
                        return old, False
                    now = utc_now()
                    cid = conn.execute(select(c.c.id).where(
                        (c.c.bot_id == bot_id) & (c.c.source == source) & (c.c.group_id == group_id) & (c.c.user_id == user_id)
                    )).scalar_one_or_none()
                    if cid is None:
                        cid = conn.execute(c.insert().values(bot_id=bot_id, source=source, group_id=group_id, user_id=user_id,
                            created_at=now, last_active_at=now)).inserted_primary_key[0]
                    else:
                        conn.execute(c.update().where(c.c.id == cid).values(last_active_at=now))
                    rid = conn.execute(r.insert().values(bot_id=bot_id, conversation_id=cid, source=source,
                        group_id=group_id, external_message_id=message_id, question=question,
                        record_type="reset" if question.strip() == "/reset" else "chat", status="pending",
                        message_sent_at=sent_at.astimezone(timezone.utc).replace(tzinfo=None) if sent_at else None,
                        received_at=now, updated_at=now)).inserted_primary_key[0]
                    return rid, True
            except IntegrityError:
                if attempt == 2:
                    raise
        raise StorageError("消息入库失败")

    def update_record(self, record_id: int, **values) -> None:
        if "status" in values and values["status"] not in (
            "pending", "processing", "replied", "model_failed", "send_failed",
            "delivery_unknown", "expired", "rejected",
        ):
            raise ValueError("无效的消息状态")
        if "record_type" in values and values["record_type"] not in ("chat", "reset"):
            raise ValueError("无效的记录类型")
        values["updated_at"] = utc_now()
        if values.get("status") not in (None, "pending", "processing"):
            values["completed_at"] = utc_now()
        with self.engine.begin() as conn:
            conn.execute(self.records.update().where(self.records.c.id == record_id).values(**values))

    def query_records(self, *, page: int = 1, page_size: int = 20, source: str = "",
                      group_id: str = "", user_id: str = "", status: str = "",
                      start: datetime | None = None, end: datetime | None = None, bot_id: int | None = None) -> dict:
        r, c = self.records, self.conversations
        joined = r.join(c, r.c.conversation_id == c.c.id)
        filters = []
        if bot_id is not None:
            filters.append(r.c.bot_id == bot_id)
        for col, value in [(r.c.source, source), (r.c.group_id, group_id), (c.c.user_id, user_id), (r.c.status, status)]:
            if value:
                filters.append(col == value)
        if start:
            filters.append(r.c.received_at >= start)
        if end:
            filters.append(r.c.received_at <= end)
        columns = [r.c.id, r.c.bot_id, r.c.source, r.c.group_id, c.c.user_id, r.c.status, r.c.received_at,
                   r.c.model_name_snapshot, func.substr(r.c.question, 1, 120).label("question")]
        with self.engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(joined).where(*filters)).scalar_one()
            rows = conn.execute(select(*columns).select_from(joined).where(*filters)
                .order_by(r.c.received_at.desc(), r.c.id.desc()).offset((page - 1) * page_size).limit(page_size)).mappings()
            return {"items": [dict(row) for row in rows], "total": count, "page": page, "page_size": page_size}

    def send_targets(self, bot_id: int, source: str, page: int = 1) -> dict:
        """按实际投递范围去重；群内不同用户只显示一个群目标。"""
        r, c = self.records, self.conversations
        latest = select(func.max(r.c.id).label("record_id")).where(
            r.c.bot_id == bot_id, r.c.source == source,
        ).group_by(r.c.group_id).subquery()
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(latest)).scalar_one()
            rows = conn.execute(select(r.c.id, r.c.bot_id, r.c.source, r.c.group_id,
                c.c.user_id, c.c.contact_name).select_from(
                    latest.join(r, r.c.id == latest.c.record_id).join(c, c.c.id == r.c.conversation_id)
                ).order_by(r.c.id.desc()).offset((page - 1) * 50).limit(50)).mappings()
            return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": 50}

    def record_detail(self, record_id: int) -> dict | None:
        r, c = self.records, self.conversations
        with self.engine.connect() as conn:
            row = conn.execute(select(r, c.c.user_id, c.c.member_name, c.c.family_role, c.c.qq_number_note, c.c.contact_name, c.c.contact_qq).join(c, r.c.conversation_id == c.c.id)
                               .where(r.c.id == record_id)).mappings().first()
            return dict(row) if row else None

    def save_family_binding(self, bot_id: int, record_id: int, values: dict | None) -> dict:
        """仅从已有 QQ 记录取身份，客户端不能指定或替换 OpenID。"""
        r, c = self.records, self.conversations
        with self.engine.begin() as conn:
            row = conn.execute(select(c).select_from(r.join(c, r.c.conversation_id == c.c.id)).where(
                r.c.id == record_id, r.c.bot_id == bot_id, c.c.bot_id == bot_id
            ).with_for_update()).mappings().first()
            if row is None:
                raise ValueError("该机器人下不存在这条消息")
            if row["source"] not in ("qq_group", "qq_c2c"):
                raise ValueError("仅支持从 QQ 消息绑定家庭成员")
            binding = {"member_name": None, "family_role": None, "qq_number_note": None}
            if values is not None:
                name = values.get("member_name", "").strip()
                role = values.get("family_role")
                note = values.get("qq_number_note", "").strip()
                if not name or len(name) > 100 or any(ord(ch) < 32 for ch in name):
                    raise ValueError("姓名需为 1 至 100 字符且不能包含控制字符")
                if role not in ("father", "mother", "friend"):
                    raise ValueError("无效的家庭角色")
                if len(note) > 20 or any(ch not in "0123456789" for ch in note):
                    raise ValueError("QQ 备注只能填写最多 20 位数字")
                binding.update(member_name=name, family_role=role, qq_number_note=note or None)
            conn.execute(c.update().where(c.c.id == row["id"], c.c.bot_id == bot_id).values(**binding))
            return {"bot_id": bot_id, "source": row["source"], "group_id": row["group_id"],
                    "user_id": row["user_id"], **binding}

    def family_identity(self, bot_id: int, record_id: int) -> dict | None:
        r, c = self.records, self.conversations
        with self.engine.connect() as conn:
            row = conn.execute(select(c.c.member_name, c.c.family_role).select_from(
                r.join(c, r.c.conversation_id == c.c.id)).where(
                    r.c.id == record_id, r.c.bot_id == bot_id, c.c.bot_id == bot_id,
                    c.c.source.in_(("qq_group", "qq_c2c"))
                )).mappings().first()
            return dict(row) if row and row["family_role"] else None

    def greeting_settings(self, bot_id: int, record_id: int) -> dict:
        from qbot.greetings import default_rules
        record = self.record_detail(record_id)
        if not record or record["bot_id"] != bot_id or record["source"] not in ("qq_c2c", "qq_group"):
            raise ValueError("该机器人下不存在这条 QQ 消息")
        g, runs = self.greetings, self.greeting_runs
        with self.engine.connect() as conn:
            rows = conn.execute(select(g).where(g.c.bot_id == bot_id,
                g.c.conversation_id == record["conversation_id"])).mappings().all()
            rules = {r["slot"]: dict(slot=r["slot"], enabled=bool(r["enabled"]), start=r["start_time"],
                end=r["end_time"], weekdays=[int(d) for d in r["weekdays"].split(",")], content=r["content"]) for r in rows}
            history = conn.execute(select(runs.c.local_date, g.c.slot, runs.c.content,
                runs.c.record_id, self.records.c.status, self.records.c.error_message)
                .select_from(runs.join(g, runs.c.greeting_id == g.c.id)
                    .join(self.records, runs.c.record_id == self.records.c.id))
                .where(g.c.bot_id == bot_id, g.c.conversation_id == record["conversation_id"])
                .order_by(runs.c.id.desc()).limit(20)).mappings().all()
        return {"rules": [rules.get(r["slot"], r) for r in default_rules()],
                "bound": bool(record["contact_name"]), "contact_name": record["contact_name"],
                "history": [dict(r) for r in history]}

    def save_greeting_settings(self, bot_id: int, record_id: int, values: dict) -> None:
        from qbot.greetings import GreetingInput
        data = GreetingInput.model_validate(values)
        c, r, g = self.conversations, self.records, self.greetings
        with self.engine.begin() as conn:
            # 和绑定修改共用会话行锁，防止解除绑定时又被并发启用。
            row = conn.execute(select(c).select_from(r.join(c, r.c.conversation_id == c.c.id)).where(
                r.c.id == record_id, r.c.bot_id == bot_id, c.c.bot_id == bot_id
            ).with_for_update()).mappings().first()
            if not row or row["source"] not in ("qq_c2c", "qq_group"):
                raise ValueError("该机器人下不存在这条 QQ 消息")
            if any(rule.enabled for rule in data.rules) and not row["contact_name"]:
                raise ValueError("请先在用户绑定中设置接收人名称，再启用问候")
            for rule in data.rules:
                scope = (g.c.bot_id == bot_id) & (g.c.conversation_id == row["id"]) & (g.c.slot == rule.slot)
                fields = dict(enabled=rule.enabled, start_time=rule.start, end_time=rule.end,
                              weekdays=",".join(map(str, rule.weekdays)), content=rule.content)
                if conn.execute(select(g.c.id).where(scope)).first():
                    conn.execute(g.update().where(scope).values(**fields))
                else:
                    conn.execute(g.insert().values(bot_id=bot_id, conversation_id=row["id"], slot=rule.slot, **fields))

    def claim_greeting(self, bot_id: int, record_id: int) -> str:
        """生成成功后、发送前领取；同一时段当天最多尝试一次，未知送达不重发。"""
        from qbot.greetings import local_time
        r, c, g, runs = self.records, self.conversations, self.greetings, self.greeting_runs
        with self.engine.begin() as conn:
            row = conn.execute(select(c, r.c.received_at).select_from(r.join(c, r.c.conversation_id == c.c.id))
                .where(r.c.id == record_id, r.c.bot_id == bot_id, c.c.bot_id == bot_id)
                .with_for_update()).mappings().first()
            if not row or row["source"] not in ("qq_c2c", "qq_group") or not row["contact_name"]:
                return ""
            enabled = conn.execute(select(self.bots.c.enabled).where(self.bots.c.id == bot_id)).scalar_one()
            if not enabled:
                return ""
            received, now = local_time(row["received_at"]), local_time(utc_now())
            if received.date() != now.date():
                return ""
            rules = conn.execute(select(g).where(g.c.bot_id == bot_id,
                g.c.conversation_id == row["id"], g.c.enabled == 1).order_by(g.c.id)).mappings().all()
            for rule in rules:
                if (received.isoweekday() not in [int(d) for d in rule["weekdays"].split(",")]
                        or not rule["start_time"] <= received.strftime("%H:%M") < rule["end_time"]
                        or not rule["start_time"] <= now.strftime("%H:%M") < rule["end_time"]):
                    continue
                if conn.execute(select(runs.c.id).where(runs.c.bot_id == bot_id,
                    runs.c.greeting_id == rule["id"], runs.c.local_date == now.date())).first():
                    continue
                conn.execute(runs.insert().values(bot_id=bot_id, greeting_id=rule["id"],
                    local_date=now.date(), record_id=record_id, content=rule["content"], created_at=utc_now()))
                return rule["content"]
            return ""

    def greeting_targets(self, bot_id: int | None = None, page: int = 1, page_size: int = 20) -> dict:
        """独立菜单列出已绑定或曾配置过问候的会话，沿用记录身份校验。"""
        c, r, g = self.conversations, self.records, self.greetings
        latest = select(r.c.bot_id, r.c.conversation_id, func.max(r.c.id).label("record_id"))
        stats = select(g.c.bot_id, g.c.conversation_id, func.sum(g.c.enabled).label("enabled_count"))
        if bot_id is not None:
            latest = latest.where(r.c.bot_id == bot_id)
            stats = stats.where(g.c.bot_id == bot_id)
        latest = latest.group_by(r.c.bot_id, r.c.conversation_id).subquery()
        stats = stats.group_by(g.c.bot_id, g.c.conversation_id).subquery()
        joined = c.join(latest, (latest.c.conversation_id == c.c.id) & (latest.c.bot_id == c.c.bot_id)).outerjoin(
            stats, (stats.c.conversation_id == c.c.id) & (stats.c.bot_id == c.c.bot_id))
        filters = [c.c.source.in_(("qq_c2c", "qq_group")),
                   c.c.contact_name.is_not(None) | stats.c.conversation_id.is_not(None)]
        if bot_id is not None:
            filters.append(c.c.bot_id == bot_id)
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(joined).where(*filters)).scalar_one()
            rows = conn.execute(select(latest.c.record_id.label("id"), c.c.bot_id, c.c.source,
                c.c.group_id, c.c.user_id, c.c.contact_name,
                func.coalesce(stats.c.enabled_count, 0).label("enabled_count"))
                .select_from(joined).where(*filters).order_by(c.c.bot_id, c.c.id)
                .offset((page - 1) * page_size).limit(page_size)).mappings().all()
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def list_user_bindings(self, bot_id: int | None = None, user_id: str = "", page: int = 1) -> dict:
        c, r = self.conversations, self.records
        filters = [c.c.source.in_(("qq_c2c", "qq_group"))]
        if bot_id is not None:
            filters.append(c.c.bot_id == bot_id)
        if user_id:
            filters.append(c.c.user_id == user_id)
        record_id = select(func.max(r.c.id)).where(r.c.bot_id == c.c.bot_id,
            r.c.conversation_id == c.c.id).correlate(c).scalar_subquery()
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(c).where(*filters)).scalar_one()
            rows = conn.execute(select(c.c.bot_id, c.c.source, c.c.group_id, c.c.user_id,
                c.c.contact_name, c.c.contact_qq, record_id.label("record_id"))
                .where(*filters).order_by(c.c.last_active_at.desc(), c.c.id.desc())
                .offset((page - 1) * 20).limit(20)).mappings().all()
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": 20}

    def bind_user_id(self, bot_id: int, values: dict) -> dict:
        """手填 OpenID 只能匹配当前机器人的已知会话，不把数字 QQ 号当身份。"""
        c, r = self.conversations, self.records
        source, user_id, group_id = values["source"], values["user_id"], values.get("group_id", "")
        if source not in ("qq_c2c", "qq_group"):
            raise ValueError("仅支持 QQ 私聊或群聊身份")
        if source == "qq_group" and not group_id:
            raise ValueError("群聊绑定必须填写群 OpenID")
        if source == "qq_c2c" and group_id:
            raise ValueError("私聊绑定不需要群 OpenID")
        filters = [c.c.bot_id == bot_id, c.c.source == source, c.c.user_id == user_id,
                   r.c.bot_id == bot_id]
        if source == "qq_group":
            filters.append(c.c.group_id == group_id)
        with self.engine.connect() as conn:
            rows = conn.execute(select(c.c.id, func.max(r.c.id).label("record_id"))
                .select_from(c.join(r, r.c.conversation_id == c.c.id)).where(*filters)
                .group_by(c.c.id).limit(2)).mappings().all()
        if not rows:
            raise ValueError("未找到匹配的用户消息，请确认机器人、来源和 OpenID，并让用户先发一条消息")
        if len(rows) != 1:
            raise ValueError("该身份对应多个会话，请从聊天记录详情绑定")
        binding = {key: values[key] for key in ("contact_name", "contact_qq")}
        return self.save_contact(bot_id, rows[0]["record_id"], binding)

    def save_contact(self, bot_id: int, record_id: int, values: dict | None) -> dict:
        """维护接收人通讯录，不修改模型的家庭身份。"""
        c, r = self.conversations, self.records
        with self.engine.begin() as conn:
            row = conn.execute(select(c).select_from(r.join(c, r.c.conversation_id == c.c.id)).where(
                r.c.id == record_id, r.c.bot_id == bot_id, c.c.bot_id == bot_id
            ).with_for_update()).mappings().first()
            if not row or row["source"] not in ("qq_c2c", "qq_group"):
                raise ValueError("该机器人下不存在这条 QQ 消息")
            name = values["contact_name"].strip() if values else None
            note = values.get("contact_qq", "").strip() if values else None
            if values and (not name or len(name) > 100 or any(ord(ch) < 32 for ch in name)):
                raise ValueError("接收人名称需为 1 至 100 字符且不能包含控制字符")
            if note and (len(note) > 20 or any(ch not in "0123456789" for ch in note)):
                raise ValueError("QQ 备注只能是最多 20 位数字")
            conn.execute(c.update().where(c.c.id == row["id"], c.c.bot_id == bot_id).values(
                contact_name=name, contact_qq=note or None))
            if not name:
                conn.execute(self.greetings.update().where(self.greetings.c.bot_id == bot_id,
                    self.greetings.c.conversation_id == row["id"]).values(enabled=False))
            return {"contact_name": name, "contact_qq": note or None}
