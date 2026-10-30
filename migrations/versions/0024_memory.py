"""持久化记忆与异步整理账本；不回填旧聊天。"""
from alembic import op
from alembic.util import CommandError

revision = "0024_memory"
down_revision = "0023_voice_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS bot_memory_settings (
        bot_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
        config TEXT NOT NULL,
        version BIGINT NOT NULL DEFAULT 1,
        owner_version BIGINT NOT NULL DEFAULT 1,
        enabled_after BIGINT NOT NULL DEFAULT 0,
        paused_reason VARCHAR(200) NOT NULL DEFAULT '',
        updated_at DATETIME(6) NOT NULL,
        CONSTRAINT fk_memory_config_bot FOREIGN KEY (bot_id) REFERENCES bots(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    op.execute("""CREATE TABLE IF NOT EXISTS memory_states (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        bot_id BIGINT UNSIGNED NOT NULL,
        conversation_id BIGINT UNSIGNED NOT NULL,
        generation BIGINT NOT NULL DEFAULT 1,
        boundary_id BIGINT NOT NULL DEFAULT 0,
        summary TEXT NOT NULL,
        summary_through BIGINT NOT NULL DEFAULT 0,
        last_run_at DATETIME(6) NULL,
        checked_at DATETIME(6) NULL,
        next_run_at DATETIME(6) NULL,
        reason VARCHAR(200) NOT NULL DEFAULT '',
        UNIQUE KEY uq_memory_state (bot_id, conversation_id),
        CONSTRAINT fk_memory_state_bot FOREIGN KEY (bot_id) REFERENCES bots(id),
        CONSTRAINT fk_memory_state_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    op.execute("""CREATE TABLE IF NOT EXISTS memory_items (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        bot_id BIGINT UNSIGNED NOT NULL,
        conversation_id BIGINT UNSIGNED NOT NULL,
        scope VARCHAR(16) NOT NULL,
        group_id VARCHAR(191) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        subject_id VARCHAR(256) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        category VARCHAR(24) NOT NULL,
        fact_key VARCHAR(80) NOT NULL,
        content TEXT NOT NULL,
        content_hash CHAR(64) CHARACTER SET ascii NOT NULL,
        sources TEXT NOT NULL,
        origin VARCHAR(20) NOT NULL,
        owner_version BIGINT NOT NULL DEFAULT 0,
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        expires_at DATETIME(6) NULL,
        replaced_id BIGINT UNSIGNED NULL,
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        INDEX idx_memory_person (bot_id, conversation_id, status),
        INDEX idx_memory_group (bot_id, group_id, scope, status),
        INDEX idx_memory_hash (bot_id, content_hash),
        CONSTRAINT fk_memory_item_bot FOREIGN KEY (bot_id) REFERENCES bots(id),
        CONSTRAINT fk_memory_item_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    op.execute("""CREATE TABLE IF NOT EXISTS memory_runs (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        bot_id BIGINT UNSIGNED NOT NULL,
        conversation_id BIGINT UNSIGNED NOT NULL,
        generation BIGINT NOT NULL,
        config_version BIGINT NOT NULL,
        owner_version BIGINT NOT NULL,
        budget_date DATE NOT NULL,
        status VARCHAR(24) NOT NULL,
        source_ids TEXT NOT NULL,
        request_hash CHAR(64) CHARACTER SET ascii NOT NULL,
        provider_id BIGINT UNSIGNED NOT NULL,
        model_name VARCHAR(200) NOT NULL,
        reserved_tokens BIGINT NOT NULL,
        charged_tokens BIGINT NOT NULL,
        call_count INT NOT NULL DEFAULT 1,
        input_tokens BIGINT NULL,
        output_tokens BIGINT NULL,
        total_tokens BIGINT NULL,
        result MEDIUMTEXT NULL,
        error VARCHAR(200) NOT NULL DEFAULT '',
        created_at DATETIME(6) NOT NULL,
        finished_at DATETIME(6) NULL,
        INDEX idx_memory_budget (budget_date, bot_id),
        INDEX idx_memory_run_state (status, id),
        CONSTRAINT fk_memory_run_bot FOREIGN KEY (bot_id) REFERENCES bots(id),
        CONSTRAINT fk_memory_run_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    op.execute("""CREATE TABLE IF NOT EXISTS memory_sources (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        bot_id BIGINT UNSIGNED NOT NULL,
        conversation_id BIGINT UNSIGNED NOT NULL,
        record_id BIGINT UNSIGNED NOT NULL,
        generation BIGINT NOT NULL,
        config_version BIGINT NOT NULL,
        owner_version BIGINT NOT NULL,
        is_owner BOOLEAN NOT NULL DEFAULT FALSE,
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        position INT NOT NULL DEFAULT 0,
        run_id BIGINT UNSIGNED NULL,
        created_at DATETIME(6) NOT NULL,
        UNIQUE KEY uq_memory_source (bot_id, record_id),
        INDEX idx_memory_pending (status, bot_id, conversation_id),
        CONSTRAINT fk_memory_source_bot FOREIGN KEY (bot_id) REFERENCES bots(id),
        CONSTRAINT fk_memory_source_record FOREIGN KEY (record_id) REFERENCES chat_records(id),
        CONSTRAINT fk_memory_source_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")


def downgrade() -> None:
    raise CommandError("降级会丢失记忆和成本账本，请使用新迁移修正")
