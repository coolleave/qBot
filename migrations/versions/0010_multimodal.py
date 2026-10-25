"""多模态能力配置及处理阶段记录，MySQL 5.6 使用 TEXT 保存 JSON。"""
from alembic import op
from alembic.util import CommandError

revision = "0010_multimodal"
down_revision = "0009_bot_user_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE bots ADD COLUMN multimodal TEXT NULL")
    op.execute("ALTER TABLE model_providers ADD COLUMN supports_images TINYINT UNSIGNED NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE chat_records ADD COLUMN media_details MEDIUMTEXT NULL")


def downgrade() -> None:
    raise CommandError("降级会丢失多模态配置与记录，请使用新迁移修正")
