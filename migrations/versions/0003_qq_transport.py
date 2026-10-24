"""QQ 默认使用 WebSocket，可选 Webhook。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa

revision = "0003_qq_transport"
down_revision = "0002_qq_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("qq_transport", sa.String(16), nullable=False,
                                          server_default="websocket"))


def downgrade() -> None:
    raise CommandError("不自动删除接入方式配置，请通过新迁移修正")
