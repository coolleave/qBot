import socket

import pytest

from qbot.config import Settings


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """所有测试禁止真实外网调用，避免意外产生模型费用。"""
    original_connect = socket.socket.connect

    def blocked(sock, address):
        # Windows asyncio 用回环 socketpair 唤醒事件循环。
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(sock, address)
        raise AssertionError("测试禁止真实网络连接")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


@pytest.fixture
def settings():
    return Settings(_env_file=None, deepseek_api_key="offline-test-key",
                    qq_app_id="test-app", qq_app_secret="test-secret")
