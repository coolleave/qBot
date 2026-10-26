"""定时任务组合内容，图片二进制随配置保存，旧任务保持文字模式。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMBLOB

revision = "0013_greeting_media"
down_revision = "0012_token_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("interaction_greetings")}
    for column in (sa.Column("delivery", sa.Text(), nullable=True),
                   sa.Column("image_data", MEDIUMBLOB(), nullable=True),
                   sa.Column("image_filename", sa.String(150), nullable=True)):
        if column.name not in columns:
            op.add_column("interaction_greetings", column)


def downgrade() -> None:
    raise CommandError("降级会丢失任务图片及语音配置，请使用新迁移修正")
