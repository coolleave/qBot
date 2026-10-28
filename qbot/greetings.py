"""定时主动发送配置；北京时间按固定 UTC+8 计算，无需额外时区依赖。"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from qbot.greeting_workflows import GreetingWorkflow, resolve_workflow

BEIJING = timezone(timedelta(hours=8))


class GreetingRule(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    slot: Literal["morning", "noon", "evening", "other"]
    enabled: bool = False
    start: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    # 兼容旧客户端；调度不再使用结束时间。
    end: str | None = Field(default=None, exclude=True, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    weekdays: list[int] = Field(min_length=1, max_length=7)
    content: str = Field(default="", max_length=200)
    delivery_mode: str = Field(default="text", min_length=1, max_length=80)
    workflow: GreetingWorkflow | None = None
    tts_provider_id: int | None = Field(default=None, gt=0)
    voice: str = Field(default="Bunny", min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_ -]+$")
    image_base64: str | None = Field(default=None, max_length=4_666_668)
    image_filename: str = Field(default="", max_length=150)
    remove_image: bool = False

    @model_validator(mode="after")
    def validate_rule(self):
        self.workflow = resolve_workflow(self.model_dump())
        if self.workflow.output == "none":
            self.content = ""
        if self.workflow.output != "none" and self.workflow.content_source == "fixed" and not self.content:
            raise ValueError("请填写 1 至 200 字文案")
        if self.workflow.output in ("audio", "text_audio") and not self.tts_provider_id:
            raise ValueError("语音输出需选择 TTS 供应商")
        if self.remove_image and self.image_base64 is not None:
            raise ValueError("不能同时删除和上传图片")
        if len(set(self.weekdays)) != len(self.weekdays) or any(d not in range(1, 8) for d in self.weekdays):
            raise ValueError("星期必须为不重复的 1 至 7")
        self.weekdays.sort()
        return self


class GreetingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_key: str = Field(default="", pattern=r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?$")
    task_name: str = Field(default="", max_length=100)
    rules: list[GreetingRule] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_rules(self):
        if self.task_key:
            self.task_name = self.task_name.strip()
            if not self.task_name or any(ord(c) < 32 or ord(c) == 127 for c in self.task_name):
                raise ValueError("任务名称必填，最多 100 字且不能包含控制字符")
            if len(self.rules) != 1:
                raise ValueError("独立任务只允许保存一条发送规则")
            return self
        slots = {r.slot for r in self.rules}
        if len(slots) != len(self.rules) or "other" in slots or not {"morning", "evening"}.issubset(slots):
            raise ValueError("需提供不重复的早间、午间和晚间设置")
        # 旧客户端只保存早晚规则时，不覆盖已经保存的午间任务。
        return self


def default_rules(*, include_other: bool = False) -> list[dict]:
    rules = [dict(slot="morning", enabled=False, start="07:00", weekdays=list(range(1, 8)),
                 content="早呀，新的一天开始啦，记得吃早饭喵。"),
            dict(slot="noon", enabled=False, start="12:00", weekdays=list(range(1, 8)),
                 content="中午好呀，记得好好吃午饭，休息一下喵。"),
            dict(slot="evening", enabled=False, start="21:00", weekdays=list(range(1, 8)),
                 content="晚上好呀，忙了一天辛苦了，记得留点时间休息喵。")]
    if include_other:
        rules.append(dict(slot="other", enabled=False, start="17:00", weekdays=list(range(1, 8)),
                          content="你好呀，送你一份小小的关心，愿你今天开开心心。"))
    return rules


def local_time(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc).astimezone(BEIJING)
