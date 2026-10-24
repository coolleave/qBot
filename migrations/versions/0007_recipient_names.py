"""接收人名称独立于家庭角色。"""
from alembic import op
from alembic.util import CommandError
revision = "0007_recipient_names"
down_revision = "0006_interaction_greetings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE conversations ADD COLUMN contact_name VARCHAR(100) NULL,
        ADD COLUMN contact_qq VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL""")
    # 保留已有接收对象的显示名称，之后独立编辑，不改变家庭角色。
    op.execute("UPDATE conversations SET contact_name=member_name, contact_qq=qq_number_note WHERE member_name IS NOT NULL")


def downgrade() -> None:
    raise CommandError("降级会丢失接收人名称，请使用新迁移修正")
