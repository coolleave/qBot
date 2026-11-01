"""供应商可选思考参数，默认不发送。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa

revision = "0026_provider_thinking"
down_revision = "0025_realtime_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_providers", sa.Column("enable_thinking", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    raise CommandError("降级会丢失思考配置，请使用新迁移修正")
