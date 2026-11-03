"""供应商级百炼联网搜索模式，已有供应商默认关闭。"""
from alembic import op
from alembic.util import CommandError
import sqlalchemy as sa

revision = "0029_provider_web_search"
down_revision = "0028_tts_voice_clone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_providers", sa.Column(
        "web_search_mode", sa.String(16), nullable=False, server_default="disabled"))


def downgrade() -> None:
    raise CommandError("降级会丢失供应商联网搜索配置，请使用新迁移修正")
