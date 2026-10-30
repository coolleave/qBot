"""网页通话独立配置及记录，保留现有 QQ 消息表。"""
from alembic import op
from alembic.util import CommandError

revision = "0023_voice_calls"
down_revision = "0022_fallback_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS voice_call_configs (
        bot_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
        enabled BOOLEAN NOT NULL DEFAULT FALSE,
        chat_provider_id BIGINT UNSIGNED NULL,
        asr_provider_id BIGINT UNSIGNED NULL,
        tts_provider_id BIGINT UNSIGNED NULL,
        voice VARCHAR(100) NOT NULL,
        max_seconds INT NOT NULL,
        idle_seconds INT NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        CONSTRAINT fk_voice_config_bot FOREIGN KEY (bot_id) REFERENCES bots(id),
        CONSTRAINT fk_voice_config_chat FOREIGN KEY (chat_provider_id) REFERENCES model_providers(id),
        CONSTRAINT fk_voice_config_asr FOREIGN KEY (asr_provider_id) REFERENCES model_providers(id),
        CONSTRAINT fk_voice_config_tts FOREIGN KEY (tts_provider_id) REFERENCES model_providers(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    op.execute("""CREATE TABLE IF NOT EXISTS voice_calls (
        id VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
        bot_id BIGINT UNSIGNED NOT NULL,
        started_at DATETIME(6) NOT NULL,
        ended_at DATETIME(6) NULL,
        status VARCHAR(32) NOT NULL,
        reason VARCHAR(200) NULL,
        model_snapshot TEXT NOT NULL,
        turns MEDIUMTEXT NOT NULL,
        INDEX idx_voice_bot_started (bot_id, started_at),
        CONSTRAINT fk_voice_call_bot FOREIGN KEY (bot_id) REFERENCES bots(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")


def downgrade() -> None:
    raise CommandError("降级会丢失通话记录，请使用新迁移修正")
