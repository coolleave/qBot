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
            metadata.reflect(engine, only=["model_providers", "app_settings", "conversations", "chat_records", "bots"])
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
                if provider_id == default_id and default_id is not None and not values["enabled"]:
                    raise ValueError("请先切换默认供应商，再停用该供应商")
                if provider_id is not None and not values["enabled"]:
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

    def record_detail(self, record_id: int) -> dict | None:
        r, c = self.records, self.conversations
        with self.engine.connect() as conn:
            row = conn.execute(select(r, c.c.user_id, c.c.member_name, c.c.family_role, c.c.qq_number_note).join(c, r.c.conversation_id == c.c.id)
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
