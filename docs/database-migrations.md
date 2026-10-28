# 数据库迁移

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
