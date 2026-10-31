"""普通聊天的客观时间和天气参考；不依赖模型工具调用能力。"""
import json
import re
from datetime import datetime

from langchain_core.messages import BaseMessage, HumanMessage

from qbot.greeting_context import time_context, weather_context
from qbot.greetings import BEIJING

_WEATHER_TOPIC = re.compile(
    r"天气|气温|温度|几度|多少度|降温|升温|下雨|下雪|降雨|降雪|降水|带伞|打伞|雨伞|"
    r"晴天|阴天|多云|冷不冷|热不热|冷吗|热吗|会冷|会热|穿什么|穿啥|穿衣|"
    r"风力|风速|空气质量|雾霾|\b(?:weather|temperature|rain|snow|umbrella)\b", re.I)
_WEATHER_FOLLOWUP = re.compile(
    r"^(?:那|今天|明天|后天|现在|今晚|早上|上午|中午|下午|晚上|周末|这周|下周|"
    r"会不会|要不要|需不需要|出门|穿|带|几度|多少度|"
    r"what about|how about)", re.I)


def needs_weather(text: str, history: list[BaseMessage]) -> bool:
    """只用当前问题及最近用户发言识别追问，避免模型旧回复触发查询。"""
    if _WEATHER_TOPIC.search(text):
        return True
    if len(text) > 40 or not (_WEATHER_FOLLOWUP.search(text) or text.rstrip("？?。！! ").endswith("呢")):
        return False
    previous = next((message.content for message in reversed(history)
                     if isinstance(message, HumanMessage)), "")
    return isinstance(previous, str) and bool(_WEATHER_TOPIC.search(previous))


async def chat_context(text: str, history: list[BaseMessage]) -> str:
    """查询失败由共享天气模块明确返回；取消与聊天总超时正常向上传播。"""
    weather = None
    if needs_weather(text, history):
        weather = await weather_context(datetime.now(BEIJING))
    # 网络等待后重新取时间，防止跨日时把昨日预报当作今日天气。
    now = datetime.now(BEIJING)
    context = time_context(now)
    if weather is not None:
        if weather.get("今日预报", {}).get("日期", now.date().isoformat()) != now.date().isoformat():
            weather = {"状态": "不可用", "城市": "石家庄", "说明": "跨日后不沿用昨日天气"}
        context["天气参考"] = weather
    else:
        context["天气参考"] = {"状态": "未查询"}
    return json.dumps(context, ensure_ascii=False)
