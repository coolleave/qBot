"""按机器人分别保存用户备注与群备注，保留冲突旧备注供人工整理。"""
from alembic import op
from alembic.util import CommandError

revision = "0016_contact_notes"
down_revision = "0015_bot_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE bot_contact_notes (
        bot_id BIGINT UNSIGNED NOT NULL,
        target_type VARCHAR(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        openid VARCHAR(256) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        name VARCHAR(100) NOT NULL,
        qq_number VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
        updated_at DATETIME(6) NOT NULL,
        PRIMARY KEY (bot_id, target_type, openid),
        CONSTRAINT fk_contact_note_bot FOREIGN KEY (bot_id) REFERENCES bots(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    # 不挑选某个会话覆盖其他备注；只导入名称和 QQ 备注都无冲突的用户。
    op.execute("""INSERT INTO bot_contact_notes (bot_id,target_type,openid,name,qq_number,updated_at)
        SELECT bot_id,'user',user_id,MAX(contact_name),NULLIF(MAX(COALESCE(contact_qq,'')),''),UTC_TIMESTAMP(6)
        FROM conversations WHERE source IN ('qq_c2c','qq_group') AND contact_name IS NOT NULL AND contact_name<>''
        GROUP BY bot_id,user_id
        HAVING COUNT(DISTINCT BINARY contact_name)=1 AND COUNT(DISTINCT COALESCE(contact_qq,''))=1""")

    # 将无冲突的旧备注同步到相同用户的空备注会话，保持定时任务的资格判断一致。
    op.execute("""UPDATE conversations c JOIN bot_contact_notes n
        ON n.bot_id=c.bot_id AND n.target_type='user' AND n.openid=c.user_id
        SET c.contact_name=n.name,c.contact_qq=n.qq_number
        WHERE c.source IN ('qq_c2c','qq_group')""")


def downgrade() -> None:
    raise CommandError("降级会丢失通讯录备注，请使用新迁移修正")
