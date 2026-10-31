# 数据库迁移

## 持久化记忆（0024，已编写未执行）

`0024_memory` 接续 `0023_voice_calls`，使用可重复执行的建表语句新增 `bot_memory_settings`、`memory_states`、`memory_items`、`memory_sources`、`memory_runs`，不修改既有表数据或自动回填历史。MySQL 5.6/utf8mb4兼容，JSON采用TEXT，来源去重键包含bot_id。应用启动反射五张新表，因此需先停止服务、备份、显式执行 `python -m alembic upgrade head` 后重启；该命令包含所有尚未执行的前置迁移。降级明确拒绝丢失记忆和账本。本轮未执行迁移、测试或数据库验收，详见[记忆系统](memory-system.md)。

## 网页语音通话（0023，已编写未执行）

新增 `0023_voice_calls`，依赖 `0022_fallback_provider`，创建 `voice_call_configs` 和 `voice_calls`；不修改已有消息数据或供应商配置。配置按 bot_id 唯一，聊天/实时识别/TTS 供应商引用均有外键；记录按 bot_id 和开始时间索引。现有 model_type 为 VARCHAR(16)，可保存 asr_realtime，无需改列。新代码启动反射两张表，需先停服务、备份并显式升级后重启。本次未执行迁移，降级拒绝丢弃记录。详见[语音通话](voice-calls.md)。

使用 Alembic + SQLAlchemy + PyMySQL，复用项目根目录 .env 的 MYSQL_HOST、MYSQL_PORT、MYSQL_DATABASE、MYSQL_USER、MYSQL_PASSWORD。alembic.ini 不保存凭证。所有命令在项目根目录执行；迁移只作用于配置的项目库，本项目使用 qbot。

表结构、字段语义与 Agent 排查入口见 [数据库表结构指南](database-schema.md)。

## 初始化及升级

Docker 部署使用 `docker compose run --rm --no-deps qbot python -m alembic upgrade head`，版本查询将 `upgrade head` 替换为 `current`。先停止旧应用并备份，迁移成功后才启动新容器；启动不会自动迁移，完整步骤见 [Docker 部署](docker-deployment.md)。容器使用 Compose 注入的 MYSQL_* 环境变量，不需要将 `.env` 打入镜像。

```powershell
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m alembic current
.\.venv\Scripts\python -m alembic history
```

新库执行初始版本时建立四张业务表和版本表，升级到 head 还会应用后续结构变化；当前表职责与版本定位见 [存储与记录](storage.md)。旧 SQL 初始化的库会先核对字段、索引、外键及排序规则，匹配才接管；部分建表或结构不符会停止，先核对差异，不能直接 stamp。已有旧版 256 字符群字段不会被自动缩短。

MySQL 5.6.5+ 使用 InnoDB / utf8mb4；不依赖 CHECK。初始快照的群标识限定 191 个 ASCII 字符，兼容旧版索引长度。当前实际验证版本为 5.6.51。

运行需要 SELECT、INSERT、UPDATE 等业务权限；迁移另需 CREATE、ALTER、INDEX、REFERENCES，仅授予 qbot.*。当前初始化不需要 DROP；不操作其他库，不创建数据库或账号。未来迁移若涉及删除对象，应单独评估权限及数据影响。

## 新增结构变更

```powershell
.\.venv\Scripts\python -m alembic revision -m "新增字段说明"
```

编辑生成版本的 upgrade / downgrade，填写实际 SQLAlchemy 操作，再审阅并执行升级。当前应用反射数据库，没有声明式目标 metadata，因此禁止 --autogenerate。已执行的迁移和 migrations/snapshots 内快照不可改动，使用新版本修正。

可先生成 SQL 供审阅（离线 SQL 不执行旧库结构校验）：

```powershell
.\.venv\Scripts\python -m alembic upgrade head --sql
```

MySQL DDL 会隐式提交，迁移失败可能留下已建表或已修改的结构；不能假定事务会回滚所有 DDL。先备份数据并审阅迁移，失败后核对实际结构再处理，不删表重试。

初始版本明确拒绝 downgrade base，避免删除聊天记录和供应商配置。后续版本只有实现并审阅对应 downgrade 后才可回退；常规修正优先新增升级版本。应用启动不自动执行迁移。

## 多机器人版本

0004_multiple_bots 创建 bots 表，将旧配置及历史归属机器人 1，替换消息和会话唯一索引，新增机器人范围外键。保留旧 QQ 配置列但不再用于运行。不删除业务记录，不支持降级合并多机器人历史。升级前停止旧服务，升级后运行新代码。

## 数据库角色目录

0015_bot_roles 新增角色表和身份的可空 role_id，使用复合外键隔离机器人。初始目录冻结于 migrations/snapshots/0015_identity_roles.json；已匹配身份回填关联，不匹配身份保留待处理。不会删除原有用户身份或创建 QQ 用户绑定。应用启动不自动迁移。

2026-10-26 本地已应用 0015；升级前原身份表备份保存在本机临时目录 qbot-role-backup-ba3cqxcp/identities-before-0015.json。核对旧身份字段未变、两条关联完成和复合外键存在，未运行测试。

## 通讯录备注

`0016_contact_notes` 新增 bot_contact_notes，并导入同机器人、同 OpenID 的无冲突旧用户备注，回填兼容会话字段；有冲突的用户保留旧会话值。群备注从空开始，不改身份、聊天记录或任务启用状态。升级前备份 conversations 和版本信息；应用不自动迁移，部署前手动升级。

2026-10-26 备注功能开发期间，版本检查发现并行进程已升级到 0016，本轮未再次迁移。已核对备注表主键、外键和兼容字段一致性，并保存迁移后现状快照；本轮未获得迁移前备份。服务 8092 健康接口正常。

迁移执行记录：2026-10-26 重启服务时从 0015 显式升级至 0016。升级前 conversations（14 条）及 alembic_version 已备份到本机临时目录 `qbot-before-0016-2me0gwy2`；升级成功后重新启动服务，健康接口返回正常。上述并行核对使用的是迁移后快照。

## 多条独立定时任务

`0017_independent_tasks` 扩展 interaction_greetings 的任务标识、名称及配置唯一索引，保留旧任务 ID、配置和执行历史。升级前停止服务并备份 interaction_greetings、interaction_greeting_runs 和版本信息，然后显式执行 `python -m alembic upgrade head`。不支持将多个任务降级合并为旧三时段配置。

2026-10-26 已备份到本机临时目录 `qbot-before-0017-nb96g0cx` 并显式升级至 `0017_independent_tasks`。升级后逐字段核对原 6 条任务和 11 条执行记录保持不变，配置唯一索引已包含 task_key。后端已重启，健康接口正常。已完成 Python AST、JavaScript 语法和差异静态检查；未运行测试、浏览器验证、模型调用或主动发送验收。

## 定时保存时刻

0018_schedule_timing 为 interaction_greetings 增加非空 DATETIME(6) schedule_updated_at（UTC），旧任务初始化为迁移时刻，不改其他字段及执行历史。新保存由应用更新此字段；调度扫描和领取均要求配置早于当前分钟开始、发送时间等于当前分钟，取消过期补发。迁移前停服务并备份任务及历史，再显式迁移、启动。

2026-10-26 已备份到本机临时目录 `qbot-before-0018-1tjql7xl`，显式迁移至 `0018_schedule_timing`，核对原 8 条任务及 14 条执行记录的既有字段全部保留，新增保存时刻非空。后端已重启，健康接口正常；Python AST、JavaScript 语法、HTML ID 唯一性及差异静态检查通过。未运行测试、打开浏览器或手动执行发送验证，自动调度效果待实际到点验收。

## 平台昵称

0019_user_nickname 仅为 conversations 新增可空 platform_nickname VARCHAR(256)，不修改既有备注或身份。部署先停服务、备份 conversations 及版本，再显式升级并启动。语音文本复用 media_details，无额外表。迁移实现已编写，执行状态见后续记录。

2026-10-26 已停服务并备份 conversations 和版本到本机临时目录 `qbot-before-0019-9jah0_u8`，显式从 0018 升级至 0019，逐字段核对原 14 条会话完整保留，新昵称字段初始为空。已重启项目服务。

## 唯一主人（已编写，待验证）

0020_identity_owner 为身份表增加 owner_slot、owner_selected_at 与机器人范围唯一索引；保留所有原身份，默认没有主人。升级前停止服务并备份 bot_user_identities 与版本，再显式执行 `python -m alembic upgrade head`。应用不自动迁移，不使用 stamp；本地执行状态见下方记录。

2026-10-28：认主相关代码已编写，Python/JavaScript 语法与差异静态检查通过；未运行测试、浏览器或真实模型验收。

2026-10-28 已将本地项目库从 0019 显式升级至 0020_identity_owner。升级前未发现本机运行中的 qBot 服务；原 4 条身份、表结构及版本已备份到本机临时目录 `qbot-before-0020-0hap63ti/identity-backup.json`。升级后逐字段核对原身份保留、新主人字段均为空、唯一索引正确，Store 初始化正常。未替用户选择主人。

迁移后已使用原命令 `python -m qbot serve` 启动本地服务，8092 的 `/health` 返回 `status=ok`；此检查仅确认 HTTP 服务可用，不代表 QQ 连接或模型回复验收通过。

## 定时模板管理（已编写，待验证）

0021_scheduled_templates 仅创建全局模板表，不修改已有任务或配置文件内置模板。部署先停止服务、备份项目库及版本，显式执行 `python -m alembic upgrade head` 后启动。此次不自动执行迁移。

2026-10-28：完成 Python AST、JavaScript 语法、HTML ID/脚本资源及差异静态检查；未运行测试、浏览器、数据库迁移或模型/TTS/QQ 调用。需显式升级数据库、重启后端并刷新后台后使用。

2026-10-28 已按用户确认，本地与实验室服务器测试环境共用同一数据库，仅执行一次 `alembic upgrade 0021_scheduled_templates`。执行前版本为0020，模板表不存在；12张既有表结构和数据已备份到本机临时目录 `qbot-before-0021-nfsjxddr`（每表JSON，二进制以Base64保存，manifest.json记录行数）。执行后确认版本0021、模板表7个字段正确、初始0条模板及Store初始化正常。服务器新容器内 `alembic current` 同样返回0021 (head)，未重复执行迁移；未运行测试或真实发送。

## 0022 备用聊天模型（已编写，未执行）

`0022_fallback_provider` 接续 `0021_scheduled_templates`，为 app_settings 增加可空 BIGINT UNSIGNED 的 fallback_provider_id、索引和指向 model_providers 的外键；已有行保持 NULL。默认与备用的互斥、启用状态及类型校验由应用在同一设置行锁下完成。不修改历史迁移，不自动选择模型。先停止服务并备份项目库，再执行 `python -m alembic upgrade head`，成功后重启服务并刷新后台；运行账号需要读取新字段，迁移账号需要 ALTER、INDEX、REFERENCES 权限。本次未运行数据库迁移或测试。

## 2026-10-30 备用模型部署记录

实验室服务器已部署功能提交 `5552181`。持有 `.deploy/deploy.lock`，构建新镜像后停服；生产环境配置和项目库的13张表、438行数据备份于服务器 `.deploy/backups/before-0022-20261030-151756/`，文件仅部署用户可读，数据库备份为包含表结构及类型标记的 JSON，并保存 SHA-256。

显式升级 `0021_scheduled_templates` 至 `0022_fallback_provider` 后，逐表比较原数据一致（除版本表及新增字段），fallback_provider_id 初始为 NULL，外键、索引和 Store 初始化正常。新容器 running healthy、重启次数0，/health与/admin均返回200；recording.py、storage.py及admin.js与服务器仓库SHA-256一致。未运行测试、浏览器或真实模型调用，未替用户选择备用供应商。更新自动部署基线后继续沿用原定时更新。

## 2026-10-30 共享远程库升级记录

按用户授权，共享远程 qbot 库仅升级一次：0022_fallback_provider → 0023_voice_calls → 0024_memory。升级前取得服务器部署锁并停止 qbot-prod 应用容器，确认本机没有 qBot 服务。停服后的13张表、464行数据及表结构备份于维护机临时目录 `qbot-stopped-before-0024-lnfz6ln6`，每表JSON保留类型标记，manifest记录SHA-256，另保留迁移源码快照。

显式升级后逐表核对原12张业务表结构和数据不变（版本表按预期更新）；新增7张表为空，字段、索引、外键可读取，当前工作区Store初始化正常。未运行测试、浏览器或付费接口。此操作只更新数据库；服务器仍使用原8386877应用版本，语音通话代码保留在独立codex/voice-calls分支，未合并部署。

数据库版本已超出服务器旧代码和语音独立分支的迁移头；后续Alembic维护需使用包含0024_memory的完整迁移链，不使用stamp或降级规避版本差异。

服务恢复确认：原qbot-prod容器running healthy，重启次数0；容器内查询共享库为0024_memory，/health和/admin均返回HTTP 200。部署锁随后释放，未改动自动部署成功基线。健康结果仅证明服务可用，不代表真实语音或记忆功能验收。

## 实时通话与网页记忆（0025，已编写未执行）

`0025_realtime_calls` 接续 `0024_memory`，向 `voice_call_configs` 增加 `mode`（旧行默认 pipeline）、`realtime_provider_id` 外键和 `realtime_voice`；新增 `voice_memories`。`model_type` 原 VARCHAR(16) 足以保存 realtime。启动会反射新表，需先停应用、备份项目库、使用含完整0024链的代码显式 `python -m alembic upgrade head`，再启动新代码。不修改已执行迁移，不 stamp，不自动迁移；本轮尚未执行升级。

本轮静态检查：修改涉及的9个Python文件AST解析、2个JavaScript文件语法检查、通话DOM引用/ID和 `git diff --check` 通过。未运行测试、数据库迁移、浏览器或付费通话；运行效果待验收。
