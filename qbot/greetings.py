"""首次互动问候配置；北京时间按固定 UTC+8 计算，无需额外时区依赖。"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BEIJING = timezone(timedelta(hours=8))


class GreetingRule(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    slot: Literal["morning", "evening"]
    enabled: bool = False
    start: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    end: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    weekdays: list[int] = Field(min_length=1, max_length=7)
    content: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_rule(self):
        if self.start >= self.end:
            raise ValueError("结束时间必须晚于开始时间，暂不支持跨午夜")
        if len(set(self.weekdays)) != len(self.weekdays) or any(d not in range(1, 8) for d in self.weekdays):
            raise ValueError("星期必须为不重复的 1 至 7")
        self.weekdays.sort()
        return self


class GreetingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[GreetingRule] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_rules(self):
        if {r.slot for r in self.rules} != {"morning", "evening"}:
            raise ValueError("需同时提供早间和晚间设置")
        a, b = self.rules
        if (a.enabled and b.enabled and set(a.weekdays) & set(b.weekdays)
                and a.start < b.end and b.start < a.end):
            raise ValueError("启用的问候时段不能重叠")
        return self


def default_rules() -> list[dict]:
    return [dict(slot="morning", enabled=False, start="07:00", end="10:00", weekdays=list(range(1, 8)),
                 content="早呀，新的一天开始啦，记得吃早饭喵。"),
            dict(slot="evening", enabled=False, start="21:00", end="23:30", weekdays=list(range(1, 8)),
                 content="晚上好呀，忙了一天辛苦了，记得留点时间休息喵。")]


def local_time(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc).astimezone(BEIJING)
