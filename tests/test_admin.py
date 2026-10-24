from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from qbot.recording import RecordedChat
from qbot.qq import QQAPIError, QQDeliveryUnknown
from qbot.security import password_hash, verify_password
from qbot.server import Dispatcher, GroupMessage, create_app
from qbot.storage import Store, StorageError


@pytest.fixture
def db(settings):
    """使用隔离 SQLite 验证仓储事务；不冒充真实 MySQL DDL 验证。"""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    ddl = [
        """CREATE TABLE model_providers (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
        api_type TEXT, base_url TEXT, model_name TEXT, api_key_encrypted TEXT, enabled INTEGER,
        created_at DATETIME, updated_at DATETIME)""",
        "CREATE TABLE app_settings (id INTEGER PRIMARY KEY, default_provider_id INTEGER, updated_at DATETIME)",
        """CREATE TABLE conversations (id INTEGER PRIMARY KEY, source TEXT, group_id TEXT, user_id TEXT,
        created_at DATETIME, last_active_at DATETIME, UNIQUE(source, group_id, user_id))""",
        """CREATE TABLE chat_records (id INTEGER PRIMARY KEY, conversation_id INTEGER, source TEXT,
        group_id TEXT, external_message_id TEXT, record_type TEXT, question TEXT, answer TEXT, status TEXT,
        error_message TEXT, provider_id INTEGER, provider_name_snapshot TEXT, model_name_snapshot TEXT,
        message_sent_at DATETIME, received_at DATETIME, completed_at DATETIME, updated_at DATETIME,
        UNIQUE(source, group_id, external_message_id))""",
        "INSERT INTO app_settings (id) VALUES (1)",
    ]
    with engine.begin() as conn:
        for statement in ddl:
            conn.execute(text(statement))
    settings.admin_enabled = True
    settings.mysql_user = "test"
    settings.admin_password_hash = SecretStr(password_hash("test-password-long"))
    settings.admin_session_secret = SecretStr("s" * 48)
    settings.provider_encryption_key = SecretStr(Fernet.generate_key().decode())
    store = Store(engine, settings.provider_encryption_key.get_secret_value())
    yield store
    engine.dispose()


def provider(name="DeepSeek"):
    return dict(name=name, api_type="deepseek", base_url="https://api.deepseek.com",
                model_name="deepseek-flash", api_key="private-test-key", enabled=True)


def test_password_hash():
    value = password_hash("correct-password-long")
    assert verify_password("correct-password-long", value)
    assert not verify_password("wrong", value)
    assert not verify_password("wrong", "invalid")


def test_provider_encryption_default_and_retained_key(db):
    pid = db.save_provider(provider())
    assert "private-test-key" not in str(db.list_providers())
    with db.engine.connect() as conn:
        encrypted = conn.execute(text("SELECT api_key_encrypted FROM model_providers")).scalar_one()
    assert encrypted != "private-test-key"
    db.set_default(pid)
    assert db.active_provider()["api_key"] == "private-test-key"
    db.save_provider({**provider(), "api_key": "", "model_name": "other-model"}, pid)
    assert db.active_provider()["api_key"] == "private-test-key"
    with pytest.raises(ValueError):
        db.save_provider({**provider(), "enabled": False}, pid)
    db.set_default(None)
    db.save_provider({**provider(), "enabled": False}, pid)
    with pytest.raises(ValueError):
        db.set_default(pid)


async def test_records_dedup_filter_and_detail(db):
    rid, created = await db.call("receive", "qq_group", "g", "u", "m", "你好😀")
    assert created
    assert await db.call("receive", "qq_group", "g", "u", "m", "duplicate") == (rid, False)
    await db.call("receive", "qq_group", "other", "u", "m", "other group")
    result = await db.call("query_records", group_id="g", user_id="u", page_size=1)
    assert result["total"] == 1 and result["items"][0]["id"] == rid
    assert (await db.call("record_detail", rid))["question"] == "你好😀"
    assert (await db.call("query_records", group_id="g' OR 1=1 --"))["total"] == 0


async def test_admin_auth_csrf_masking_and_validation(settings, db):
    settings.deepseek_api_key = SecretStr("")
    settings.qq_app_id = ""
    settings.qq_app_secret = SecretStr("")
    app = create_app(settings, store=db)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/admin")).status_code == 200
            assert (await client.post("/qq/webhook", json={})).status_code == 503
            assert (await client.get("/api/admin/providers")).status_code == 401
            assert (await client.post("/api/admin/login", json={"username":"admin","password":"test-password-long"})).status_code == 403
            headers = {"X-QBot-Admin":"1"}
            login = await client.post("/api/admin/login", headers=headers, json={"username":"admin","password":"test-password-long"})
            assert login.status_code == 200
            assert "HttpOnly" in login.headers["set-cookie"]
            assert (await client.post("/api/admin/providers", headers=headers, json=provider())).status_code == 403
            headers["X-CSRF-Token"] = login.json()["csrf_token"]
            response = await client.post("/api/admin/providers", headers=headers, json=provider())
            assert response.status_code == 200
            assert "private-test-key" not in (await client.get("/api/admin/providers")).text
            invalid = {**provider(), "base_url":"https://user:password@example.com/?key=secret"}
            response = await client.post("/api/admin/providers", headers=headers, json=invalid)
            assert response.status_code == 422 and "private-test-key" not in response.text and "password@" not in response.text
            response = await client.put("/api/admin/default-provider", headers=headers, json={"provider_id":response.json().get("id", 1)})
            assert response.status_code == 200
            assert (await client.get("/api/admin/records?page_size=1000")).status_code == 422
            assert (await client.get("/api/admin/records?start=2026-01-01T00:00:00")).status_code == 400
            cookie = client.cookies.get("qbot_admin")
            assert (await client.post("/api/admin/logout", headers=headers)).status_code == 200
            client.cookies.set("qbot_admin", cookie)
            assert (await client.get("/api/admin/session")).status_code == 401


@pytest.mark.parametrize("failure,status", [(None,"replied"),(QQAPIError("safe"),"send_failed"),(QQDeliveryUnknown("safe"),"delivery_unknown")])
async def test_recorded_reply_outcomes(settings, db, monkeypatch, failure, status):
    monkeypatch.setattr("qbot.recording.provider_model", lambda *_: RunnableLambda(lambda _: AIMessage(content="answer")))
    chat = RecordedChat(settings, db)
    sender = AsyncMock(side_effect=failure)
    if failure:
        with pytest.raises(QQAPIError):
            await chat.reply(("local", "u"), "question", sender)
    else:
        assert await chat.reply(("local", "u"), "question", sender) == "answer"
    rid = db.query_records()["items"][0]["id"]
    assert db.record_detail(rid)["status"] == status
    assert db.record_detail(rid)["answer"] == "answer"


async def test_model_failure_and_reset_preserve_records(settings, db, monkeypatch):
    def fail(_):
        raise RuntimeError("secret-value-that-must-not-be-stored")
    monkeypatch.setattr("qbot.recording.provider_model", lambda *_: RunnableLambda(fail))
    chat = RecordedChat(settings, db)
    with pytest.raises(RuntimeError):
        await chat.reply(("local", "u"), "hi")
    rid = db.query_records()["items"][0]["id"]
    record = db.record_detail(rid)
    assert record["status"] == "model_failed" and "secret-value" not in str(record)
    assert "清空" in await chat.reply(("local", "u"), "/reset")
    assert db.query_records()["total"] == 2


async def test_accept_commits_before_enqueue_and_persistent_dedup(settings, db):
    dispatcher = Dispatcher(settings, None, None, db)
    message = GroupMessage(id="m", group_openid="g", author={"member_openid":"u"}, content="hi", timestamp=datetime.now(timezone.utc))
    await dispatcher.accept(message)
    assert dispatcher.queue.qsize() == 1
    assert db.query_records()["total"] == 1
    other = Dispatcher(settings, None, None, db)
    await other.accept(message)
    assert other.queue.empty()
    broken = SimpleNamespace(call=AsyncMock(side_effect=StorageError("offline")))
    dispatcher = Dispatcher(settings, None, None, broken)
    with pytest.raises(StorageError):
        await dispatcher.accept(message)
    assert dispatcher.queue.empty()


async def test_default_provider_changes_next_request(settings, db, monkeypatch):
    names = []
    def build(_, config):
        names.append(config["name"] if config else "environment")
        return RunnableLambda(lambda _: AIMessage(content="ok"))
    monkeypatch.setattr("qbot.recording.provider_model", build)
    chat = RecordedChat(settings, db)
    await chat.reply(("local", "u"), "one")
    db.set_default(db.save_provider(provider("new-provider")))
    await chat.reply(("local", "u"), "two")
    assert names == ["environment", "new-provider"]
