"""为聊天记录增加可汇总的聊天模型 token 用量。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa

revision = "0012_token_usage"
down_revision = "0011_admin_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MySQL DDL 不支持事务回滚；允许中断后重新执行，旧记录保持 NULL。
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("chat_records")}
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        if name not in columns:
            op.add_column("chat_records", sa.Column(name, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    raise CommandError("降级会丢失 token 用量，请使用新迁移修正")
