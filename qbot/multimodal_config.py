"""机器人多模态配置；默认关闭付费附件处理和自动语音。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MultimodalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    images: bool = False
    gifs: bool = False
    asr: bool = False
    vision_provider_id: int | None = Field(default=None, gt=0)
    asr_provider_id: int | None = Field(default=None, gt=0)
    tts_provider_id: int | None = Field(default=None, gt=0)
    voice: str = Field(default="Bunny", min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_ -]+$")
    private_reply: Literal["text", "follow", "voice"] = "text"
    group_reply: Literal["text", "follow", "voice"] = "text"

    @model_validator(mode="after")
    def check_bindings(self):
        if self.gifs and not self.images:
            raise ValueError("GIF 理解需要同时启用图片理解")
        if self.asr and not self.asr_provider_id:
            raise ValueError("开启语音识别需选择 ASR 供应商")
        if (self.private_reply != "text" or self.group_reply != "text") and not self.tts_provider_id:
            raise ValueError("自动语音回复需选择 TTS 供应商")
        return self
