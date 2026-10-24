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
            metadata.reflect(engine, only=["model_providers", "app_settings", "conversations", "chat_records"])
            self.providers = metadata.tables["model_providers"]
            self.settings = metadata.tables["app_settings"]
            self.conversations = metadata.tables["conversations"]
            self.records = metadata.tables["chat_records"]
            with engine.connect() as conn:
                if conn.execute(select(self.settings.c.id).where(self.settings.c.id == 1)).first() is None:
                    raise StorageError("请先执行 sql/001_init.sql 初始化配置行")
        except SQLAlchemyError:
            raise StorageError("数据库连接或表结构不可用，请检查配置并执行 DDL") from None

    async def call(self, method: str, *args, **kwargs):
        try:
            return await asyncio.to_thread(getattr(self, method), *args, **kwargs)
        except SQLAlchemyError:
            raise StorageError("数据库操作失败，请检查连接及表结构") from None

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
            if provider_id is not None:
                provider = conn.execute(select(self.providers).where(self.providers.c.id == provider_id)).mappings().first()
                if not provider or not provider["enabled"] or not provider["api_key_encrypted"]:
                    raise ValueError("只能选择已启用且已配置密钥的供应商")
            conn.execute(self.settings.update().where(self.settings.c.id == 1).values(
                default_provider_id=provider_id, updated_at=utc_now()))

    def active_provider(self) -> dict | None:
        with self.engine.connect() as conn:
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
                question: str, sent_at: datetime | None = None) -> tuple[int, bool]:
        """接收记录和会话同事务提交；唯一索引阻止重复推送创建新记录。"""
        r, c = self.records, self.conversations
        identity = (r.c.source == source) & (r.c.group_id == group_id) & (r.c.external_message_id == message_id)
        for attempt in range(3):
            try:
                with self.engine.begin() as conn:
                    old = conn.execute(select(r.c.id).where(identity)).scalar_one_or_none()
                    if old is not None:
                        return old, False
                    now = utc_now()
                    cid = conn.execute(select(c.c.id).where(
                        (c.c.source == source) & (c.c.group_id == group_id) & (c.c.user_id == user_id)
                    )).scalar_one_or_none()
                    if cid is None:
                        cid = conn.execute(c.insert().values(source=source, group_id=group_id, user_id=user_id,
                            created_at=now, last_active_at=now)).inserted_primary_key[0]
                    else:
                        conn.execute(c.update().where(c.c.id == cid).values(last_active_at=now))
                    rid = conn.execute(r.insert().values(conversation_id=cid, source=source,
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
        values["updated_at"] = utc_now()
        if values.get("status") not in (None, "pending", "processing"):
            values["completed_at"] = utc_now()
        with self.engine.begin() as conn:
            conn.execute(self.records.update().where(self.records.c.id == record_id).values(**values))

    def query_records(self, *, page: int = 1, page_size: int = 20, source: str = "",
                      group_id: str = "", user_id: str = "", status: str = "",
                      start: datetime | None = None, end: datetime | None = None) -> dict:
        r, c = self.records, self.conversations
        joined = r.join(c, r.c.conversation_id == c.c.id)
        filters = []
        for col, value in [(r.c.source, source), (r.c.group_id, group_id), (c.c.user_id, user_id), (r.c.status, status)]:
            if value:
                filters.append(col == value)
        if start:
            filters.append(r.c.received_at >= start)
        if end:
            filters.append(r.c.received_at <= end)
        columns = [r.c.id, r.c.source, r.c.group_id, c.c.user_id, r.c.status, r.c.received_at,
                   r.c.model_name_snapshot, func.substr(r.c.question, 1, 120).label("question")]
        with self.engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(joined).where(*filters)).scalar_one()
            rows = conn.execute(select(*columns).select_from(joined).where(*filters)
                .order_by(r.c.received_at.desc(), r.c.id.desc()).offset((page - 1) * page_size).limit(page_size)).mappings()
            return {"items": [dict(row) for row in rows], "total": count, "page": page, "page_size": page_size}

    def record_detail(self, record_id: int) -> dict | None:
        r, c = self.records, self.conversations
        with self.engine.connect() as conn:
            row = conn.execute(select(r, c.c.user_id).join(c, r.c.conversation_id == c.c.id)
                               .where(r.c.id == record_id)).mappings().first()
            return dict(row) if row else None
