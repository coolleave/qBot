# qBot 对话智能体

使用 Python + LangChain + DeepSeek 开发的个人 QQ 对话机器人。使用 FastAPI 接收 Webhook，并提供轻量管理后台。启用后台后，通过 MySQL 保存聊天记录与供应商配置；模型上下文仍保存在内存，单进程按顺序处理消息。

## 当前进度

- [x] 项目骨架、环境变量配置、独立业务提示词
- [x] 基础对话（LangChain + DeepSeek 适配，模拟响应验证通过）
- [x] 多轮对话（隔离、过期、裁剪、清空及顺序处理离线验证通过）
- [ ] 真实 DeepSeek API 联调（需要本地配置有效密钥）
- [ ] QQ 消息接收与回复验收（代码和模拟回调已验证，尚未真实平台联调）
- [x] 后台管理页面与管理员登录（接口离线验证通过，按约定未做浏览器验证）
- [x] 供应商、模型与 API 密钥管理（加密、掩码及切换离线验证通过）
- [x] 聊天记录存储与查询代码（隔离数据库验证通过）
- [ ] 真实 MySQL 连接、DDL 与读写联调
- [ ] 工具调用（后续版本）
- [ ] 重启后恢复对话上下文（后续版本，与聊天记录查询分开实现）

完成并验证后再勾选；离线验证不代表真实平台验收通过。

## 如何运行

推荐 Python 3.13，本次验证版本为 Windows / Python 3.13.9。以下 PowerShell 命令在项目根目录执行，无需激活虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c constraints.txt -e ".[dev]"
Copy-Item .env.example .env
```

首次配置才复制 `.env`，不要覆盖已有密钥。编辑 `.env`，先填写 `DEEPSEEK_API_KEY`，随后启动本地对话：

```powershell
.\.venv\Scripts\python -m qbot chat
```

`/reset` 清空当前会话，`/exit` 退出本地对话。实际聊天会调用付费 API；启动或检查健康状态不会调用模型。

接入 QQ 时，额外填写 `QQ_APP_ID` 和 `QQ_APP_SECRET`，运行：

```powershell
.\.venv\Scripts\python -m qbot serve
```

默认监听 `127.0.0.1:8080`，健康检查路径为 `/health`，仅表示进程正常。通过反向代理配置公网 HTTPS，将 `/qq/webhook` 转发至本服务。在 QQ 开放平台设置 `https://你的域名/qq/webhook` 并订阅 `GROUP_AT_MESSAGE_CREATE`，按平台要求设置测试群或发布权限，再到群里 @ 机器人发送文字。

QQ 回调必须携带平台签名；服务验证原始请求体和签名时间（允许偏差 5 分钟），请保持服务器时钟同步。TLS 由反向代理终止，不直接把本地 HTTP 地址填到平台。只运行一个进程，不启用多 worker。

## 配置

全部配置见 `.env.example`，环境变量优先于当前工作目录下的 `.env`。真实密钥不提交到 Git。

| 配置 | 默认值 / 说明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 必填，DeepSeek 密钥 |
| `DEEPSEEK_MODEL` | `deepseek-flash`，可修改为账号可用模型 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `MODEL_TIMEOUT_SECONDS` / `MODEL_MAX_RETRIES` | 单次请求超时 45 秒，最多重试 1 次 |
| `CHAT_TIMEOUT_SECONDS` / `MODEL_MAX_TOKENS` | 整次生成总时限 90 秒，最多输出 1024 token |
| `QQ_APP_ID` / `QQ_APP_SECRET` | QQ 模式必填；凭证获取与回调验签使用 |
| `QQ_API_BASE_URL` / `QQ_TOKEN_URL` | 官方生产地址，见示例文件 |
| `QQ_TIMEOUT_SECONDS` | QQ HTTP 请求超时 10 秒；后台单次回复另有 25 秒总时限 |
| `HOST` / `PORT` | `127.0.0.1` / `8080` |
| `SESSION_TTL_SECONDS` / `SESSION_MAX_TURNS` | 闲置 1800 秒过期，保存最近 10 轮 |
| `SESSION_MAX_COUNT` | 最多 1000 个会话，达到上限淘汰最久未使用的会话 |
| `MAX_INPUT_CHARS` / `MAX_REPLY_CHARS` | 输入上限 4000 字符，回复超过 1500 字符截断 |
| `RATE_LIMIT_PER_MINUTE` / `GLOBAL_RATE_LIMIT_PER_MINUTE` | 每个群内用户每分钟最多 6 条，全局最多 30 条 |
| `QUEUE_MAX_SIZE` | 最多 32 条待处理消息 |

默认关闭 DeepSeek 深度思考并一次性回复文本。会话按“群 + 用户”隔离，模型上下文仅在内存保存，重启清空；启用后台后聊天记录单独保存至 MySQL。`/reset` 仅清空发起者在当前群的会话。无文字的消息提示当前仅支持文本，附件不会传给模型。

## 后台与 MySQL

你自行在已有 Docker MySQL 中创建项目数据库，然后在数据库客户端选中该库，执行 [sql/001_init.sql](sql/001_init.sql)。要求 MySQL 8.0.16+，推荐 8.4；脚本使用 InnoDB / utf8mb4，不包含建库、删表或账号操作。

脚本建立 `model_providers`、`app_settings`、`conversations`、`chat_records` 四张表，并初始化一行运行配置。重复执行不会清空数据或重置默认供应商，但也不会升级已有表结构。管理员账号和加密主密钥由环境配置提供。

代码已接入这四张表，普通启动不会建库或改表。当前还未使用真实 MySQL 验证。

本地 `.env` 已由模板创建，里面的会话签名密钥和供应商加密密钥已随机生成，不要随意更换；加密主密钥丢失后无法解密已有供应商密钥。请完成以下配置：

1. 填写 `MYSQL_HOST`、`MYSQL_PORT`（Docker 映射端口）、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`。
2. 在你新建的库中执行 `sql/001_init.sql`。
3. 执行下面的命令，输入至少 12 字符的管理员密码，将输出的 `ADMIN_PASSWORD_HASH=...` 整行写入 `.env`：

```powershell
.\.venv\Scripts\python -m qbot hash-password
```

4. 设置 `ADMIN_USERNAME`，将 `ADMIN_ENABLED` 改为 `true`；本机 HTTP 保持 `ADMIN_COOKIE_SECURE=false`，HTTPS 部署改为 `true`。
5. 更新依赖并启动：

```powershell
.\.venv\Scripts\python -m pip install -c constraints.txt -e ".[dev]"
.\.venv\Scripts\python -m qbot serve
```

访问 [http://127.0.0.1:8080/admin](http://127.0.0.1:8080/admin) 登录。先添加供应商，填写服务地址、模型及 API Key，启用后点击“设为默认”。编辑时密钥留空表示保留。默认项必须先切换才能停用；“使用环境配置”会回退到原来的 DeepSeek 环境变量。

后台模式不要求提前填写 QQ 或模型凭证，可先登录配置供应商。QQ 入口缺少凭证时返回 503。保存配置不调用模型，下一次聊天才使用新配置；同样支持 `chat` 模式的本地记录。`/reset` 清空上下文，不删除历史。

`ADMIN_ENABLED=false` 保留原来的无数据库聊天模式，不提供管理页面。新机器初始化 `.env` 时，可使用 `python -m qbot generate-secrets` 生成两种密钥；不要覆盖已经使用中的加密主密钥。

新增配置完整列表见 `.env.example`。应用在宿主机运行时使用映射端口；不要使用应用容器的 localhost 连接另一个 MySQL 容器。

## 代码位置

- 启动入口：`qbot/__main__.py`
- 智能体组装与会话：`qbot/chat.py`
- 环境配置 / 模型配置：`qbot/config.py` / `qbot/model.py`
- QQ Webhook 与后台队列：`qbot/server.py`
- QQ 验签、凭证和回复接口：`qbot/qq.py`
- 后台接口与登录：`qbot/admin.py` / `qbot/security.py`
- 数据库与聊天记录：`qbot/storage.py` / `qbot/recording.py`
- 后台页面：`qbot/static/`
- 系统提示词：`qbot/prompts/system.txt`，修改后重启生效
- 工具函数：尚未引入工具调用，按实际需求新增
- 开发规范：`AGENTS.md`，不作为业务提示词

## 验证

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m pip check
```

测试使用函数形式、模拟模型和 HTTP 响应，禁止连接外网，不调用付费接口。`constraints.txt` 记录本次验证的依赖版本，升级依赖后需重新验证。

2026-10-24 验证结果：26 项离线测试通过，覆盖后台鉴权、CSRF、密钥保护、供应商切换、记录查询及消息状态；`pip check` 和 JavaScript 语法检查通过。数据库测试使用隔离 SQLite 验证仓储逻辑，不代表 MySQL DDL 已验证。未打开浏览器，未调用真实 DeepSeek 或 QQ API。

## 当前问题和下一步

- 后台页面、接口及数据库访问已实现，下一步使用真实 MySQL 联调。复用已有 Docker MySQL，不另建服务。
- 填写本地 `.env`、执行 DDL 并生成管理员密码哈希后，即可启动后台。
- 尚未提供真实凭证及公网 HTTPS 回调地址，下一步进行 DeepSeek 实际对话和 QQ 测试群联调。
- 当前为单进程串行处理，适合个人低频使用。限流返回 HTTP 429，队列满返回 503；超时等待会提示重新发送，已过回复窗口的消息跳过。
- 后台模式在入队确认前将 QQ 接收记录落库，使用唯一消息 ID 持久化去重；数据库失败返回 503。运行队列仍在内存中，崩溃后未完成记录可能保留 pending/processing，不会自动重放。未启用后台时仍只使用内存去重。
- 模型失败会尝试回复错误提示；QQ 发送失败仅记录安全日志，不无限重试。发送超时可能已送达，因此不会盲目再次发送。

参考：[QQ Webhook](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)、[QQ 验签](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html)、[DeepSeek API](https://api-docs.deepseek.com/)、[LangChain DeepSeek 集成](https://docs.langchain.com/oss/python/integrations/chat/deepseek)。
