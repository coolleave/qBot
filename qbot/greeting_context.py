"""定时文案的客观时间与默认城市天气；失败时不影响问候生成。"""
import asyncio
import logging
import math
import time
from collections import OrderedDict
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
_weather_cache: OrderedDict[str, tuple[float, str, dict]] = OrderedDict()
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


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


def _parse_weather(data: dict, now: datetime, city: str = "石家庄") -> dict:
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
    return {"状态": "可用", "城市": city, "来源": "Open-Meteo",
            "天气时间": observed.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M"),
            "当前气温℃": _number(current["temperature_2m"], -80, 60),
            "当前体感℃": _number(current["apparent_temperature"], -100, 80),
            "当前天气": WEATHER_CODES.get(code, "未提供可识别的天气现象"),
            "今日预报": {"日期": daily["time"][0], "最低气温℃": low, "最高气温℃": high,
                         "最高降水概率%": _number(daily["precipitation_probability_max"][0], 0, 100)}}


async def weather_context(now: datetime, city: str = "石家庄") -> dict:
    """复用天气校验；城市解析和预报共享总期限，不向外部发送聊天历史。"""
    city = city.strip()
    if not city or len(city) > 40:
        return {"状态": "不可用", "说明": "请提供不超过40字的城市名称，例如 /weather 北京"}
    cache_key = city.casefold()
    today = now.astimezone(BEIJING).date().isoformat()
    cached = _weather_cache.get(cache_key)
    if cached and time.monotonic() < cached[0] and cached[1] == today:
        _weather_cache.move_to_end(cache_key)
        return cached[2].copy()
    resolved_city = city
    try:
        async with asyncio.timeout(5):
            async with httpx.AsyncClient(timeout=4, follow_redirects=False) as client:
                params = WEATHER_PARAMS.copy()
                if city not in {"石家庄", "石家庄市"}:
                    response = await client.get(GEOCODING_URL, params={
                        "name": city, "count": 5, "language": "zh", "format": "json"})
                    response.raise_for_status()
                    locations = response.json().get("results", [])
                    # 优先精确名称，不能直接把同名搜索结果的第一项当作用户位置。
                    exact = [item for item in locations
                             if item.get("name", "").removesuffix("市").casefold()
                             == city.removesuffix("市").casefold()]
                    locations = exact or locations
                    if not locations:
                        return {"状态": "不可用", "城市": city, "说明": "未找到该城市，请检查名称或使用更明确的城市名称"}
                    if len(locations) != 1:
                        candidates = [" / ".join(str(item.get(key) or "") for key in
                                      ("country", "admin1", "name")) for item in locations]
                        return {"状态": "需确认城市", "城市": city, "候选地点": candidates,
                                "说明": "存在多个匹配地点，请用户提供更明确的城市名称后重新发送 /weather 城市；尚未查询天气"}
                    location = locations[0]
                    # 复用范围校验，但保留坐标精度，避免四舍五入到其他城区。
                    _number(location["latitude"], -90, 90)
                    _number(location["longitude"], -180, 180)
                    params["latitude"] = location["latitude"]
                    params["longitude"] = location["longitude"]
                    resolved_city = " / ".join(str(location.get(key) or "") for key in
                                               ("country", "admin1", "name"))
                response = await client.get(WEATHER_URL, params=params)
                response.raise_for_status()
                result = _parse_weather(response.json(), datetime.now(BEIJING), resolved_city)
        # 缓存不能让临近过期的气象数据超过45分钟仍被使用。
        observed = datetime.fromisoformat(result["天气时间"]).replace(tzinfo=BEIJING)
        ttl = min(600, max(0, (observed + timedelta(minutes=45) - datetime.now(BEIJING)).total_seconds()))
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        logger.warning("天气暂不可用 type=%s；本次回复不引用具体天气", type(exc).__name__)
        result = {"状态": "不可用", "城市": resolved_city, "说明": "天气查询失败或数据无效；不要描述当前天气、气温或降水预报"}
        ttl = 60
    _weather_cache[cache_key] = (time.monotonic() + ttl, today, result)
    _weather_cache.move_to_end(cache_key)
    while len(_weather_cache) > 128:
        _weather_cache.popitem(last=False)
    return result.copy()
