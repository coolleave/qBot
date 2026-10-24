import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from qbot.chat import ChatService
from qbot.model import create_model


async def test_history_isolation_reset_and_bounds(settings):
    prompts = []

    async def fake(prompt):
        prompts.append(prompt.to_messages())
        return AIMessage(content="回复")

    settings.session_max_turns = 1
    chat = ChatService(settings, RunnableLambda(fake))
    await chat.reply(("g1", "u1"), "你好")
    await chat.reply(("g1", "u1"), "继续")
    assert [m.content for m in prompts[1][1:]] == ["你好", "回复", "继续"]
    assert len(chat.sessions[("g1", "u1")].messages) == 2
    await chat.reply(("g2", "u1"), "另一群")
    await chat.reply(("g1", "u2"), "另一人")
    assert len(prompts[2]) == len(prompts[3]) == 2
    await chat.reply(("g1", "u1"), "/reset")
    await chat.reply(("g1", "u1"), "新对话")
    assert len(prompts[-1]) == 2


async def test_expiry_capacity_and_invalid_input(settings):
    chat = ChatService(settings, RunnableLambda(lambda _: AIMessage(content="ok")))
    settings.session_max_count = 1
    await chat.reply(("g", "a"), "hi")
    chat.sessions[("g", "a")].touched = time.monotonic() - 2000
    await chat.reply(("g", "b"), "hi")
    assert ("g", "a") not in chat.sessions
    await chat.reply(("g", "c"), "hi")
    assert list(chat.sessions) == [("g", "c")]
    assert "文字" in await chat.reply(("g", "c"), " ")
    assert "过长" in await chat.reply(("g", "c"), "x" * 4001)
    assert len(chat.sessions[("g", "c")].messages) == 2


async def test_timeout_and_send_failure_do_not_commit(settings):
    async def slow(_):
        await asyncio.sleep(1)
        return AIMessage(content="late")

    settings.chat_timeout_seconds = 0.01
    chat = ChatService(settings, RunnableLambda(slow))
    with pytest.raises(TimeoutError):
        await chat.reply(("g", "u"), "hi")
    assert not chat.sessions
    chat = ChatService(settings, RunnableLambda(lambda _: AIMessage(content="ok")))
    sender = AsyncMock(side_effect=RuntimeError("send failed"))
    with pytest.raises(RuntimeError):
        await chat.reply(("g", "u"), "hi", sender)
    assert not chat.sessions


async def test_concurrent_replies_preserve_context_and_send_order(settings):
    prompts, sent = [], []

    async def fake(prompt):
        prompts.append(prompt.to_messages())
        await asyncio.sleep(0.01)
        return AIMessage(content=prompt.to_messages()[-1].content)

    async def sender(text):
        sent.append(text)

    chat = ChatService(settings, RunnableLambda(fake))
    await asyncio.gather(chat.reply(("g", "u"), "first", sender),
                         chat.reply(("g", "u"), "second", sender))
    assert sent == ["first", "second"]
    assert [m.content for m in prompts[1][1:]] == ["first", "first", "second"]


def test_deepseek_configuration(settings):
    model = create_model(settings)
    assert model.model_name == "deepseek-flash"
    assert model.api_base == "https://api.deepseek.com"
    assert model.max_retries == 1
    assert model.extra_body == {"thinking": {"type": "disabled"}}


async def test_real_langchain_deepseek_adapter_with_mock_sdk(settings, monkeypatch):
    model = create_model(settings)
    payload = {"id": "mock", "model": "deepseek-flash", "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "模拟回答"}, "finish_reason": "stop"},
    ], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    call = AsyncMock(return_value=SimpleNamespace(parse=lambda: payload))
    monkeypatch.setattr(model.async_client.with_raw_response, "create", call)
    chat = ChatService(settings, model)
    assert await chat.reply(("local", "u"), "你好") == "模拟回答"
    sent = call.call_args.kwargs
    assert sent["model"] == "deepseek-flash"
    assert sent["messages"][-1] == {"role": "user", "content": "你好"}
    assert sent["extra_body"] == {"thinking": {"type": "disabled"}}
    assert not sent["stream"]
