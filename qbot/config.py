from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
        hide_input_in_errors=True,
    )

    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    model_timeout_seconds: float = Field(45, gt=0, le=120)
    model_max_retries: int = Field(1, ge=0, le=2)
    model_max_tokens: int = Field(1024, ge=1, le=8192)
    chat_timeout_seconds: float = Field(90, gt=0, le=120)
    qq_transport: Literal["websocket", "webhook"] = "websocket"
    qq_app_id: str = ""
    qq_app_secret: SecretStr = SecretStr("")
    qq_api_base_url: str = "https://api.bot.qq.com"
    qq_token_url: str = "https://api.bot.qq.com/app/getAppAccessToken"
    qq_timeout_seconds: float = Field(10, gt=0, le=30)
    host: str = "127.0.0.1"
    port: int = Field(8092, ge=1, le=65535)
    session_ttl_seconds: int = Field(1800, ge=1)
    session_max_turns: int = Field(10, ge=1, le=50)
    session_max_count: int = Field(1000, ge=1, le=10000)
    max_input_chars: int = Field(4000, ge=1, le=10000)
    max_reply_chars: int = Field(1500, ge=100, le=4000)
    rate_limit_per_minute: int = Field(6, ge=1, le=60)
    global_rate_limit_per_minute: int = Field(30, ge=1, le=600)
    queue_max_size: int = Field(32, ge=1, le=100)
    admin_enabled: bool = True
    mysql_host: str = "127.0.0.1"
    mysql_port: int = Field(3306, ge=1, le=65535)
    mysql_database: str = "qbot"
    mysql_user: str = ""
    mysql_password: SecretStr = SecretStr("")
    admin_username: str = "admin"
    admin_password_hash: SecretStr = SecretStr("")
    admin_session_secret: SecretStr = SecretStr("")
    provider_encryption_key: SecretStr = SecretStr("")
    admin_cookie_secure: bool = False

    def require_admin(self) -> None:
        from cryptography.fernet import Fernet
        from qbot.security import valid_password_hash

        if not self.mysql_user or not self.mysql_database:
            raise ValueError("请配置 MYSQL_USER 和 MYSQL_DATABASE")
        if not self.admin_username or not valid_password_hash(self.admin_password_hash.get_secret_value()):
            raise ValueError("请配置 ADMIN_USERNAME，并使用 hash-password 生成 ADMIN_PASSWORD_HASH")
        if len(self.admin_session_secret.get_secret_value()) < 32:
            raise ValueError("ADMIN_SESSION_SECRET 至少需要 32 个字符")
        try:
            Fernet(self.provider_encryption_key.get_secret_value().encode())
        except ValueError:
            raise ValueError("PROVIDER_ENCRYPTION_KEY 无效，请使用 generate-secrets 生成") from None

    @field_validator("deepseek_base_url", "qq_api_base_url", "qq_token_url")
    @classmethod
    def validate_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("外部 API 地址必须使用 HTTPS")
        return value.rstrip("/")

    def require_credentials(self, *, qq: bool = False) -> None:
        missing = []
        if not self.deepseek_api_key.get_secret_value().strip():
            missing.append("DEEPSEEK_API_KEY")
        if qq:
            if not self.qq_app_id.strip():
                missing.append("QQ_APP_ID")
            if not self.qq_app_secret.get_secret_value().strip():
                missing.append("QQ_APP_SECRET")
        if missing:
            raise ValueError("请在 .env 配置：" + ", ".join(missing))
