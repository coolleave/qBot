# qBot 对话智能体

使用 Python + LangChain + DeepSeek 开发的个人 QQ 对话机器人。默认通过 WebSocket 接收 QQ 消息，保留 Webhook 可选；FastAPI 提供轻量管理后台。启用后台后，通过 MySQL 保存聊天记录与供应商配置；模型上下文仍保存在内存，单进程运行，每个机器人独立队列按顺序处理消息。

## 当前进度

- [x] 项目骨架、环境变量配置、独立业务提示词
- [x] 基础对话（LangChain + DeepSeek 适配，模拟响应验证通过）
- [x] 多轮对话（隔离、过期、裁剪、清空及顺序处理离线验证通过）
- [ ] 真实 DeepSeek API 联调（需要本地配置有效密钥）
- [ ] QQ 消息接收与回复验收（代码和模拟回调已验证，尚未真实平台联调）
- [x] 后台管理页面与管理员登录（接口离线验证通过，按约定未做浏览器验证）
- [x] 供应商、模型与 API 密钥管理（加密、掩码及切换离线验证通过）
- [x] 聊天记录存储与查询代码（隔离数据库验证通过）
- [x] MySQL 5.6.51 连接、Alembic 建表与重复迁移验证
- [ ] 真实 MySQL 业务读写端到端联调
- [ ] 工具调用（后续版本）
- [ ] 重启后恢复对话上下文（后续版本，与聊天记录查询分开实现）

完成并验证后再勾选；离线验证不代表真实平台验收通过。

## 如何运行

推荐 Python 3.13，本次验证版本为 Windows / Python 3.13.9。以下 PowerShell 命令在项目根目录执行，无需激活虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c constraints.txt -e .
# 新机器手动创建 .env，按下方配置表填写（已有 .env 不要覆盖）
```

不要覆盖已有 `.env` 或密钥。启用后台时，先在「模型供应商」添加 DeepSeek、填写 API Key、启用并设为默认，随后启动本地对话（无后台模式才读取 `.env` 的 `DEEPSEEK_API_KEY`）：

```powershell
.\.venv\Scripts\python -m qbot chat
```

`/reset` 清空当前会话，`/exit` 退出本地对话。实际聊天会调用付费 API；启动或检查健康状态不会调用模型。

接入 QQ 时，在后台「机器人管理」填写 AppID 和 AppSecret，保存后自动重新连接对应机器人：

```powershell
.\.venv\Scripts\python -m qbot serve
```

默认监听 `127.0.0.1:8080`，访问 `/admin` 管理配置，`/health` 仅表示 HTTP 进程正常，不代表 QQ 已连接。仅运行一个进程，不启用多 worker。

### QQ 接入（默认 WebSocket，无需公网 IP）

1. 在 QQ 开放平台「事件订阅与回调地址」选择 WebSocket，并配置机器人服务范围或内部体验号码，将机器人添加到有权限的群。
2. 在本项目后台「机器人管理」填写 AppID、AppSecret，接入方式选择 WebSocket，保存后自动重新连接对应机器人。
3. 在「模型供应商」添加模型 API Key、启用并设为默认。
4. 在「机器人管理」点击刷新连接状态，连接成功后在群里 @机器人发送文字。

程序主动连接官方 WSS 网关，订阅 `GROUP_AND_C2C_EVENT (1 << 25)`，处理其中的 `GROUP_AT_MESSAGE_CREATE` 群聊事件和 `C2C_MESSAGE_CREATE` 私聊事件。回复仍通过 QQ HTTP API 发送。本机无需域名、公网 IP 或端口转发，但需要能访问 QQ 和模型服务并持续运行。

连接具有心跳、确认超时检测、短线 Resume 和指数退避重连；连续失败 10 次停止重连，修复配置后重启。当前仅单分片，恢复会话只在内存保存，进程重启后重新登录，不能恢复丢失的运行队列。限流消息记录日志后跳过，队列满或数据库失败会断线尝试恢复，超过平台保留窗口不保证补发。

### 可选 Webhook

在本项目后台及 QQ 开放平台同时选择 Webhook，保存后自动重新连接对应机器人。配置公网 HTTPS 地址 `https://你的域名/qq/webhook/{bot_id}`，通过反向代理转发到 `http://127.0.0.1:8080/qq/webhook/{bot_id}`，订阅 `GROUP_AT_MESSAGE_CREATE` 和 `C2C_MESSAGE_CREATE`。此模式需要公网入口。回调验签允许时间偏差 5 分钟，请保持服务器时钟同步。WebSocket 模式拒绝 Webhook 请求，避免同时接入。

## 配置

配置定义见 `qbot/config.py`，环境变量优先于当前工作目录下的 `.env`。真实密钥不提交到 Git。

| 配置 | 默认值 / 说明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 仅无后台模式使用；后台模式在模型供应商页面填写 |
| `DEEPSEEK_MODEL` | `deepseek-flash`，可修改为账号可用模型 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `MODEL_TIMEOUT_SECONDS` / `MODEL_MAX_RETRIES` | 单次请求超时 45 秒，最多重试 1 次 |
| `CHAT_TIMEOUT_SECONDS` / `MODEL_MAX_TOKENS` | 整次生成总时限 90 秒，最多输出 1024 token |
| `QQ_APP_ID` / `QQ_APP_SECRET` | 仅无后台模式使用；后台模式在 QQ 配置页面填写 |
| `QQ_API_BASE_URL` / `QQ_TOKEN_URL` | 官方生产地址，见 qbot/config.py |
| `QQ_TIMEOUT_SECONDS` | QQ HTTP 请求超时 10 秒；后台单次回复另有 25 秒总时限 |
| `HOST` / `PORT` | `127.0.0.1` / `8080` |
| `SESSION_TTL_SECONDS` / `SESSION_MAX_TURNS` | 闲置 1800 秒过期，保存最近 10 轮 |
| `SESSION_MAX_COUNT` | 最多 1000 个会话，达到上限淘汰最久未使用的会话 |
| `MAX_INPUT_CHARS` / `MAX_REPLY_CHARS` | 输入上限 4000 字符，回复超过 1500 字符截断 |
| `RATE_LIMIT_PER_MINUTE` / `GLOBAL_RATE_LIMIT_PER_MINUTE` | 每个群内用户每分钟最多 6 条，全局最多 30 条 |
| `QUEUE_MAX_SIZE` | 最多 32 条待处理消息 |

默认关闭 DeepSeek 深度思考并一次性回复文本。会话按“群 + 用户”隔离，模型上下文仅在内存保存，重启清空；启用后台后聊天记录单独保存至 MySQL。`/reset` 仅清空发起者在当前群的会话。无文字的消息提示当前仅支持文本，附件不会传给模型。

## 后台与 MySQL

复用已有 Docker MySQL，兼容 MySQL 5.6.5+（本次验证为 5.6.51）。先创建项目库并配置 `.env`，然后在项目根目录显式执行：

```powershell
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m alembic current
```

迁移建立四张业务表及 `alembic_version` 版本表；重复执行不会清空数据或重置默认供应商。普通启动不会自动改表。已有 SQL 初始化的库使用同一命令，结构核对通过才登记版本，不能用 `stamp` 绕过错误。

MySQL 5.6 不依赖 CHECK 约束，业务校验由应用负责；群标识限制为 191 个 ASCII 字符，以兼容旧版 InnoDB 的索引长度限制。迁移流程、权限及后续修改方法见 [数据库迁移说明](docs/database-migrations.md)。`sql/001_init.sql` 保留为初始结构参考，后续结构变化统一使用 Alembic。

本地 `.env` 已由模板创建，里面的会话签名密钥和供应商加密密钥已随机生成，不要随意更换；加密主密钥丢失后无法解密已有供应商密钥。请完成以下配置：

1. 填写 `MYSQL_HOST`、`MYSQL_PORT`（Docker 映射端口）、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`。
2. 在项目根目录执行上方 `alembic upgrade head` 命令。
3. 执行下面的命令，输入至少 12 字符的管理员密码，将输出的 `ADMIN_PASSWORD_HASH=...` 整行写入 `.env`：

```powershell
.\.venv\Scripts\python -m qbot hash-password
```

4. 设置 `ADMIN_USERNAME`，将 `ADMIN_ENABLED` 改为 `true`；本机 HTTP 保持 `ADMIN_COOKIE_SECURE=false`，HTTPS 部署改为 `true`。
5. 更新依赖并启动：

```powershell
.\.venv\Scripts\python -m pip install -c constraints.txt -e .
.\.venv\Scripts\python -m qbot serve
```

访问 [http://127.0.0.1:8080/admin](http://127.0.0.1:8080/admin) 登录。先添加供应商，填写服务地址、模型及 API Key，启用后点击“设为默认”。编辑时密钥留空表示保留。默认项必须先切换才能停用；取消默认项后需要重新选择供应商才能聊天，不再回退到环境变量。

后台模式不要求提前填写 QQ 或模型凭证，可先登录配置供应商。在「机器人管理」填写 AppID 和 AppSecret，首次保存或更换 AppID 时必须填写密钥，后续留空保留原值。保存后自动重新连接对应机器人使 QQ 配置生效。QQ AppSecret 与模型 API Key 均使用本地加密主密钥加密后保存到 MySQL，查询接口只返回掩码；AppID 是公开标识，正常展示。已有 `.env` 凭证不会自动导入或删除，后台模式会忽略它们。QQ 入口缺少凭证时返回 503。保存模型供应商配置不调用模型，下一次聊天才使用新配置；同样支持 `chat` 模式的本地记录。`/reset` 清空上下文，不删除历史。

`ADMIN_ENABLED=false` 保留原来的无数据库聊天模式，不提供管理页面。新机器初始化 `.env` 时，可使用 `python -m qbot generate-secrets` 生成两种密钥；不要覆盖已经使用中的加密主密钥。

新增配置包括上面的 MySQL 五项、`ADMIN_ENABLED`、`ADMIN_USERNAME`、`ADMIN_PASSWORD_HASH`、`ADMIN_SESSION_SECRET`、`PROVIDER_ENCRYPTION_KEY`、`ADMIN_COOKIE_SECURE`；完整定义见 `qbot/config.py`。应用在宿主机运行时使用映射端口；不要使用应用容器的 localhost 连接另一个 MySQL 容器。

## 代码位置

- 启动入口：`qbot/__main__.py`
- 智能体组装与会话：`qbot/chat.py`
- 环境配置 / 模型配置：`qbot/config.py` / `qbot/model.py`
- 多机器人生命周期：`qbot/bots.py`
- QQ WebSocket 网关：`qbot/gateway.py`
- QQ Webhook 与后台队列：`qbot/server.py`
- QQ 验签、凭证和回复接口：`qbot/qq.py`
- 后台接口与登录：`qbot/admin.py` / `qbot/security.py`
- 数据库迁移：`alembic.ini` / `migrations/`
- 数据库与聊天记录：`qbot/storage.py` / `qbot/recording.py`
- 后台页面：`qbot/static/`
- 系统提示词：`qbot/prompts/system.txt`，修改后重启生效
- 工具函数：尚未引入工具调用，按实际需求新增
- 开发规范：`AGENTS.md`，不作为业务提示词

## 验证

按维护者要求，已删除测试文件及 pytest 项目配置，日常修改不自动新增或运行测试，仅在明确要求时执行。此前记录的测试结果属于历史验证，不代表当前保留测试套件。

## 当前问题和下一步

- MySQL 建表和迁移已验证，下一步进行真实业务读写与后台联调。复用已有 Docker MySQL，不另建服务。
- 填写本地 `.env`、执行迁移并生成管理员密码哈希后，即可启动后台。
- 真实 QQ / DeepSeek 联调待完成；WebSocket 模式不需要公网 HTTPS 回调地址。
- 当前为单进程串行处理，适合个人低频使用。限流返回 HTTP 429，队列满返回 503；超时等待会提示重新发送，已过回复窗口的消息跳过。
- 后台模式在入队确认前将 QQ 接收记录落库，使用唯一消息 ID 持久化去重；数据库失败返回 503。运行队列仍在内存中，崩溃后未完成记录可能保留 pending/processing，不会自动重放。未启用后台时仍只使用内存去重。
- 模型失败会尝试回复错误提示；QQ 发送失败仅记录安全日志，不无限重试。发送超时可能已送达，因此不会盲目再次发送。

参考：[QQ Webhook](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)、[QQ 验签](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html)、[DeepSeek API](https://api-docs.deepseek.com/)、[LangChain DeepSeek 集成](https://docs.langchain.com/oss/python/integrations/chat/deepseek)。

## 后台凭证配置更新

2026-10-24：已增加 QQ 配置页面，后台模型仅使用数据库默认供应商。37 项离线测试及 JavaScript 语法检查通过；qbot 库已迁移至 `0002_qq_settings` 并核验新增字段。未打开浏览器，未调用真实 QQ 或模型 API。

更新代码后先执行 `python -m alembic upgrade head`，再重启服务。数据库连接、管理员登录配置、会话签名密钥和加密主密钥仍保存在本地 `.env`，不能迁入依赖这些配置才能访问的后台。

后台默认开启，本地 `.env` 应保持 `ADMIN_ENABLED=true`。`DEEPSEEK_API_KEY`、`QQ_APP_ID`、`QQ_APP_SECRET` 可以为空，不影响后台启动；登录后在 Web 中配置即可。只有显式设置 `ADMIN_ENABLED=false` 的旧版无后台模式才要求环境凭证。数据库连接、管理员密码哈希、会话签名密钥和加密主密钥仍须配置。

## WebSocket 更新

新增依赖 `websockets` 和迁移 `0003_qq_transport`。更新后安装依赖并升级数据库，再启动服务：

```powershell
.\.venv\Scripts\python -m pip install -c constraints.txt -e .
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m qbot serve
```

接入方式默认设为 websocket（包括升级前已有配置），Webhook 用户需在后台切换回 webhook。无后台模式可通过 `QQ_TRANSPORT=websocket` 或 `webhook` 配置，默认 websocket。按维护者要求，本次不新增或执行测试，未进行真实 QQ 联调，不将 WebSocket 标记为验收通过。

## 供应商连接检查

模型供应商卡片的「测试连接」使用已保存的模型、地址和密钥发送“你好”，显示成功/失败、完整调用耗时（毫秒）及文本回复。无需设为默认或启用即可检查，但必须先保存密钥。该操作会调用真实模型，可能产生少量费用；不写入聊天记录、不改变默认供应商。单次生成最多 64 token，总调用超时 25 秒，不自动重试，同一服务每 10 秒最多发起一次且禁止并发检查。错误信息经过脱敏，不返回上游原始异常或密钥。

本功能按要求未运行测试，也未自动调用真实模型。更新代码后重启服务并刷新后台即可使用，无数据库结构变更。

## QQ 私聊

已编写 QQ 私聊（C2C）接收与回复，WebSocket 和 Webhook 共用消息处理流程。QQ 平台需允许该用户与机器人单聊；群聊仍需要 @机器人。后台记录可按「QQ 私聊」筛选，私聊、群聊的上下文、去重和限流按来源隔离。回复使用 `/v2/users/{openid}/messages`，不支持频道私信事件。无需数据库结构迁移，重启服务生效。本次未运行测试或真实私聊联调。

## 多机器人管理（当前版本）

新增迁移 `0004_multiple_bots`，新增 bots 表，并将原 QQ 凭证、模型绑定及所有历史会话和聊天记录归属机器人 1。旧 app_settings 中的 QQ 字段保留，但运行代码不再读取；旧单机器人配置接口已替换为机器人管理接口。升级需停止旧服务，执行 `python -m alembic upgrade head`，再启动新服务。

使用方法：

1. 在 QQ 开放平台分别创建机器人，取得各自 AppID / AppSecret。
2. 在后台「模型供应商」配置模型并启用，可供多个机器人共享。
3. 在「机器人管理」添加机器人，填写名称、凭证、接入方式、供应商及独立提示词。
4. 勾选启用并保存，或稍后点击启动。刷新状态查看是否已连接，连接中不代表已通过平台鉴权。

供应商可独立指定，也可跟随全局默认；提示词留空使用 qbot/prompts/system.txt。相同 AppID 不能重复添加。供应商已绑定机器人时不能停用，应先更换机器人绑定。

保存启用的机器人会自动重载该机器人的连接与提示词，不重启整个服务，其他机器人不受影响。停止会保存禁用状态，下次启动服务不会自动启用它。编辑或重新连接会清空该机器人的内存上下文，历史记录保留。

每个机器人独立 QQ 客户端、Token、WebSocket、消息队列、限流和上下文；数据库会话、去重索引及范围外键包含 bot_id。后台聊天记录可按机器人筛选。停止或编辑时最多等待已有队列 10 秒，超时后未完成记录可能停留在 pending/processing，不自动重放；建议等回复完成后操作。

Webhook 使用 `/qq/webhook/{bot_id}`；旧 `/qq/webhook` 保留为机器人 1 的入口。无后台模式仍只使用一个环境配置机器人。本地聊天可指定：

```powershell
.\.venv\Scripts\python -m qbot chat --bot-id 1
```

本次未新增或运行测试，未打开浏览器，未主动调用模型。数据库迁移已执行，服务重启日志显示原机器人 WebSocket 已连接；多机器人端到端验收待完成。

## 官方机器人资料

机器人卡片通过 QQ `GET /users/@me` 显示官方昵称、头像及用户 ID，使用各自凭证，停用的机器人也可以读取。头像直接使用官方 HTTPS 地址，无需 OSS；昵称、头像为只读，本地备注名不修改 QQ 资料。修改官方资料请到 QQ 开放平台操作。

资料成功缓存 5 分钟，失败缓存 30 秒；可点击刷新官方资料，同一机器人最短刷新间隔 10 秒。读取失败时显示提示和备用名称/头像，不影响消息连接。此功能无数据库结构变更，需重启服务加载；本次未运行测试或实际资料接口验证。


## 家庭成员绑定

新增手写迁移 `0005_family_binding`。更新后先执行 `.\.venv\Scripts\python -m alembic upgrade head`，再重启服务并刷新后台。

在「聊天记录」筛选小苍，找到本人发送的 QQ 消息，点击「查看」，填写姓名（如宇哥）、角色（爸爸/妈妈/朋友）及可选 QQ 号备注，保存绑定。无需手动获取 OpenID：服务端从该记录所属会话提取身份，不接受客户端提交用户 ID。详情可查看、修改或解除绑定；QQ 号备注不用于鉴权，也不代表平台验证了该 QQ 号。

接口为 `PUT /api/admin/bots/{bot_id}/records/{record_id}/binding`，请求字段为 `member_name`、`family_role`（father/mother/friend）、`qq_number_note`；同路径 DELETE 解除绑定，记录详情 GET 返回当前绑定。所有接口要求管理员会话，写操作沿用 CSRF 校验。只允许绑定该机器人下已有的 QQ 消息，不支持本地聊天。

绑定复用会话身份，按机器人、来源、群和用户隔离；同一会话的历史消息显示当前绑定，私聊和不同群需分别确认绑定。下一次对话读取当前身份并传入系统消息；身份改变时清空该会话的内存上下文，历史记录保留。家庭角色不授予管理权限。本次未新增或执行测试，未打开浏览器，未调用真实模型；端到端效果待人工验收。


## 官方资料显示修复

QQ 官方头像可能返回 HTTP 的 qlogo.cn 地址，服务端仅对该官方域名升级为 HTTPS，避免头像被校验和页面安全策略丢弃。聊天列表、机器人筛选和详情同步显示官方昵称，获取失败时使用本地备注及机器人编号。头像加载失败会单独提示。无需数据库迁移，重启服务并刷新后台生效；本次未运行测试或打开浏览器。
