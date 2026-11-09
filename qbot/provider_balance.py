"""供应商账户余额查询；只访问已明确支持的官方端点。"""

import asyncio
import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx


def balance_capability(provider: dict) -> dict:
    """按实际地址识别服务，不能根据模型名将第三方 Key 发给官方。"""
    try:
        url = urlsplit(provider["base_url"])
        if (url.scheme != "https" or url.port not in (None, 443)
                or url.username or url.password or url.query or url.fragment):
            raise ValueError
    except (ValueError, KeyError):
        return {"supported": False, "message": "该供应商暂不支持余额查询"}
    if url.hostname == "api.deepseek.com" and url.path.rstrip("/") in ("", "/v1", "/beta"):
        return {"supported": True, "message": "DeepSeek 账户余额"}
    if url.hostname in {
        "dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com", "coding.dashscope.aliyuncs.com",
    }:
        return {
            "supported": True, "service": "aliyun", "message": "阿里云账户余额",
        }
    return {"supported": False, "message": "该供应商暂不支持余额查询"}


async def query_aliyun_balance(credentials: dict) -> dict:
    """RPC 签名只查询资金；模型地域和账单账户站点是两个独立概念。"""
    endpoints = {"china": "https://business.aliyuncs.com/",
                 "international": "https://business.ap-southeast-1.aliyuncs.com/"}
    endpoint = endpoints.get(credentials.get("site"))
    if not endpoint:
        raise ValueError("余额凭证站点无效，请重新配置")
    params = {
        "Action": "QueryAccountBalance", "Version": "2017-12-14", "Format": "JSON",
        "AccessKeyId": credentials["access_key_id"], "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0", "SignatureNonce": str(uuid4()),
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    canonical = "&".join(f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted(params.items()))
    string_to_sign = "POST&%2F&" + quote(canonical, safe="~")
    params["Signature"] = base64.b64encode(hmac.new(
        (credentials["access_key_secret"] + "&").encode(), string_to_sign.encode(), hashlib.sha1,
    ).digest()).decode()
    try:
        async with asyncio.timeout(15):
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                # 签名和 AccessKey ID 放请求体，不出现在 URL 或访问日志中。
                async with client.stream("POST", endpoint, data=params) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise ValueError("官方余额响应过大")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("官方余额响应格式异常")
        if response.status_code != 200 or data.get("Success") is not True:
            code = data.get("Code")
            if code in ("NoPermission", "NotAuthorized", "Forbidden", "Forbidden.RAM"):
                raise ValueError("AccessKey 缺少账户余额查询权限 bss:DescribeAcccount")
            if code in ("InvalidAccessKeyId.NotFound", "SignatureDoesNotMatch", "InvalidAccessKeyId"):
                raise ValueError("AccessKey 无效或签名不匹配，请检查余额凭证")
            raise ValueError("阿里云余额查询失败，请检查凭证、权限及账户站点后重试")
        balance = data.get("Data")
        if not isinstance(balance, dict) or balance.get("Currency") not in ("CNY", "USD", "JPY"):
            raise ValueError("官方余额响应格式异常")
        item = {"currency": balance["Currency"]}
        for source, target in (("AvailableCashAmount", "cash_balance"), ("AvailableAmount", "total_balance"),
                               ("CreditAmount", "credit_balance"), ("MybankCreditAmount", "bank_credit_balance")):
            value = balance.get(source)
            # 兼容账单接口返回的千分位字符串，绝不将缺失金额当作零。
            if not isinstance(value, str) or not re.fullmatch(r"-?(?:\d{1,20}|\d{1,3}(?:,\d{3}){1,6})(?:\.\d{1,10})?", value):
                raise ValueError("官方余额金额格式异常")
            item[target] = value.replace(",", "")
        return {"supported": True, "service": "aliyun", "items": [item],
                "checked_at": datetime.now(timezone.utc).isoformat()}
    except (TimeoutError, httpx.TimeoutException):
        raise ValueError("余额查询超时，请稍后重试") from None
    except httpx.HTTPError:
        raise ValueError("无法连接阿里云余额接口，请稍后重试") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("官方余额响应格式异常") from None


async def query_balance(provider: dict) -> dict:
    capability = balance_capability(provider)
    if not capability["supported"]:
        return capability
    try:
        # 总时间和解压后的响应体均受限，且不跟随重定向传递凭证。
        async with asyncio.timeout(15):
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                async with client.stream(
                    "GET", "https://api.deepseek.com/user/balance",
                    headers={"Authorization": f"Bearer {provider['api_key']}"},
                ) as response:
                    if response.status_code != 200:
                        message = {
                            401: "API Key 无效或已失效", 403: "该 Key 没有余额查询权限",
                            429: "官方接口限流，请稍后重试",
                        }.get(response.status_code, "官方余额接口暂不可用")
                        raise ValueError(message)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise ValueError("官方余额响应过大")
        data = json.loads(body)
        if not isinstance(data, dict) or type(data.get("is_available")) is not bool:
            raise ValueError("官方余额响应格式异常")
        balances = data.get("balance_infos")
        if not isinstance(balances, list) or not 1 <= len(balances) <= 10:
            raise ValueError("官方未返回有效余额明细")
        items = []
        for balance in balances:
            if not isinstance(balance, dict) or balance.get("currency") not in ("CNY", "USD"):
                raise ValueError("官方余额币种格式异常")
            item = {"currency": balance["currency"]}
            for field in ("total_balance", "granted_balance", "topped_up_balance"):
                value = balance.get(field)
                if not isinstance(value, str) or not re.fullmatch(r"-?\d{1,20}(?:\.\d{1,10})?", value):
                    raise ValueError("官方余额金额格式异常")
                item[field] = value
            items.append(item)
        return {**capability, "is_available": data["is_available"], "items": items,
                "checked_at": datetime.now(timezone.utc).isoformat()}
    except (TimeoutError, httpx.TimeoutException):
        raise ValueError("余额查询超时，请稍后重试") from None
    except httpx.HTTPError:
        raise ValueError("无法连接官方余额接口，请稍后重试") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("官方余额响应格式异常") from None
