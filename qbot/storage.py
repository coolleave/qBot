from qbot.greeting_workflows import resolve_workflow, public_catalog, load_catalog
import asyncio
import base64
import json
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import MetaData, String, cast, create_engine, func, literal_column, select
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
                                           "interaction_greetings", "interaction_greeting_runs", "bot_user_identities",
                                           "admin_sessions"])
            self.admin_sessions = metadata.tables["admin_sessions"]
            self.identities = metadata.tables["bot_user_identities"]
            self.greetings = metadata.tables["interaction_greetings"]
            if any(name not in self.greetings.c for name in ("delivery", "image_data", "image_filename")):
                raise StorageError("请先执行 python -m alembic upgrade head 升级定时任务结构")
            self.greeting_runs = metadata.tables["interaction_greeting_runs"]
            if not self.greeting_runs.c.local_date.nullable:
                raise StorageError("请先执行 python -m alembic upgrade head 升级手动任务执行结构")
            self.bots = metadata.tables["bots"]
            self.providers = metadata.tables["model_providers"]
            self.settings = metadata.tables["app_settings"]
            self.conversations = metadata.tables["conversations"]
            self.records = metadata.tables["chat_records"]
            if any(name not in self.records.c for name in ("input_tokens", "output_tokens", "total_tokens")):
                raise StorageError("Token 统计表结构尚未升级，请先执行 python -m alembic upgrade head")
            if ("multimodal" not in self.bots.c or "supports_images" not in self.providers.c
                    or "media_details" not in self.records.c):
                raise StorageError("多模态表结构尚未升级，请先执行 python -m alembic upgrade head")
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

    def create_admin_session(self, token_hash: str, csrf: str, auth_version: str, ttl: int) -> None:
        """持久化登录；只存 Token 摘要，串行清理并限制最多 100 个会话。"""
        table = self.admin_sessions
        with self.engine.begin() as conn:
            conn.execute(select(self.settings.c.id).where(self.settings.c.id == 1).with_for_update()).one()
            now = utc_now()
            conn.execute(table.delete().where(
                (table.c.expires_at <= now) | (table.c.auth_version != auth_version)))
            existing = conn.execute(select(table.c.token_hash).order_by(
                table.c.created_at, table.c.token_hash)).scalars().all()
            if len(existing) >= 100:
                conn.execute(table.delete().where(table.c.token_hash.in_(existing[:len(existing) - 99])))
            conn.execute(table.insert().values(token_hash=token_hash, csrf_token=csrf,
                                               auth_version=auth_version, created_at=now,
                                               expires_at=now + timedelta(seconds=ttl)))

    def admin_session_csrf(self, token_hash: str, auth_version: str) -> str | None:
        """使用 UTC 绝对时间校验有效期，进程重启不会延长或清空会话。"""
        table = self.admin_sessions
        with self.engine.connect() as conn:
            return conn.execute(select(table.c.csrf_token).where(
                table.c.token_hash == token_hash, table.c.auth_version == auth_version,
                table.c.expires_at > utc_now())).scalar_one_or_none()

    def delete_admin_session(self, token_hash: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(self.admin_sessions.delete().where(self.admin_sessions.c.token_hash == token_hash))

    def list_bots(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.bots).order_by(self.bots.c.id)).mappings()
            items = []
            for row in rows:
                item = dict(row)
                item["multimodal"] = json.loads(item["multimodal"] or "{}")
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
        item["multimodal"] = json.loads(item["multimodal"] or "{}")
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
                from qbot.multimodal_config import MultimodalConfig
                mm = MultimodalConfig.model_validate(values.get("multimodal", {})).model_dump()
                if mm["images"] and not mm["vision_provider_id"]:
                    raise ValueError("开启图片理解需单独选择视觉理解模型")
                for field, kind in (("vision_provider_id", "chat"), ("asr_provider_id", "asr"), ("tts_provider_id", "tts")):
                    pid = mm[field]
                    if pid is None:
                        continue
                    selected = conn.execute(select(self.providers).where(self.providers.c.id == pid)).mappings().first()
                    if not selected or not selected["enabled"] or not selected["api_key_encrypted"] or selected["model_type"] != kind:
                        raise ValueError("多模态供应商类型不匹配或未启用")
                    if field == "vision_provider_id" and (not selected["supports_images"] or selected["api_type"] != "openai_compatible"):
                        raise ValueError("视觉供应商需要支持图片输入的兼容聊天接口")
                values["multimodal"] = json.dumps(mm, ensure_ascii=False)
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
        if row["model_type"] not in ("chat", "tts", "asr"):
            raise ValueError("该类型未接入调用")
        result = dict(row)
        encrypted = result.pop("api_key_encrypted")
        if not encrypted:
            raise ValueError("请先保存 API Key")
        try:
            result["api_key"] = self.cipher.decrypt(encrypted.encode()).decode()
        except InvalidToken:
            raise ValueError("密钥解密失败，请检查加密主密钥") from None
        return result

    def multimodal_config(self, bot_id: int) -> dict:
        from qbot.multimodal_config import MultimodalConfig
        with self.engine.connect() as conn:
            value = conn.execute(select(self.bots.c.multimodal).where(self.bots.c.id == bot_id)).scalar_one()
        return MultimodalConfig.model_validate(json.loads(value or "{}")).model_dump()

    def capability_provider(self, provider_id: int, kind: str, vision: bool = False) -> dict:
        provider = self.provider_for_probe(provider_id)
        if not provider["enabled"] or provider["model_type"] != kind:
            raise ValueError("供应商未启用或用途不匹配")
        if vision and (not provider["supports_images"] or provider["api_type"] != "openai_compatible"):
            raise ValueError("供应商不支持图片输入")
        return provider

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
                if (old and not key and old["api_key_encrypted"]
                        and urlsplit(old["base_url"]).netloc != urlsplit(values["base_url"]).netloc):
                    raise ValueError("更换供应商服务域名时请重新填写 API Key，不能沿用原供应商密钥")
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
                if values.get("supports_images") and (values["model_type"] != "chat" or values["api_type"] != "openai_compatible"):
                    raise ValueError("图片输入能力仅用于兼容接口的 chat 模型")
                if provider_id is not None:
                    for raw in conn.execute(select(self.greetings.c.delivery)).scalars():
                        delivery = json.loads(raw or "{}")
                        if (resolve_workflow(delivery).output == "audio" and delivery.get("tts_provider_id") == provider_id
                                and (not values["enabled"] or values["model_type"] != "tts")):
                            raise ValueError("供应商已被定时语音任务引用，请先调整任务配置")
                    for bot in conn.execute(select(self.bots)).mappings():
                        mm = json.loads(bot["multimodal"] or "{}")
                        for field, kind in (("vision_provider_id", "chat"), ("asr_provider_id", "asr"), ("tts_provider_id", "tts")):
                            bound_id = mm.get(field)
                            if bound_id == provider_id and (not values["enabled"] or values["model_type"] != kind
                                    or (field == "vision_provider_id" and (not values.get("supports_images")
                                        or values["api_type"] != "openai_compatible"))):
                                raise ValueError("供应商已被多模态配置引用，请先调整机器人配置")
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
            provider = None
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
                   r.c.input_tokens, r.c.output_tokens, r.c.total_tokens,
                   r.c.model_name_snapshot, func.substr(r.c.question, 1, 120).label("question")]
        with self.engine.connect() as conn:
            # 与列表共用筛选条件，汇总全部匹配记录，不受分页或发送状态限制。
            aggregate = conn.execute(select(
                func.count().label("records"),
                *(func.sum(r.c[name]).label(name) for name in ("input_tokens", "output_tokens", "total_tokens")),
                func.count(r.c.total_tokens).label("known_records"),
            ).select_from(joined).where(*filters)).mappings().one()
            usage = {name: int(value) if value is not None else None for name, value in aggregate.items()}
            count = usage["records"]
            rows = conn.execute(select(*columns).select_from(joined).where(*filters)
                .order_by(r.c.received_at.desc(), r.c.id.desc()).offset((page - 1) * page_size).limit(page_size)).mappings()
            return {"items": [dict(row) for row in rows], "total": count, "page": page,
                    "page_size": page_size, "token_usage": usage}

    def token_stat_options(self, bot_id: int | None = None, search: str = "", page: int = 1) -> dict:
        """会话选项分页查询，模型使用历史快照，避免配置改名后无法筛选。"""
        r, c, b = self.records, self.conversations, self.bots
        filters = [c.c.bot_id == bot_id] if bot_id is not None else []
        if search:
            filters.append(c.c.user_id.contains(search, autoescape=True)
                | c.c.group_id.contains(search, autoescape=True)
                | c.c.contact_name.contains(search, autoescape=True)
                | (cast(c.c.id, String) == search))
        with self.engine.connect() as conn:
            bots = conn.execute(select(b.c.id, b.c.name).order_by(b.c.id)).mappings().all()
            models = select(r.c.model_name_snapshot).where(r.c.model_name_snapshot.is_not(None))
            if bot_id is not None:
                models = models.where(r.c.bot_id == bot_id)
            model_names = conn.execute(models.distinct().order_by(r.c.model_name_snapshot)).scalars().all()
            count = conn.execute(select(func.count()).select_from(c).where(*filters)).scalar_one()
            conversations = conn.execute(select(c.c.id, c.c.bot_id, c.c.source, c.c.group_id,
                c.c.user_id, c.c.contact_name).where(*filters).order_by(c.c.id.desc())
                .offset((page - 1) * 50).limit(50)).mappings().all()
            return {"bots": [dict(row) for row in bots], "models": model_names,
                    "conversations": [dict(row) for row in conversations], "total": count,
                    "page": page, "page_size": 50}

    def token_statistics(self, *, start: datetime, end: datetime, bot_id: int | None = None,
                         conversation_id: int | None = None, model_name: str = "") -> dict:
        """在数据库内聚合，所有图表使用同一组条件及北京时间日期边界。"""
        r, c, b = self.records, self.conversations, self.bots
        joined = r.join(c, (r.c.conversation_id == c.c.id) & (r.c.bot_id == c.c.bot_id)).join(b, r.c.bot_id == b.c.id)
        filters = [r.c.received_at >= start, r.c.received_at < end]
        if bot_id is not None:
            filters.append(r.c.bot_id == bot_id)
        if conversation_id is not None:
            filters.append(r.c.conversation_id == conversation_id)
        if model_name:
            filters.append(r.c.model_name_snapshot == model_name)
        measures = [func.count().label("records"), func.count(r.c.total_tokens).label("known_records"),
            *(func.sum(r.c[name]).label(name) for name in ("input_tokens", "output_tokens", "total_tokens"))]
        day = func.date(func.timestampadd(literal_column("HOUR"), 8, r.c.received_at))

        def serialize(row) -> dict:
            result = dict(row)
            for name in ("records", "known_records", "input_tokens", "output_tokens", "total_tokens"):
                if result.get(name) is not None:
                    result[name] = int(result[name])
            if "day" in result:
                result["day"] = str(result["day"])
            return result

        with self.engine.connect() as conn:
            summary = conn.execute(select(*measures).select_from(joined).where(*filters)).mappings().one()
            daily = conn.execute(select(day.label("day"), *measures).select_from(joined)
                .where(*filters).group_by(day).order_by(day)).mappings().all()

            def ranking(columns: list, limit: int) -> list[dict]:
                rows = conn.execute(select(*columns, *measures).select_from(joined).where(*filters)
                    .group_by(*columns).order_by(func.sum(r.c.total_tokens).desc(), *columns)
                    .limit(limit)).mappings().all()
                return [serialize(row) for row in rows]

            return {"summary": serialize(summary), "daily": [serialize(row) for row in daily],
                "bots": ranking([r.c.bot_id, b.c.name], 10),
                "models": ranking([r.c.model_name_snapshot], 10),
                "conversations": ranking([r.c.conversation_id, r.c.bot_id, c.c.source,
                    c.c.group_id, c.c.user_id, c.c.contact_name], 20)}

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
        r, c, i = self.records, self.conversations, self.identities
        with self.engine.connect() as conn:
            row = conn.execute(select(r, c.c.user_id, i.c.member_name, i.c.family_role,
                i.c.qq_number_note, c.c.contact_name, c.c.contact_qq)
                .select_from(r.join(c, r.c.conversation_id == c.c.id).outerjoin(i,
                    (i.c.bot_id == c.c.bot_id) & (i.c.user_id == c.c.user_id)
                    & c.c.source.in_(("qq_group", "qq_c2c"))))
                .where(r.c.id == record_id)).mappings().first()
            if not row:
                return None
            result = dict(row)
            result["media_details"] = json.loads(result["media_details"] or "{}")
            return result

    def save_family_binding(self, bot_id: int, record_id: int, values: dict | None) -> dict:
        """仅从已有 QQ 记录取身份，客户端不能指定或替换 OpenID。"""
        r, c = self.records, self.conversations
        with self.engine.begin() as conn:
            # 同一机器人的身份写入串行化，涵盖首次插入及跨会话修改。
            conn.execute(select(self.bots.c.id).where(self.bots.c.id == bot_id).with_for_update()).first()
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
                if role not in ("father", "mother", "friend", "owner"):
                    raise ValueError("无效的家庭角色")
                if len(note) > 20 or any(ch not in "0123456789" for ch in note):
                    raise ValueError("QQ 备注只能填写最多 20 位数字")
                binding.update(member_name=name, family_role=role, qq_number_note=note or None)
            i = self.identities
            scope = (i.c.bot_id == bot_id) & (i.c.user_id == row["user_id"])
            conn.execute(i.delete().where(scope))
            if values is not None:
                conn.execute(i.insert().values(bot_id=bot_id, user_id=row["user_id"], **binding))
            return {"bot_id": bot_id, "source": row["source"], "group_id": row["group_id"],
                    "user_id": row["user_id"], **binding}

    def list_identity_bindings(self, bot_id: int | None = None, user_id: str = "", page: int = 1) -> dict:
        """按机器人和 OpenID 合并群聊、私聊，展示已知用户及其统一身份。"""
        c, r, i = self.conversations, self.records, self.identities
        filters = [c.c.source.in_(("qq_group", "qq_c2c"))]
        if bot_id is not None:
            filters.append(c.c.bot_id == bot_id)
        if user_id:
            filters.append(c.c.user_id == user_id)
        users = select(c.c.bot_id, c.c.user_id, func.max(r.c.id).label("record_id")).select_from(
            c.join(r, (r.c.conversation_id == c.c.id) & (r.c.bot_id == c.c.bot_id))
        ).where(*filters).group_by(c.c.bot_id, c.c.user_id).subquery()
        joined = users.outerjoin(i, (i.c.bot_id == users.c.bot_id) & (i.c.user_id == users.c.user_id))
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(users)).scalar_one()
            rows = conn.execute(select(users, i.c.member_name, i.c.family_role, i.c.qq_number_note)
                .select_from(joined).order_by(users.c.record_id.desc())
                .offset((page - 1) * 20).limit(20)).mappings().all()
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": 20}

    def bind_identity_id(self, bot_id: int, values: dict) -> dict:
        """从已接收的 QQ 消息确认 OpenID，复用身份校验与写入锁。"""
        c, r = self.conversations, self.records
        with self.engine.connect() as conn:
            record_id = conn.execute(select(func.max(r.c.id)).select_from(
                c.join(r, (r.c.conversation_id == c.c.id) & (r.c.bot_id == c.c.bot_id))
            ).where(c.c.bot_id == bot_id, c.c.user_id == values["user_id"],
                    c.c.source.in_(("qq_group", "qq_c2c")))).scalar_one()
        if record_id is None:
            raise ValueError("该机器人下未找到此 OpenID 的 QQ 消息，请让用户先发一条消息")
        return self.save_family_binding(bot_id, record_id, values)

    def family_identity(self, bot_id: int, record_id: int) -> dict | None:
        record = self.record_detail(record_id)
        if (not record or record["bot_id"] != bot_id or record["source"] not in ("qq_group", "qq_c2c")
                or not record["family_role"]):
            return None
        return {"member_name": record["member_name"], "family_role": record["family_role"]}

    def greeting_settings(self, bot_id: int, record_id: int) -> dict:
        from qbot.greetings import default_rules, local_time
        record = self.record_detail(record_id)
        if not record or record["bot_id"] != bot_id or record["source"] not in ("qq_c2c", "qq_group"):
            raise ValueError("该机器人下不存在这条 QQ 消息")
        g, runs = self.greetings, self.greeting_runs
        with self.engine.connect() as conn:
            rows = conn.execute(select(g).where(g.c.bot_id == bot_id,
                g.c.conversation_id == record["conversation_id"])).mappings().all()
            rules = {r["slot"]: dict(slot=r["slot"], enabled=bool(r["enabled"]), start=r["start_time"],
                weekdays=[int(d) for d in r["weekdays"].split(",")], content=r["content"],
                **json.loads(r["delivery"] or "{}"), image_filename=r["image_filename"] or "",
                has_image=bool(r["image_data"])) for r in rows}
            history = conn.execute(select(runs.c.local_date, runs.c.created_at, g.c.slot, runs.c.content,
                runs.c.record_id, self.records.c.status, self.records.c.error_message)
                .select_from(runs.join(g, runs.c.greeting_id == g.c.id)
                    .join(self.records, runs.c.record_id == self.records.c.id))
                .where(g.c.bot_id == bot_id, g.c.conversation_id == record["conversation_id"])
                .order_by(runs.c.id.desc()).limit(20)).mappings().all()
        result_rules = [rules.get(r["slot"], r) for r in default_rules()]
        for rule in result_rules:
            rule["workflow"] = resolve_workflow(rule).model_dump()
        return {"rules": result_rules, "catalog": public_catalog(),
                "bound": bool(record["contact_name"]), "contact_name": record["contact_name"],
                "history": [{**dict(r), "manual": r["local_date"] is None,
                    "local_date": r["local_date"] or local_time(r["created_at"]).date()} for r in history]}

    def save_greeting_settings(self, bot_id: int, record_id: int, values: dict) -> None:
        from qbot.greetings import GreetingInput
        from qbot.media import validate_media
        data = GreetingInput.model_validate(values)
        c, r, g = self.conversations, self.records, self.greetings
        with self.engine.begin() as conn:
            conn.execute(select(self.settings.c.id).where(self.settings.c.id == 1).with_for_update()).one()
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
                # 保留旧表必填字段兼容结构，结束时间不再参与调度。
                fields = dict(enabled=rule.enabled, start_time=rule.start, end_time="23:59",
                              weekdays=",".join(map(str, rule.weekdays)), content=rule.content)
                old = conn.execute(select(g).where(scope)).mappings().first()
                image_data = old["image_data"] if old else None
                if rule.remove_image:
                    image_data = None
                    fields.update(image_data=None, image_filename=None)
                if rule.image_base64 is not None:
                    try:
                        raw = base64.b64decode(rule.image_base64, validate=True)
                    except ValueError:
                        raise ValueError("图片编码无效，请重新选择图片") from None
                    media = validate_media("image", rule.image_filename, raw)
                    image_data = media.data
                    fields.update(image_data=image_data, image_filename=media.filename)
                if rule.workflow.image_source == "upload" and not image_data:
                    raise ValueError("组合消息需要上传图片")
                if rule.workflow.image_source == "library" and rule.workflow.library_id not in {item.id for item in load_catalog().libraries}:
                    raise ValueError("请选择有效素材库")
                if rule.workflow.output == "audio":
                    provider = conn.execute(select(self.providers).where(self.providers.c.id == rule.tts_provider_id)).mappings().first()
                    if not provider or not provider["enabled"] or provider["model_type"] != "tts" or not provider["api_key_encrypted"]:
                        raise ValueError("请选择已启用且配置密钥的 TTS 供应商")
                fields["delivery"] = json.dumps(dict(delivery_mode=rule.delivery_mode, workflow=rule.workflow.model_dump(),
                    tts_provider_id=rule.tts_provider_id, voice=rule.voice))
                if old:
                    conn.execute(g.update().where(scope).values(**fields))
                else:
                    conn.execute(g.insert().values(bot_id=bot_id, conversation_id=row["id"], slot=rule.slot, **fields))

    def due_greetings(self) -> list[int]:
        """仅扫描今天已到发送时间、尚未领取的任务。"""
        from qbot.greetings import local_time
        now = local_time(utc_now())
        g, runs = self.greetings, self.greeting_runs
        c = self.conversations
        claimed = select(runs.c.id).where(runs.c.bot_id == g.c.bot_id,
            runs.c.greeting_id == g.c.id, runs.c.local_date == now.date()).exists()
        with self.engine.connect() as conn:
            return list(conn.execute(select(g.c.id).select_from(g.join(c,
                (g.c.conversation_id == c.c.id) & (g.c.bot_id == c.c.bot_id))).where(g.c.enabled == 1,
                c.c.contact_name.is_not(None), c.c.contact_name != "", c.c.source.in_(("qq_c2c", "qq_group")),
                g.c.start_time <= now.strftime("%H:%M"),
                func.find_in_set(str(now.isoweekday()), g.c.weekdays) > 0, ~claimed)
                .order_by(g.c.id).limit(100)).scalars())

    def greeting_for_execution(self, bot_id: int, record_id: int, slot: str) -> int:
        record = self.record_detail(record_id)
        if not record or record["bot_id"] != bot_id or record["source"] not in ("qq_group", "qq_c2c"):
            raise ValueError("请选择此机器人已有的 QQ 接收对象")
        g = self.greetings
        with self.engine.connect() as conn:
            rule_id = conn.execute(select(g.c.id).where(g.c.bot_id == bot_id,
                g.c.conversation_id == record["conversation_id"], g.c.slot == slot)).scalar_one_or_none()
        if rule_id is None:
            raise ValueError("请先保存该任务，再执行一次")
        return rule_id

    def claim_scheduled_greeting(self, greeting_id: int, manual: bool = False) -> dict | None:
        """配置复核、每日领取和独立发送记录在同一事务中完成，崩溃后不重发。"""
        from qbot.greetings import local_time
        g, c, runs, r = self.greetings, self.conversations, self.greeting_runs, self.records
        with self.engine.begin() as conn:
            row = conn.execute(select(g, c.c.source, c.c.group_id, c.c.user_id, c.c.contact_name)
                .select_from(g.join(c, (g.c.conversation_id == c.c.id) & (g.c.bot_id == c.c.bot_id)))
                .where(g.c.id == greeting_id).with_for_update()).mappings().first()
            now_utc = utc_now()
            now = local_time(now_utc)
            if (not row or not row["contact_name"] or row["source"] not in ("qq_c2c", "qq_group")):
                if manual:
                    raise ValueError("任务不存在或接收人已解除绑定")
                return None
            if not manual and (not row["enabled"]
                    or str(now.isoweekday()) not in row["weekdays"].split(",")
                    or row["start_time"] > now.strftime("%H:%M")):
                return None
            if not manual:
                previous = conn.execute(select(runs.c.id).where(runs.c.bot_id == row["bot_id"],
                    runs.c.greeting_id == greeting_id, runs.c.local_date == now.date())).first()
                if previous:
                    return None
            workflow = resolve_workflow(json.loads(row["delivery"] or "{}"))
            preset = workflow.content_source == "ai"
            # 预设生成前不将旧固定草稿标为本次实际正文。
            execution_content = "" if preset else row["content"]
            rid = conn.execute(r.insert().values(bot_id=row["bot_id"], conversation_id=row["conversation_id"],
                source=row["source"], group_id=row["group_id"],
                external_message_id=(f"scheduled-manual:{greeting_id}:{uuid4()}" if manual
                                     else f"scheduled:{greeting_id}:{now.date().isoformat()}"),
                question=("【定时任务执行一次】\n" if manual else "【定时主动发送】\n") + ("AI 生成消息" if preset else execution_content), answer=execution_content,
                record_type="chat", status="processing", received_at=now_utc,
                updated_at=now_utc)).inserted_primary_key[0]
            conn.execute(runs.insert().values(bot_id=row["bot_id"], greeting_id=greeting_id,
                local_date=None if manual else now.date(), record_id=rid, content=execution_content, created_at=now_utc))
            return {**dict(row), "record_id": rid, "workflow": workflow.model_dump()}

    def save_scheduled_content(self, bot_id: int, record_id: int, content: str) -> None:
        """在发送前一起保存生成正文和执行历史，不改写任务的固定草稿。"""
        with self.engine.begin() as conn:
            conn.execute(self.records.update().where(self.records.c.id == record_id,
                self.records.c.bot_id == bot_id).values(answer=content, updated_at=utc_now()))
            conn.execute(self.greeting_runs.update().where(self.greeting_runs.c.record_id == record_id,
                self.greeting_runs.c.bot_id == bot_id).values(content=content))

    def greeting_targets(self, bot_id: int | None = None, page: int = 1, page_size: int = 20,
                         *, bound_only: bool = False, search: str = "") -> dict:
        """独立菜单列出已绑定或曾配置过问候的会话，沿用记录身份校验。"""
        c, r, g = self.conversations, self.records, self.greetings
        latest = select(r.c.bot_id, r.c.conversation_id, func.max(r.c.id).label("record_id"))
        stats = select(g.c.bot_id, g.c.conversation_id, func.sum(g.c.enabled).label("enabled_count"),
                       func.count(g.c.id).label("configured_count"))
        if bot_id is not None:
            latest = latest.where(r.c.bot_id == bot_id)
            stats = stats.where(g.c.bot_id == bot_id)
        latest = latest.group_by(r.c.bot_id, r.c.conversation_id).subquery()
        stats = stats.group_by(g.c.bot_id, g.c.conversation_id).subquery()
        joined = c.join(latest, (latest.c.conversation_id == c.c.id) & (latest.c.bot_id == c.c.bot_id)).outerjoin(
            stats, (stats.c.conversation_id == c.c.id) & (stats.c.bot_id == c.c.bot_id))
        filters = [c.c.source.in_(("qq_c2c", "qq_group")),
                   c.c.contact_name.is_not(None) | stats.c.conversation_id.is_not(None)]
        if bound_only:
            filters.extend((c.c.contact_name.is_not(None), c.c.contact_name != ""))
        if search:
            filters.append(c.c.contact_name.contains(search, autoescape=True) | (c.c.user_id == search))
        if bot_id is not None:
            filters.append(c.c.bot_id == bot_id)
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(joined).where(*filters)).scalar_one()
            rows = conn.execute(select(latest.c.record_id.label("id"), c.c.bot_id, c.c.source,
                c.c.group_id, c.c.user_id, c.c.contact_name,
                func.coalesce(stats.c.configured_count, 0).label("configured_count"),
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
