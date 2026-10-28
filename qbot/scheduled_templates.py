"""模板输入校验；任务套用后保存独立快照。"""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from qbot.greeting_workflows import GreetingWorkflow
from qbot.greetings import GreetingRule


class ScheduledTemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    workflow: GreetingWorkflow
    content: str = Field(default="", max_length=200)
    tts_provider_id: int | None = Field(default=None, gt=0)
    voice: str = Field(default="Bunny", min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_ -]+$")
    image_base64: str | None = Field(default=None, max_length=4_666_668)
    image_filename: str = Field(default="", max_length=150)
    remove_image: bool = False

    @model_validator(mode="after")
    def validate_template(self):
        if self.workflow.output == "none":
            self.content = ""
        if any(ord(c) < 32 or ord(c) == 127 for c in self.name):
            raise ValueError("模板名称不能含控制字符")
        if self.workflow.content_source == "ai" and not (self.workflow.custom_prompt or self.workflow.prompt_style):
            raise ValueError("AI 模板请填写提示词或选择文案风格")
        # 复用实际执行规则，避免模板可保存而任务无法使用。
        GreetingRule.model_validate(dict(slot="other", start="00:00", weekdays=[1],
            **self.model_dump(exclude={"name"})))
        return self
