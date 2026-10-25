# 数据库迁移

使用 Alembic + SQLAlchemy + PyMySQL，复用项目根目录 .env 的 MYSQL_HOST、MYSQL_PORT、MYSQL_DATABASE、MYSQL_USER、MYSQL_PASSWORD。alembic.ini 不保存凭证。所有命令在项目根目录执行；迁移只作用于配置的项目库，本项目使用 qbot。

## 初始化及升级

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
