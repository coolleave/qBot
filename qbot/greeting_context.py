"""定时文案的客观时间与默认城市天气；失败时不影响问候生成。"""
import asyncio
import logging
import math
import time
from datetime import datetime, timedelta

import httpx

from qbot.greetings import BEIJING

logger = logging.getLogger(__name__)
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
# 用户指定默认石家庄。坐标取市中心，仅用于城市天气，不代表接收人位置。
WEATHER_PARAMS = {
    "latitude": 38.0428, "longitude": 114.5149, "timezone": "Asia/Shanghai",
    "current": "temperature_2m,apparent_temperature,weather_code",
    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    "forecast_days": 1, "temperature_unit": "celsius",
}
WEATHER_CODES = {
    0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴天", 45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "较强毛毛雨", 56: "轻冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "轻冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "小阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}
_weather_cache: tuple[float, str, dict] | None = None


def time_context(now: datetime) -> dict:
    local = now.astimezone(BEIJING)
    hour = local.hour
    period = ("凌晨" if hour < 5 else "清晨" if hour < 9 else "上午" if hour < 11 else
              "中午" if hour < 14 else "下午" if hour < 18 else "傍晚" if hour < 20 else
              "晚间" if hour < 23 else "深夜")
    return {"当前北京时间": local.strftime("%Y-%m-%d %H:%M"), "时区": "Asia/Shanghai (UTC+8)",
            "公历日期": local.date().isoformat(), "星期": "星期" + "一二三四五六日"[local.weekday()],
            "时段": period, "日历说明": "星期不代表法定工作日、放假或个人日程；未提供节假日及调休数据"}


def _number(value: object, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("天气数值无效")
    return round(value, 1)


def _parse_weather(data: dict, now: datetime) -> dict:
    current, daily = data["current"], data["daily"]
    observed = datetime.fromisoformat(current["time"])
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=BEIJING)
    local = now.astimezone(BEIJING)
    age = local - observed
    if not -timedelta(minutes=10) <= age <= timedelta(minutes=45):
        raise ValueError("天气时间不在有效范围")
    if daily["time"][0] != local.date().isoformat():
        raise ValueError("预报不是当天数据")
    if data.get("utc_offset_seconds") != 28800:
        raise ValueError("天气时区不匹配")
    if data["current_units"]["temperature_2m"] != "°C" or data["current_units"]["apparent_temperature"] != "°C":
        raise ValueError("天气温度单位不匹配")
    if any(data["daily_units"][key] != unit for key, unit in (
            ("temperature_2m_max", "°C"), ("temperature_2m_min", "°C"), ("precipitation_probability_max", "%"))):
        raise ValueError("天气预报单位不匹配")
    high = _number(daily["temperature_2m_max"][0], -80, 60)
    low = _number(daily["temperature_2m_min"][0], -80, 60)
    if low > high:
        raise ValueError("天气温度范围无效")
    code = current["weather_code"]
    if isinstance(code, bool) or not isinstance(code, int):
        raise ValueError("天气代码无效")
    return {"状态": "可用", "城市": "石家庄", "来源": "Open-Meteo",
            "天气时间": observed.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M"),
            "当前气温℃": _number(current["temperature_2m"], -80, 60),
            "当前体感℃": _number(current["apparent_temperature"], -100, 80),
            "当前天气": WEATHER_CODES.get(code, "未提供可识别的天气现象"),
            "今日预报": {"日期": daily["time"][0], "最低气温℃": low, "最高气温℃": high,
                         "最高降水概率%": _number(daily["precipitation_probability_max"][0], 0, 100)}}


async def weather_context(now: datetime) -> dict:
    global _weather_cache
    today = now.astimezone(BEIJING).date().isoformat()
    if _weather_cache and time.monotonic() < _weather_cache[0] and _weather_cache[1] == today:
        return _weather_cache[2].copy()
    try:
        async with asyncio.timeout(5):
            async with httpx.AsyncClient(timeout=4, follow_redirects=False) as client:
                response = await client.get(WEATHER_URL, params=WEATHER_PARAMS)
                response.raise_for_status()
                result = _parse_weather(response.json(), now)
        # 缓存不能让临近过期的气象数据超过45分钟仍被使用。
        observed = datetime.fromisoformat(result["天气时间"]).replace(tzinfo=BEIJING)
        ttl = min(600, max(0, (observed + timedelta(minutes=45) - datetime.now(BEIJING)).total_seconds()))
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, IndexError) as exc:
        logger.warning("石家庄天气暂不可用 type=%s；本次文案不引用具体天气", type(exc).__name__)
        result = {"状态": "不可用", "城市": "石家庄", "说明": "天气查询失败或数据无效；不要描述当前天气、气温或降水预报"}
        ttl = 60
    _weather_cache = (time.monotonic() + ttl, today, result)
    return result.copy()
