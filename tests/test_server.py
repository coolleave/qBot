import asyncio
import json
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from qbot.chat import ChatService
from qbot.qq import QQAPIError, signing_key
from qbot.server import Dispatcher, GroupMessage, create_app


def message(mid="m1", user="u1"):
    return {"id": mid, "group_openid": "g1", "author": {"member_openid": user},
            "content": "你好", "timestamp": datetime.now(timezone.utc).isoformat()}


def signed(payload, settings):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = signing_key(settings.qq_app_secret.get_secret_value()).sign(timestamp.encode() + body).hex()
    return {"content": body, "headers": {"X-Signature-Timestamp": timestamp,
                                         "X-Signature-Ed25519": signature,
                                         "X-Bot-Appid": settings.qq_app_id}}


async def test_webhook_challenge_validation_dedup_and_reply(settings):
    qq = SimpleNamespace(send_group=AsyncMock())
    chat = ChatService(settings, RunnableLambda(lambda _: AIMessage(content="你好呀")))
    app = create_app(settings, chat=chat, qq=qq)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            challenge = {"op": 13, "d": {"plain_token": "hello", "event_ts": "123"}}
            response = await client.post("/qq/webhook", **signed(challenge, settings))
            signing_key("test-secret").public_key().verify(bytes.fromhex(response.json()["signature"]), b"123hello")
            assert (await client.post("/qq/webhook", json=challenge)).status_code == 401
            invalid = signed(challenge, settings)
            invalid["content"] += b" "
            assert (await client.post("/qq/webhook", **invalid)).status_code == 401
            invalid = signed(challenge, settings)
            invalid["headers"]["X-Bot-Appid"] = "wrong"
            assert (await client.post("/qq/webhook", **invalid)).status_code == 403
            payload = {"op": 0, "t": "GROUP_AT_MESSAGE_CREATE", "d": message()}
            for _ in range(2):
                response = await client.post("/qq/webhook", **signed(payload, settings))
                assert response.json() == {"op": 12}
            await asyncio.wait_for(app.state.dispatcher.queue.join(), 2)
            qq.send_group.assert_awaited_once_with("g1", "m1", "你好呀")
            payload["d"] = {}
            assert (await client.post("/qq/webhook", **signed(payload, settings))).status_code == 400
            assert (await client.post("/qq/webhook", content=b"x" * 65537)).status_code == 413
            assert (await client.post("/qq/webhook", **signed([], settings))).status_code == 400


def test_admission_limits_stale_and_duplicates(settings):
    settings.rate_limit_per_minute = 1
    settings.queue_max_size = 2
    dispatcher = Dispatcher(settings, None, None)
    dispatcher.enqueue(GroupMessage.model_validate(message()))
    dispatcher.enqueue(GroupMessage.model_validate(message()))
    assert dispatcher.queue.qsize() == 1
    with pytest.raises(HTTPException) as exc:
        dispatcher.enqueue(GroupMessage.model_validate(message("m2")))
    assert exc.value.status_code == 429
    dispatcher.enqueue(GroupMessage.model_validate(message("m3", "u2")))
    with pytest.raises(HTTPException) as exc:
        dispatcher.enqueue(GroupMessage.model_validate(message("m4", "u3")))
    assert exc.value.status_code == 503
    stale = message("old")
    stale["timestamp"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    dispatcher.enqueue(GroupMessage.model_validate(stale))
    assert dispatcher.queue.qsize() == 2


async def test_model_failure_has_user_feedback_and_worker_survives(settings):
    chat = SimpleNamespace(reply=AsyncMock(side_effect=TimeoutError))
    qq = SimpleNamespace(send_group=AsyncMock())
    dispatcher = Dispatcher(settings, chat, qq)
    dispatcher.enqueue(GroupMessage.model_validate(message()))
    worker = asyncio.create_task(dispatcher.run())
    try:
        await asyncio.wait_for(dispatcher.queue.join(), 2)
        assert "暂时不可用" in qq.send_group.call_args.args[2]
        assert not worker.done()
        chat.reply.side_effect = QQAPIError("send failed")
        dispatcher.enqueue(GroupMessage.model_validate(message("m2")))
        await asyncio.wait_for(dispatcher.queue.join(), 2)
        assert qq.send_group.await_count == 1
        assert not worker.done()
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
