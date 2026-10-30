# 存储、聊天记录与迁移定位

各表完整字段、主键、索引、外键与 Agent 修改规则见 [数据库表结构指南](database-schema.md)（原全库基线为 `0015_bot_roles`，新增备注表已核对至 `0016_contact_notes`）。本页侧重存储流程。

导航见 [文档索引](README.md)，操作命令、权限和回退规则见[数据库迁移](database-migrations.md)。

## 数据访问与表职责

[storage.py](../qbot/storage.py) 集中同步事务，异步方通过 Store.call 在线程池执行。SQLAlchemy + PyMySQL，InnoDB / utf8mb4，UTC 存储；定时任务转北京时间。启动反射表并检查必需字段，不自动迁移。

API Key 和 QQ AppSecret 用本地 PROVIDER_ENCRYPTION_KEY 加密，查询掩码。数据库连接与加密主密钥仍在本地环境。

| 表 | 职责 |
| --- | --- |
| model_providers | 模型、协议、类型、图片声明和加密密钥 |
| app_settings | 全局默认及备用聊天模型；旧 QQ 字段保留但不再运行读取 |
| bots | 多机器人凭证、启用、人设、绑定及多模态 JSON |
| conversations | 机器人、来源、群、用户范围及接收人名称 |
| chat_records | 问答、状态、模型快照、用量和媒体详情 |
| bot_contact_notes | bot_id + target_type + OpenID 的用户/群备注；角色独立 |
| bot_roles | 每个智能体独立的角色定义及关系说明 |
| bot_user_identities | bot_id + user_id 的身份，通过 role_id 关联角色 |
| interaction_greetings | 会话早午晚任务规则（end_time 仅兼容保留，不参与调度） |
| interaction_greeting_runs | 每日领取与发送记录关联 |
| admin_sessions | 登录摘要、CSRF、配置指纹及有效期 |
| voice_call_configs | 0023：机器人独立通话模型、音色及限制 |
| voice_calls | 0023：通话状态、模型快照、轮次文字、播放确认及聊天用量 |
| alembic_version | 当前迁移版本 |

初始四张业务表不代表当前完整结构；旧会话家庭字段保留核对，运行身份来源已迁到独立表。

## 隔离、状态与用量

接收入队前持久化，去重索引包含 bot_id 和消息来源、群范围。记录用于审计查询，不自动恢复模型历史和未完成队列。

状态包括 pending、processing、replied、rejected、expired、model_failed、send_failed、delivery_unknown，具体流转见 server.py、recording.py、proactive.py。不是每次请求都经过全部状态，送达未知不能直接重试。

供应商/模型快照保留调用时信息。media_details 保存转写、附件处理、视觉摘要、耗时、用量、回复方式和回退原因；不保存原图、原始音频、Base64 和临时签名地址。

聊天模型 input_tokens、output_tokens、total_tokens 单独保存，缺失 NULL，旧记录不估算。列表 token_usage 按筛选范围汇总，缺失不代表零费用；视觉/ASR/TTS 用量仍在媒体详情，不混作聊天模型 token。实现见 token_usage.py、chat.py、recording.py、Store.query_records。

## 迁移定位

### Token 可视化模块（已编写，待验证）

后台独立「Token 统计」菜单复用现有 token 字段，不新增数据库迁移。`GET /api/admin/token-stats` 按机器人、conversation_id、历史模型名及北京时间日期范围聚合，默认最近30天，最多366天；返回概览、每日已知总量，以及机器人/模型前10、对话前20排行。所有筛选同时生效，统计在数据库聚合，不拉取全部聊天内容。未知值仍为 NULL，不计作零；无记录日期与有记录但缺少用量的日期分别显示。

`GET /api/admin/token-stats/options` 提供机器人、历史模型及可搜索的会话选项，会话每页50条。对话按现有机器人＋来源＋群＋用户隔离，显示 ID 和范围，切换机器人清空会话选择。模型名来自记录快照，同名模型跨供应商合并。两接口沿用管理员鉴权。入口为 Store.token_statistics、Store.token_stat_options、admin.py 和 static/token-stats.js。统计范围沿用聊天模型，不扩展视觉/语音用量。

聊天记录页保留每条 token 及详情，将可视化汇总集中到独立菜单。未运行测试、浏览器或数据库验证。

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
| 0013_greeting_media | 定时投递 JSON 及上传图片 |
| 0014_manual_greeting_runs | 手动执行日期可空，自动每日去重保留 |
| 0015_bot_roles | 数据库角色目录与同机器人复合外键 |
| 0016_contact_notes | 独立用户/群备注及无冲突旧备注导入 |

上述为整理时工作区迁移，不代表目标库已执行。部署前核对 alembic current 与版本目录。仅新增手写迁移，不改已执行版本/快照，不 stamp、不删库删表初始化，复用现有 Docker MySQL，不操作其他库。

启动失败先查连接、权限、迁移及 Store 字段要求；/health 正常不能证明业务读写通过。本次未连库、迁移或测试。

迁移 `0013_greeting_media` 为 interaction_greetings 增加 delivery、image_data、image_filename。定时任务图片作为可重复发送的配置持久化，替换或删除才移除；这与聊天记录不保存原媒体的规则不同。聊天记录只保存发送各部分状态、图片文件名与摘要，不复制图片。

定时预设 1 复用 interaction_greetings.delivery 的 delivery_mode=preset_1，无新迁移；原图读取项目图片目录，不复制进每条任务。执行时生成的正文同步保存 chat_records.answer 与 interaction_greeting_runs.content，配图名称和摘要保存在 media_details；任务固定草稿保留。

预设 2 使用同一 delivery JSON（delivery_mode=preset_2），保存 tts_provider_id 与 voice；执行时的 AI 正文与预设 1 一样保存，media_details.parts 记录 audio / image 的发送状态及 TTS 处理信息。无新表或字段。

通用流程配置保存到 interaction_greetings.delivery.workflow（version、content_source、image_source、output、library_id），delivery_mode 仅作模板标识和旧数据转换。已保存 workflow 优先生效，执行时快照记录到 media_details.workflow。无新迁移；旧任务在读取/执行时从模板目录映射，保存后固定组合。

手动重复执行使用迁移 0014_manual_greeting_runs：interaction_greeting_runs.local_date 改为可空，NULL 表示手动执行，允许同任务多条手动记录；自动记录仍写日期并受 uq_greeting_day 约束。手动 chat_records.external_message_id 使用独立 UUID；日期显示由 created_at 换算北京时间。保留已有领取，不回填或删除历史。

## 数据库角色目录

`bot_roles` 按 bot_id 保存 role_key、label、relationship、member_name，(bot_id,role_key) 唯一。`bot_user_identities.role_id` 可空，通过 (bot_id,role_id) 外键确保引用同一个机器人；NULL 只用于未匹配旧身份，不能作为可信模型身份。原 family_role 保留兼容，写入时与 role_key 一起校验。迁移 `0015_bot_roles` 仅补充字段及匹配关联，保留原身份资料。角色编辑与绑定共用 bots 行锁，有绑定时拒绝删除或修改为冲突称呼。

2026-10-26 已在本地项目库迁移至 0015：四个初始角色、两条已匹配身份，零条待处理；核对原身份字段未变且复合外键存在。未运行测试或真实模型调用。

用户备注统一入口为 Store.save_contact，群备注为 Store.save_group_note；展示批量补充 user_remark/group_remark，原 OpenID 保留。迁移与冲突处理见[后台与模型](admin-and-models.md)。新备注不合并聊天历史、不合并不同 OpenID。

独立定时任务自 `0017_independent_tasks` 使用 task_key/task_name，同一会话可有多条同一时段任务；旧配置的 task_key 为空，保留其 ID 和每日领取记录。配置唯一索引包含 task_key，自动去重仍按 greeting_id 和日期，详见定时任务与数据库表结构文档。

自 0018_schedule_timing，定时任务以 schedule_updated_at 防止保存即触发，只领取当前分钟到期任务，取消当天错过时间的补发。扫描与领取双重校验；手动执行仍独立。

## 对话页面读取范围

Store.send_targets 私聊按机器人、来源和 user_id 分组，群聊按机器人、来源和 group_id 分组，支持备注或 OpenID 包含搜索。Store.conversation_messages 从已归属记录解析目标：私聊只读同机器人同用户的 qq_c2c 记录，群聊只读同机器人同群的 qq_group 记录，群成员的原会话仍独立存储。每页40条记录，以 id 向前游标分页，返回按 ID 升序的问答及状态。服务端将 external_message_id 前缀转为 incoming/manual/scheduled 来源，不把原始外部 ID 和完整媒体诊断返回页面。仅聚合展示，不合并模型上下文、不新增数据库表。

## 昵称与语音文本（已实现，真实消息待验收）

新增迁移 0019_user_nickname，在 conversations 单独保存 platform_nickname；从 QQ 消息 author.username 接收非空昵称，同机器人同 OpenID 同步，空值不覆盖已有昵称，不改备注或身份权限。对话显示备注优先，其次平台昵称、绑定名称和 ID；昵称可搜索。旧记录不猜测昵称，等待新消息。

ASR 继续保存 media_details.attachments[].transcript；自动/定时 TTS 增加 tts_text，记录实际送入合成的文本。手动 TTS 随音频上传保存文本快照，纳入同请求内容校验，不重复合成。对话气泡显示“语音消息：文本”，识别失败或纯上传音频没有文本时明确提示；音频文件不落库、不接 OSS、不提供历史播放。旧记录仅展示已有转写/回复正文，不重新调用模型。

2026-10-26 已完成 Python AST、JavaScript 语法与差异静态检查；本地已迁移至 0019_user_nickname 并重启。未运行测试、浏览器、付费 ASR/TTS 或真实发送验证；昵称取决于平台实际推送。

## 唯一主人（已编写，待验证）

迁移 0020_identity_owner 增加身份主人标记及选择时间。Store.set_owner 使用机器人行锁和唯一索引保证每个机器人最多一位主人；解绑取消，普通身份编辑保留。Store.owner_context 仅补充同群、认主后、最近30分钟内最多5条已回复主人发言，每条截取800字，不合并会话历史或读取私聊、其他群。部署需显式迁移；本地执行状态见下方记录。

2026-10-28：认主相关代码已编写，Python/JavaScript 语法与差异静态检查通过；未运行测试、浏览器或真实模型验收。

## 定时模板快照（已编写，待验证）

新增 scheduled_templates 保存管理员全局可复用消息模板；读取列表不加载图片。套用时客户端取得完整详情，将图片、文案及工作流一同保存到任务，执行器无需再次读取模板，删除或修改模板不影响已保存任务。新迁移0021，未执行。

2026-10-28：完成 Python AST、JavaScript 语法、HTML ID/脚本资源及差异静态检查；未运行测试、浏览器、数据库迁移或模型/TTS/QQ 调用。需显式升级数据库、重启后端并刷新后台后使用。

### 备用模型记录（0022，已编写待联调）

app_settings.fallback_provider_id 保存全局备用供应商。普通聊天切换后，chat_records 的供应商及模型快照指向备用；Token 字段只保存最终返回模型的用量，不把主模型失败或空白产生的费用归给备用，不能代替供应商账单。QQ 诊断 media_details 保存 fallback_provider_id；日志只记录模型 ID 和异常类型，不保存原始异常响应或密钥。无新增聊天记录字段。

## 聊天阶段耗时（已编写，待实际消息验收）

QQ 被动聊天复用 media_details 保存 timings：队列等待、处理总计（不含排队）、总耗时（从入队开始，含排队及收尾）、附件预处理、聊天模型、语音准备、媒体名额等待、TTS合成、私聊转码、上传及QQ发送；附件独立记录下载、解码、ASR时间。单调时钟计时，失败和取消也保留已耗时间，未执行阶段不填零。ASR包括同步请求或Filetrans完整流程；模型耗时包括备用切换，不代表首字延迟；QQ发送仅指平台接口确认，不等于用户开始播放。各层汇总包含子阶段，不可直接相加。

后台「聊天记录→查看」显示中文耗时表，兼容历史已有模型、视觉及语音汇总字段，缺失显示未记录。计时不改变超时、重试或回复策略，不调用额外模型，无新迁移或依赖。不新增或运行测试，不打开浏览器；本次不自动部署。

2026-10-30：已完成 Python AST、JavaScript 语法及差异静态检查；未运行测试、浏览器或真实模型调用。需部署后重启后端、刷新后台，新消息才会产生完整阶段数据。
