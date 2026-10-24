"""模型用途分类，已有模型保持聊天类型。"""
from alembic import op
from alembic.util import CommandError

revision = "0008_model_types"
down_revision = "0007_recipient_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE model_providers ADD COLUMN model_type
        VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'chat'""")


def downgrade() -> None:
    raise CommandError("降级会丢失模型类型，请使用新迁移修正")
