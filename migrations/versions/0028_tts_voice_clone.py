"""TTS 复刻开关与音色创建记录，不保存参考录音。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa

revision = "0028_tts_voice_clone"
down_revision = "0027_thinking_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_providers", sa.Column("supports_voice_cloning", sa.Boolean(),
                  nullable=False, server_default=sa.text("0")))
    op.execute("""CREATE TABLE IF NOT EXISTS provider_tts_voices (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        provider_id BIGINT UNSIGNED NOT NULL,
        request_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        model_name VARCHAR(200) NOT NULL,
        service_url VARCHAR(2048) NOT NULL,
        prefix VARCHAR(10) NOT NULL,
        voice_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
        status VARCHAR(16) NOT NULL,
        error VARCHAR(200) NOT NULL DEFAULT '',
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        UNIQUE KEY uq_provider_voice_request (provider_id, request_id),
        CONSTRAINT fk_tts_voice_provider FOREIGN KEY (provider_id) REFERENCES model_providers(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")


def downgrade() -> None:
    raise CommandError("降级会丢失复刻音色记录，请使用新迁移修正")
