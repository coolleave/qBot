"""在现有会话身份上保存管理员确认的家庭绑定。"""
from alembic import op
from alembic.util import CommandError

revision = "0005_family_binding"
down_revision = "0004_multiple_bots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE conversations
        ADD COLUMN member_name VARCHAR(100) NULL,
        ADD COLUMN family_role VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
        ADD COLUMN qq_number_note VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL""")


def downgrade() -> None:
    raise CommandError("降级会丢失家庭绑定，请通过新迁移修正")
