"""全局备用聊天模型，默认不启用，不更改已有供应商。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0022_fallback_provider"
down_revision = "0021_scheduled_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("fallback_provider_id", mysql.BIGINT(unsigned=True), nullable=True))
    op.create_index("idx_settings_fallback_provider", "app_settings", ["fallback_provider_id"])
    op.create_foreign_key("fk_settings_fallback_provider", "app_settings", "model_providers", ["fallback_provider_id"], ["id"])


def downgrade() -> None:
    raise CommandError("降级会丢失备用模型配置，请使用新迁移修正")
