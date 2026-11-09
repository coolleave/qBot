"""按智能体隔离的文件知识库和索引账本。"""
from alembic import op
from alembic.util import CommandError

revision = "0031_knowledge"
down_revision = "0030_provider_web_fetch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS bot_knowledge_settings (
        bot_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
        enabled BOOLEAN NOT NULL DEFAULT 0,
        mode VARCHAR(16) NOT NULL DEFAULT 'assist',
        base_url VARCHAR(2048) NOT NULL DEFAULT '',
        model_name VARCHAR(200) NOT NULL DEFAULT '',
        api_key_encrypted TEXT NULL,
        version INT NOT NULL DEFAULT 1,
        embedding_version INT NOT NULL DEFAULT 1,
        updated_at DATETIME(6) NOT NULL,
        CONSTRAINT fk_knowledge_settings_bot FOREIGN KEY (bot_id) REFERENCES bots(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    op.execute("""CREATE TABLE IF NOT EXISTS knowledge_files (
        id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        bot_id BIGINT UNSIGNED NOT NULL,
        filename VARCHAR(200) NOT NULL,
        extension VARCHAR(8) NOT NULL,
        sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        size_bytes BIGINT NOT NULL,
        status VARCHAR(20) NOT NULL,
        generation INT NOT NULL DEFAULT 0,
        embedding_version INT NOT NULL DEFAULT 1,
        collection_name VARCHAR(100) NOT NULL DEFAULT '',
        chunk_count INT NOT NULL DEFAULT 0,
        embedding_calls INT NOT NULL DEFAULT 0,
        embedding_tokens BIGINT NOT NULL DEFAULT 0,
        unknown_calls INT NOT NULL DEFAULT 0,
        error VARCHAR(200) NOT NULL DEFAULT '',
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        PRIMARY KEY (bot_id, id),
        UNIQUE KEY uq_knowledge_file_hash (bot_id, sha256),
        KEY ix_knowledge_pending (status, updated_at),
        CONSTRAINT fk_knowledge_file_bot FOREIGN KEY (bot_id) REFERENCES bots(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    op.execute("""CREATE TABLE IF NOT EXISTS knowledge_chunks (
        bot_id BIGINT UNSIGNED NOT NULL,
        file_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        generation INT NOT NULL,
        ordinal INT NOT NULL,
        location VARCHAR(200) NOT NULL,
        content TEXT NOT NULL,
        PRIMARY KEY (bot_id, file_id, generation, ordinal),
        CONSTRAINT fk_knowledge_chunk_file FOREIGN KEY (bot_id, file_id)
            REFERENCES knowledge_files(bot_id, id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")


def downgrade() -> None:
    raise CommandError("降级会丢失知识库配置与索引账本，请使用新迁移修正")
