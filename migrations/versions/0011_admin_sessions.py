"""持久化管理员登录会话，保留重启前尚未过期的登录。"""
from alembic import op
from alembic.util import CommandError

revision = "0011_admin_sessions"
down_revision = "0010_multimodal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS admin_sessions (
        token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        csrf_token VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        auth_version CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        created_at DATETIME NOT NULL,
        expires_at DATETIME NOT NULL,
        PRIMARY KEY (token_hash),
        KEY ix_admin_sessions_expires_at (expires_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")


def downgrade() -> None:
    raise CommandError("降级会丢失管理员会话，请使用新迁移修正")
