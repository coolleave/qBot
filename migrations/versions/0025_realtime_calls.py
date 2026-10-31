"""实时对话模式与独立网页记忆，不改动 QQ 记忆。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT

revision = "0025_realtime_calls"
down_revision = "0024_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("voice_call_configs", sa.Column("mode", sa.String(16), nullable=False, server_default="pipeline"))
    op.add_column("voice_call_configs", sa.Column("realtime_provider_id", BIGINT(unsigned=True), nullable=True))
    op.add_column("voice_call_configs", sa.Column("realtime_voice", sa.String(100), nullable=False, server_default="longanqian_v3.1"))
    op.create_foreign_key("fk_voice_config_realtime", "voice_call_configs", "model_providers", ["realtime_provider_id"], ["id"])
    op.execute("""CREATE TABLE IF NOT EXISTS voice_memories (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        bot_id BIGINT UNSIGNED NOT NULL,
        owner_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        content VARCHAR(600) NOT NULL,
        content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        created_at DATETIME(6) NOT NULL,
        UNIQUE KEY uq_voice_memory (bot_id, owner_hash, content_hash),
        CONSTRAINT fk_voice_memory_bot FOREIGN KEY (bot_id) REFERENCES bots(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")


def downgrade() -> None:
    raise CommandError("降级会丢失网页记忆，请使用新迁移修正")
