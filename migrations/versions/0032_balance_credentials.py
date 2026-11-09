"""独立加密保存供应商账单查询凭证。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa

revision = "0032_balance_credentials"
down_revision = "0031_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_providers", sa.Column("balance_credentials_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    raise CommandError("降级会丢失余额查询凭证，请使用新迁移修正")
