-- qBot 初始表结构，要求 MySQL 8.0.16+（使用 CHECK 约束），推荐 MySQL 8.4。
-- 请先在客户端选中你新建的项目数据库，再执行整个文件。
-- 不创建数据库，不创建账号，不删除数据，不修改已有表结构。
-- IF NOT EXISTS 仅便于重复初始化，不代替后续版本迁移。
-- 所有应用数据库连接也需设置 UTC；DATETIME 字段约定存储 UTC。
SET NAMES utf8mb4;
SET SESSION time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS model_providers (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '供应商配置 ID',
    name VARCHAR(100) NOT NULL COMMENT '后台显示名称',
    api_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'deepseek 或 openai_compatible',
    base_url VARCHAR(2048) NOT NULL COMMENT 'HTTPS API 基地址，不含凭证',
    model_name VARCHAR(200) NOT NULL COMMENT '模型标识',
    api_key_encrypted TEXT NULL COMMENT '应用加密后的 API Key；禁止保存明文',
    enabled TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '0 停用，1 启用',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间 UTC',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间 UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uq_model_providers_name (name),
    CONSTRAINT ck_provider_api_type CHECK (api_type IN ('deepseek', 'openai_compatible')),
    CONSTRAINT ck_provider_enabled CHECK (enabled IN (0, 1)),
    CONSTRAINT ck_provider_key CHECK (
        enabled = 0 OR (api_key_encrypted IS NOT NULL AND CHAR_LENGTH(api_key_encrypted) > 0)
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='模型供应商配置；加密主密钥保存在环境变量中';

CREATE TABLE IF NOT EXISTS app_settings (
    id TINYINT UNSIGNED NOT NULL COMMENT '固定为 1，仅保留一行配置',
    default_provider_id BIGINT UNSIGNED NULL COMMENT '默认供应商；NULL 表示使用环境配置',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间 UTC',
    PRIMARY KEY (id),
    KEY idx_settings_provider (default_provider_id),
    CONSTRAINT ck_app_settings_singleton CHECK (id = 1),
    CONSTRAINT fk_settings_provider FOREIGN KEY (default_provider_id)
        REFERENCES model_providers (id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='单行运行配置；默认供应商必须在应用事务中校验为已启用';

CREATE TABLE IF NOT EXISTS conversations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '会话 ID',
    source VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'qq_group 或 local',
    group_id VARCHAR(256) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'QQ 群 OpenID；本地聊天固定为 local',
    user_id VARCHAR(256) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'QQ 成员 OpenID；本地聊天使用独立用户标识',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间 UTC',
    last_active_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近消息时间 UTC，由应用更新',
    PRIMARY KEY (id),
    UNIQUE KEY uq_conversation_identity (source, group_id, user_id),
    UNIQUE KEY uq_conversation_scope (id, source, group_id),
    KEY idx_conversation_user (source, user_id, last_active_at),
    KEY idx_conversation_activity (last_active_at, id),
    CONSTRAINT ck_conversation_source CHECK (source IN ('qq_group', 'local')),
    CONSTRAINT ck_conversation_identity CHECK (CHAR_LENGTH(group_id) > 0 AND CHAR_LENGTH(user_id) > 0)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='按来源、群和用户隔离；历史记录与运行时上下文分离';

CREATE TABLE IF NOT EXISTS chat_records (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '聊天记录 ID',
    conversation_id BIGINT UNSIGNED NOT NULL COMMENT '所属会话',
    source VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '与所属会话来源一致',
    group_id VARCHAR(256) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '与所属会话群标识一致',
    external_message_id VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'QQ 消息 ID；本地消息由应用生成 UUID；区分大小写',
    record_type VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'chat'
        COMMENT 'chat 普通聊天，reset 清空上下文指令',
    question MEDIUMTEXT NOT NULL COMMENT '收到的文本；消息长度仍由应用限制',
    answer MEDIUMTEXT NULL COMMENT '生成的回复；是否送达以 status 为准',
    status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'pending'
        COMMENT 'pending/processing/replied/model_failed/send_failed/delivery_unknown/expired/rejected',
    error_message VARCHAR(1000) NULL COMMENT '经过脱敏的错误说明，不存密钥、原始异常或堆栈',
    provider_id BIGINT UNSIGNED NULL COMMENT '实际使用的供应商 ID；环境配置时为 NULL',
    provider_name_snapshot VARCHAR(100) NULL COMMENT '请求开始时供应商名称快照',
    model_name_snapshot VARCHAR(200) NULL COMMENT '请求开始时模型名称快照',
    message_sent_at DATETIME(6) NULL COMMENT '平台消息发送时间，转换为 UTC',
    received_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '本服务接收时间 UTC',
    completed_at DATETIME(6) NULL COMMENT '处理终止时间 UTC；不等于确认送达时间',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '记录更新时间 UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uq_chat_external_message (source, group_id, external_message_id),
    KEY idx_chat_conversation_time (conversation_id, received_at, id),
    KEY idx_chat_conversation_scope (conversation_id, source, group_id),
    KEY idx_chat_received (received_at, id),
    KEY idx_chat_status_time (status, received_at, id),
    KEY idx_chat_group_time (source, group_id, received_at, id),
    KEY idx_chat_provider (provider_id),
    CONSTRAINT fk_chat_conversation FOREIGN KEY (conversation_id, source, group_id)
        REFERENCES conversations (id, source, group_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_chat_provider FOREIGN KEY (provider_id)
        REFERENCES model_providers (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_chat_message_id CHECK (CHAR_LENGTH(external_message_id) > 0),
    CONSTRAINT ck_chat_record_type CHECK (record_type IN ('chat', 'reset')),
    CONSTRAINT ck_chat_status CHECK (status IN (
        'pending', 'processing', 'replied', 'model_failed',
        'send_failed', 'delivery_unknown', 'expired', 'rejected'
    ))
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='聊天记录与处理结果；reset 不删除历史，不自动恢复上下文';

-- 初始化单行设置；重复执行保留已经选择的默认供应商。
INSERT INTO app_settings (id, default_provider_id)
VALUES (1, NULL)
ON DUPLICATE KEY UPDATE id = 1;

-- 不写入供应商密钥或管理员密码，也不创建演示聊天记录。
