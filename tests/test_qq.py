import json
import time

import httpx
import pytest

from qbot.qq import QQAPIError, QQClient, signing_key, verify_signature


def test_signature_validation_and_official_public_key(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1725442341)
    secret = "naOC0ocQE3shWLAfffVLB1rhYPG7"
    body = b'{ "op": 0,"d": {}, "t": "GATEWAY_EVENT_NAME"}'
    key = signing_key(secret)
    assert list(key.public_key().public_bytes_raw()) == [
        215, 195, 98, 254, 120, 174, 248, 31, 242, 50, 135, 180, 147, 98, 139, 93,
        176, 42, 60, 79, 227, 11, 33, 94, 77, 25, 96, 155, 93, 118, 103, 58,
    ]
    signature = key.sign(b"1725442341" + body).hex()
    assert verify_signature(secret, "1725442341", signature, body)
    assert not verify_signature(secret, "1725442341", signature, body + b" ")
    assert not verify_signature(secret, "1725440000", signature, body)
    assert not verify_signature(secret, "bad", "bad", body)


def test_challenge_known_official_vector():
    signature = signing_key("DG5g3B4j9X2KOErG").sign(b"1725442341Arq0D5A61EgUu4OxUvOp").hex()
    assert signature == "87befc99c42c651b3aac0278e71ada338433ae26fcb24307bdc5ad38c1adc2d01bcfcadc0842edac85e85205028a1132afe09280305f13aa6909ffc2d652c706"


async def test_token_cache_refresh_and_reply_contract(settings):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/app/getAppAccessToken":
            assert json.loads(request.content) == {"appId": "test-app", "clientSecret": "test-secret"}
            return httpx.Response(200, json={"access_token": "token", "expires_in": "7200"})
        assert request.headers["Authorization"] == "QQBot token"
        assert json.loads(request.content) == {"content": "你好", "msg_id": "m1", "msg_seq": 1, "msg_type": 0}
        return httpx.Response(200, json={"id": "reply"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        qq = QQClient(settings, client)
        await qq.send_group("group1", "m1", "你好")
        await qq.access_token()
        assert len(requests) == 2
        qq.expires_at = 0
        await qq.access_token()
        assert len(requests) == 3


@pytest.mark.parametrize("payload", [{"code": 100016}, {"expires_in": "bad"}, []])
async def test_invalid_token_response(settings, payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=payload),
    )) as client:
        with pytest.raises(QQAPIError):
            await QQClient(settings, client).access_token()


async def test_send_refreshes_401_once_and_does_not_retry_timeout(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if calls.count(request.url.path) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"id": "reply"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await QQClient(settings, client).send_group("g", "m", "hi")
    assert len(calls) == 4
    calls.clear()

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        qq = QQClient(settings, client)
        qq.token, qq.expires_at = "token", time.monotonic() + 100
        with pytest.raises(QQAPIError):
            await qq.send_group("g", "m", "hi")
    assert len(calls) == 1
