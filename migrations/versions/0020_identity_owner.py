"""为每个机器人选择唯一主人，旧身份不自动认领。"""
from alembic import op
from alembic.util import CommandError

revision = "0020_identity_owner"
down_revision = "0019_user_nickname"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE bot_user_identities
        ADD COLUMN owner_slot TINYINT NULL,
        ADD COLUMN owner_selected_at DATETIME(6) NULL,
        ADD UNIQUE INDEX uq_identity_owner (bot_id, owner_slot)""")


def downgrade() -> None:
    raise CommandError("降级会丢失主人绑定，请使用新迁移修正")
