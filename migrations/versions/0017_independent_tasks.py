"""同一接收对象支持多条独立定时任务，保留旧早午晚配置。"""
from alembic import op
from alembic.util import CommandError

revision = "0017_independent_tasks"
down_revision = "0016_contact_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 一个 ALTER 内替换索引，继续保留 bot_id 前缀供原外键使用。
    op.execute("""ALTER TABLE interaction_greetings
        ADD COLUMN task_key VARCHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT '',
        ADD COLUMN task_name VARCHAR(100) NOT NULL DEFAULT '',
        DROP INDEX uq_greeting_scope,
        ADD UNIQUE KEY uq_greeting_scope (bot_id,conversation_id,task_key,slot)""")


def downgrade() -> None:
    raise CommandError("多任务无法无损合并为旧早午晚配置，请使用新迁移修正")
