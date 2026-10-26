"""按机器人保存角色定义，保留无法匹配的旧绑定供管理员处理。"""
import json
from pathlib import Path

from alembic import op
from alembic.util import CommandError
from sqlalchemy import text

revision = "0015_bot_roles"
down_revision = "0014_manual_greeting_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE bot_roles (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        bot_id BIGINT UNSIGNED NOT NULL,
        role_key VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        label VARCHAR(100) NOT NULL,
        relationship VARCHAR(500) NOT NULL,
        member_name VARCHAR(100) NOT NULL DEFAULT '',
        PRIMARY KEY (id),
        UNIQUE KEY uq_bot_role_key (bot_id, role_key),
        UNIQUE KEY uq_bot_role_id (bot_id, id),
        CONSTRAINT fk_role_bot FOREIGN KEY (bot_id) REFERENCES bots(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    op.execute("""ALTER TABLE bot_user_identities ADD COLUMN role_id BIGINT UNSIGNED NULL,
        ADD INDEX ix_identity_role (bot_id,role_id),
        ADD CONSTRAINT fk_identity_role FOREIGN KEY (bot_id,role_id) REFERENCES bot_roles(bot_id,id)""")
    catalog = json.loads((Path(__file__).resolve().parent.parent / "snapshots" /
                         "0015_identity_roles.json").read_text(encoding="utf-8"))
    conn = op.get_bind()
    bot_ids = conn.execute(text("SELECT id FROM bots")).scalars().all()
    for bot_id in bot_ids:
        roles = catalog.get(str(bot_id), [{"value": "friend", "label": "朋友",
            "relationship": "当前智能体的普通朋友", "member_name": ""}])
        for role in roles:
            conn.execute(text("""INSERT INTO bot_roles
                (bot_id,role_key,label,relationship,member_name)
                VALUES (:bot_id,:value,:label,:relationship,:member_name)"""),
                {"bot_id": bot_id, **role})
    # 不根据姓名猜测角色；二进制比较与 Python 姓名校验保持一致。
    op.execute("""UPDATE bot_user_identities i JOIN bot_roles r
        ON r.bot_id=i.bot_id AND r.role_key=i.family_role
        AND (r.member_name='' OR BINARY r.member_name=BINARY i.member_name)
        SET i.role_id=r.id WHERE i.role_id IS NULL""")


def downgrade() -> None:
    raise CommandError("降级会丢失角色定义与关联，请使用新迁移修正")
