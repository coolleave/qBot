"""供应商级百炼网页抓取开关，默认关闭。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa

revision = "0030_provider_web_fetch"
down_revision = "0029_provider_web_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_providers", sa.Column(
        "enable_web_fetch", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    raise CommandError("降级会丢失供应商网页抓取配置，请使用新迁移修正")
