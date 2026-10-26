"""保存平台昵称，与管理员备注独立。"""
from alembic import op
from alembic.util import CommandError

revision = "0019_user_nickname"
down_revision = "0018_schedule_timing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations ADD COLUMN platform_nickname VARCHAR(256) NULL")


def downgrade() -> None:
    raise CommandError("降级会丢失平台昵称，请使用新迁移修正")
