"""将思考参数配置扩展为默认、开启、关闭三种模式。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa

revision = "0027_thinking_mode"
down_revision = "0026_provider_thinking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_providers", sa.Column(
        "thinking_mode", sa.String(16), nullable=False, server_default="default"))
    # 保留原行为：原先未勾选只代表不传参数，不能转换为显式关闭。
    op.execute(sa.text("UPDATE model_providers SET thinking_mode = 'enabled' WHERE enable_thinking = 1"))


def downgrade() -> None:
    raise CommandError("旧版布尔开关无法表达显式关闭，请使用新迁移修正")
