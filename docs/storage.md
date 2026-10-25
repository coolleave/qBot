# 存储、聊天记录与迁移定位

导航见 [文档索引](README.md)，操作命令、权限和回退规则见[数据库迁移](database-migrations.md)。

## 数据访问与表职责

[storage.py](../qbot/storage.py) 集中同步事务，异步方通过 Store.call 在线程池执行。SQLAlchemy + PyMySQL，InnoDB / utf8mb4，UTC 存储；定时任务转北京时间。启动反射表并检查必需字段，不自动迁移。

API Key 和 QQ AppSecret 用本地 PROVIDER_ENCRYPTION_KEY 加密，查询掩码。数据库连接与加密主密钥仍在本地环境。

| 表 | 职责 |
| --- | --- |
| model_providers | 模型、协议、类型、图片声明和加密密钥 |
| app_settings | 全局默认；旧 QQ 字段保留但不再运行读取 |
| bots | 多机器人凭证、启用、人设、绑定及多模态 JSON |
| conversations | 机器人、来源、群、用户范围及接收人名称 |
| chat_records | 问答、状态、模型快照、用量和媒体详情 |
| bot_user_identities | bot_id + user_id 的家庭身份 |
| interaction_greetings | 会话早晚任务规则 |
| interaction_greeting_runs | 每日领取与发送记录关联 |
| admin_sessions | 登录摘要、CSRF、配置指纹及有效期 |
| alembic_version | 当前迁移版本 |

初始四张业务表不代表当前完整结构；旧会话家庭字段保留核对，运行身份来源已迁到独立表。

## 隔离、状态与用量

接收入队前持久化，去重索引包含 bot_id 和消息来源、群范围。记录用于审计查询，不自动恢复模型历史和未完成队列。

状态包括 pending、processing、replied、rejected、expired、model_failed、send_failed、delivery_unknown，具体流转见 server.py、recording.py、proactive.py。不是每次请求都经过全部状态，送达未知不能直接重试。

供应商/模型快照保留调用时信息。media_details 保存转写、附件处理、视觉摘要、耗时、用量、回复方式和回退原因；不保存原图、原始音频、Base64 和临时签名地址。

聊天模型 input_tokens、output_tokens、total_tokens 单独保存，缺失 NULL，旧记录不估算。列表 token_usage 按筛选范围汇总，缺失不代表零费用；视觉/ASR/TTS 用量仍在媒体详情，不混作聊天模型 token。实现见 token_usage.py、chat.py、recording.py、Store.query_records。

## 迁移定位

| 版本 | 变化 |
| --- | --- |
| 0001_initial | 初始供应商、配置、会话和记录 |
| 0002_qq_settings / 0003_qq_transport | 历史 QQ 凭证和接入方式 |
| 0004_multiple_bots | 多机器人及隔离索引 |
| 0005_family_binding | 历史会话身份字段 |
| 0006_interaction_greetings | 问候规则、每日执行 |
| 0007_recipient_names | 接收人名称和备注 |
| 0008_model_types | 模型类型 |
| 0009_bot_user_identity | 独立机器人用户身份 |
| 0010_multimodal | 多模态、图片声明及处理详情 |
| 0011_admin_sessions | 持久登录 |
| 0012_token_usage | 聊天模型 token 字段 |

上述为整理时工作区迁移，不代表目标库已执行。部署前核对 alembic current 与版本目录。仅新增手写迁移，不改已执行版本/快照，不 stamp、不删库删表初始化，复用现有 Docker MySQL，不操作其他库。

启动失败先查连接、权限、迁移及 Store 字段要求；/health 正常不能证明业务读写通过。本次未连库、迁移或测试。
