"""角色输入校验；角色定义由数据库管理，不再读取本地配置。"""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class IdentityRole(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    value: str = Field(min_length=1, max_length=16, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=100)
    member_name: str = Field(default="", max_length=100)
    relationship: str = Field(default="", max_length=500)

    @field_validator("label", "member_name", "relationship")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("角色字段不能包含控制字符")
        return value
