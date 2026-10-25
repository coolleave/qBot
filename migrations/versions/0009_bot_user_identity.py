"""家庭身份归属机器人与用户，不再归属会话。"""
from alembic import op
from alembic.util import CommandError

revision = "0009_bot_user_identity"
down_revision = "0008_model_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS bot_user_identities (
        bot_id BIGINT UNSIGNED NOT NULL,
        user_id VARCHAR(256) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        member_name VARCHAR(100) NOT NULL,
        family_role VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        qq_number_note VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
        PRIMARY KEY (bot_id,user_id),
        CONSTRAINT fk_identity_bot FOREIGN KEY (bot_id) REFERENCES bots(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    # 只迁入已有绑定完全一致的用户；有冲突时保留旧字段供管理员核对，避免误认身份。
    op.execute("""INSERT INTO bot_user_identities (bot_id,user_id,member_name,family_role,qq_number_note)
        SELECT c.bot_id,c.user_id,MIN(c.member_name),MIN(c.family_role),MIN(c.qq_number_note)
        FROM conversations c
        LEFT JOIN bot_user_identities i ON i.bot_id=c.bot_id AND i.user_id=c.user_id
        WHERE c.source IN ('qq_group','qq_c2c') AND c.family_role IS NOT NULL
            AND c.member_name IS NOT NULL AND i.bot_id IS NULL
        GROUP BY c.bot_id,c.user_id
        HAVING COUNT(DISTINCT BINARY c.member_name)=1 AND COUNT(DISTINCT c.family_role)=1
            AND COUNT(DISTINCT COALESCE(c.qq_number_note,''))=1""")


def downgrade() -> None:
    raise CommandError("降级会丢失机器人用户身份，请使用新迁移修正")
