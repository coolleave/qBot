"""记录配置保存时间，避免保存后补发过期定时任务。"""
from alembic import op
from alembic.util import CommandError

revision = "0018_schedule_timing"
down_revision = "0017_independent_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE interaction_greetings ADD COLUMN schedule_updated_at DATETIME(6) NULL")
    op.execute("UPDATE interaction_greetings SET schedule_updated_at=UTC_TIMESTAMP(6)")
    op.execute("ALTER TABLE interaction_greetings MODIFY COLUMN schedule_updated_at DATETIME(6) NOT NULL")


def downgrade() -> None:
    raise CommandError("降级会恢复过期补发语义，请使用新迁移修正")
