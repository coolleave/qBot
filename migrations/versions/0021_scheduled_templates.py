"""管理员维护可复用的定时消息模板，不更改已有任务。"""
from alembic import op
from alembic.util import CommandError

revision = "0021_scheduled_templates"
down_revision = "0020_identity_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS scheduled_templates (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        config MEDIUMTEXT NOT NULL,
        image_data MEDIUMBLOB NULL,
        image_filename VARCHAR(150) NULL,
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")


def downgrade() -> None:
    raise CommandError("降级会丢失模板，请使用新迁移修正")
