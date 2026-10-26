"""手动执行独立记录，自动任务继续按日期去重。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa

revision = "0014_manual_greeting_runs"
down_revision = "0013_greeting_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MySQL 唯一索引允许多个 NULL；不改变已有自动领取和外键。
    op.alter_column("interaction_greeting_runs", "local_date",
                    existing_type=sa.Date(), nullable=True)


def downgrade() -> None:
    raise CommandError("手动记录无每日领取日期，禁止丢弃历史降级，请新增迁移修正")
