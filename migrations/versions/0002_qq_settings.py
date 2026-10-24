"""新增后台 QQ 凭证配置。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa

revision = "0002_qq_settings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("qq_app_id", sa.String(64), nullable=True))
    op.add_column("app_settings", sa.Column("qq_app_secret_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    raise CommandError("该版本包含 QQ 凭证，不自动删除；请通过新迁移修正")
