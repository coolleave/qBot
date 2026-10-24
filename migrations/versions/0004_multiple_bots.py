"""多机器人：保留旧配置和历史，按 bot_id 隔离消息。"""
from alembic import op
from alembic.util import CommandError

revision = "0004_multiple_bots"
down_revision = "0003_qq_transport"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE bots (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        app_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
        app_secret_encrypted TEXT NULL,
        transport VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'websocket',
        provider_id BIGINT UNSIGNED NULL,
        system_prompt TEXT NULL,
        enabled TINYINT UNSIGNED NOT NULL DEFAULT 0,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
        UNIQUE KEY uq_bots_app_id (app_id),
        CONSTRAINT fk_bot_provider FOREIGN KEY (provider_id) REFERENCES model_providers(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    op.execute("""INSERT INTO bots (id,name,app_id,app_secret_encrypted,transport,provider_id,enabled)
        SELECT 1,'原有机器人',NULLIF(qq_app_id,''),qq_app_secret_encrypted,qq_transport,
        default_provider_id,IF(qq_app_id IS NOT NULL AND qq_app_id <> '' AND qq_app_secret_encrypted IS NOT NULL,1,0)
        FROM app_settings WHERE id=1""")
    # DEFAULT 1 把现有记录归属到迁移机器人，升级后应用显式写入 bot_id。
    op.execute("""ALTER TABLE conversations
        ADD COLUMN bot_id BIGINT UNSIGNED NOT NULL DEFAULT 1,
        ADD CONSTRAINT fk_conversation_bot FOREIGN KEY (bot_id) REFERENCES bots(id),
        DROP INDEX uq_conversation_identity,
        ADD UNIQUE KEY uq_conversation_identity (bot_id,source,group_id,user_id),
        ADD UNIQUE KEY uq_conversation_bot_scope (id,bot_id,source,group_id)""")
    op.execute("""ALTER TABLE chat_records
        ADD COLUMN bot_id BIGINT UNSIGNED NOT NULL DEFAULT 1,
        DROP INDEX uq_chat_external_message,
        ADD UNIQUE KEY uq_chat_external_message (bot_id,source,group_id,external_message_id),
        ADD KEY idx_chat_bot_time (bot_id,received_at,id),
        ADD CONSTRAINT fk_chat_bot_scope FOREIGN KEY (conversation_id,bot_id,source,group_id)
            REFERENCES conversations(id,bot_id,source,group_id)""")


def downgrade() -> None:
    raise CommandError("多机器人历史不能安全合并回单机器人，请通过新迁移修正")
