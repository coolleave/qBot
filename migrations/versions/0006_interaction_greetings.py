"""首次互动问候配置与按日去重记录。"""
from alembic import op
from alembic.util import CommandError

revision = "0006_interaction_greetings"
down_revision = "0005_family_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE interaction_greetings (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        bot_id BIGINT UNSIGNED NOT NULL,
        conversation_id BIGINT UNSIGNED NOT NULL,
        slot VARCHAR(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        enabled TINYINT UNSIGNED NOT NULL DEFAULT 0,
        start_time CHAR(5) CHARACTER SET ascii NOT NULL,
        end_time CHAR(5) CHARACTER SET ascii NOT NULL,
        weekdays VARCHAR(13) CHARACTER SET ascii NOT NULL,
        content VARCHAR(200) NOT NULL,
        UNIQUE KEY uq_greeting_scope (bot_id,conversation_id,slot),
        UNIQUE KEY uq_greeting_bot (id,bot_id),
        CONSTRAINT fk_greeting_bot FOREIGN KEY (bot_id) REFERENCES bots(id),
        CONSTRAINT fk_greeting_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    op.execute("""CREATE TABLE interaction_greeting_runs (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        bot_id BIGINT UNSIGNED NOT NULL,
        greeting_id BIGINT UNSIGNED NOT NULL,
        local_date DATE NOT NULL,
        record_id BIGINT UNSIGNED NOT NULL,
        content VARCHAR(200) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        UNIQUE KEY uq_greeting_day (bot_id,greeting_id,local_date),
        CONSTRAINT fk_greeting_run_rule FOREIGN KEY (greeting_id,bot_id) REFERENCES interaction_greetings(id,bot_id),
        CONSTRAINT fk_greeting_run_record FOREIGN KEY (record_id) REFERENCES chat_records(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")


def downgrade() -> None:
    raise CommandError("降级会丢失问候设置和记录，请使用新迁移修正")
