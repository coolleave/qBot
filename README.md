# qBot 对话智能体

当前版本：**V0.5**（包版本 `0.5.0`）。本版包含 Vue 3 / Element Plus 后台、独立 Token 统计、语音输出优化及 Docker 部署配置。当前开发主线为 Memory 模块，并持续优化现有功能。

## 模块文档导航

按功能维护代码时先查 [模块文档索引](docs/README.md)：[智能体链路](docs/agent-flow.md)、[多模态处理](docs/multimodal.md)、[定时任务](docs/scheduled-tasks.md)、[QQ 接入与多机器人](docs/qq-runtime.md)、[后台与模型](docs/admin-and-models.md)、[主动发送](docs/proactive-messages.md)、[存储与记录](docs/storage.md)、[数据库迁移](docs/database-migrations.md)、[数据库表结构（Agent）](docs/database-schema.md)。AI 开发入口仍为根目录 [AGENTS.md](AGENTS.md)，其中提供按功能查阅规则。

模块文档集中说明当前实现、代码入口和边界；本 README 保留启动配置、操作说明和历史更新。原方案文档保留设计背景与待验收项，不将其视为全部已实现。本次整理仅核对文档与源码，未运行测试、迁移、浏览器或真实接口验证。


## 聊天 Token 统计

后台独立菜单「Token 统计」提供输入、输出、总用量及记录覆盖率卡片、每日柱状趋势、机器人/模型 Top 10 与对话 Top 20 排行。可按机器人、对话、历史模型名及日期组合筛选，默认最近30天，最多366天，日期按北京时间。对话可按名称、用户/群 OpenID、会话 ID 搜索并分页选择，点击排行可继续筛选；未知用量与无记录分别展示。「聊天记录」保留每条用量和详情。输入用量包含模型实际接收的提示词和历史上下文；用量在解析回复和发送 QQ 消息前保存，发送失败仍保留已返回的消耗。QQ 私聊、群聊及启用后台的本地聊天共用统计。

可视化模块复用 `0012_token_usage` 字段，无新迁移、依赖或环境变量；已升级该版本的环境重启后端并刷新后台即可加载新菜单。新增管理员接口 `/api/admin/token-stats` 和 `/api/admin/token-stats/options`，前端入口为 `qbot/static/token-stats.js`。本次仅做静态核对，未运行测试、浏览器或数据库验证。

仅统计供应商返回的聊天模型用量，优先读取 LangChain `usage_metadata`，兼容 `response_metadata.token_usage`。总数缺失但输入和输出齐全时相加；缺失字段显示「未知」，历史记录不回填，未调用模型的记录不计入已知用量。各项汇总只累加该项已知值，因此部分字段缺失时输入与输出的汇总不一定等于总量汇总。视觉、ASR、TTS、供应商试用、问候草稿及无后台模式不在本次统计范围，无法获得用量的失败请求也无法计量；该统计不能代替供应商账单，不估算费用。

升级前停止服务，在项目目录执行 `.\.venv\Scripts\python -m alembic upgrade head`（新增 `0012_token_usage`），再重启服务并刷新后台。迁移仅向 `chat_records` 增加三个可空 BIGINT 字段；应用不自动迁移。沿用管理员鉴权的 `GET /api/admin/records` 新增 `token_usage` 汇总，列表及详情新增 `input_tokens`、`output_tokens`、`total_tokens`。无新增依赖或环境变量。本次未运行测试、数据库迁移、浏览器验证或真实模型调用。

## 多模态功能

私聊／群聊图片理解、短语音转写、文字及自动语音回复、静态和 GIF 表情包理解的方案见 [多模态对话与自动语音回复方案](docs/multimodal-plan.md)。已接入后台配置和消息处理链路。

## 后台手动主动发送

重启服务并刷新后台，在菜单栏「发送消息」选择机器人及私聊/群聊，从已有接收对象中点击「编写消息」，核对目标并输入文字后点击「立即发送」。接收对象分页展示，同一个群只显示一次；也可从「聊天记录 → 查看 → 主动发送消息」进入。私聊发送给该用户，群聊发送给整个群，不会转为群成员私信。只允许使用对应机器人已有的 QQ 会话，不支持输入任意 QQ 号。此操作不调用模型，也不写入模型的内存上下文。

主动请求不携带历史消息的 `msg_id`，需要 QQ 平台允许该机器人向目标主动发送，并受平台额度、接收范围和内容限制；后台按钮不能绕过这些限制。发送结果以平台响应为准。

接口为 `POST /api/admin/bots/{bot_id}/records/{record_id}/send`，字段为 `content` 和 UUID 格式的 `request_id`，要求管理员登录及 CSRF。内容不超过 `MAX_REPLY_CHARS`；主动发送使用现有每目标/全局每分钟限额配置，计数独立于被动回复。发送总超时 25 秒。

发送前保存独立聊天记录，用户消息栏以「【后台主动发送】」标识，回复栏保存发送内容，状态区分已回复（QQ 确认发送）、发送失败和送达未知。相同目标范围下相同请求标识不会重复发送；网络出错时页面沿用原标识重新提交。送达未知或进程中断留下的待处理记录不会自动重发，请先核对 QQ 后再决定是否创建新请求。无新增依赖、环境变量或数据库迁移；按约定未运行测试或浏览器验证。

使用 Python + LangChain + DeepSeek 开发的个人 QQ 对话机器人。默认通过 WebSocket 接收 QQ 消息，保留 Webhook 可选；FastAPI 提供轻量管理后台。启用后台后，通过 MySQL 保存聊天记录与供应商配置；模型上下文仍保存在内存，单进程运行，每个机器人独立队列按顺序处理消息。

## 后台界面更新（已编写，交互待验证）

后台接入 Vue 3 + Element Plus：登录、导航、聊天表格与分页、状态标签和消息提示使用组件；其他业务表单保留并统一为浅色响应式主题。依赖随 `qbot/static/` 本地分发，无需安装 Node 或运行前端构建，也不依赖浏览器访问 CDN。组件入口 `admin-ui.js`，主题 `admin-theme.css`，详见 [后台与模型](docs/admin-and-models.md)。

本地沿用原启动命令，更新后强制刷新 `/admin`；Docker 部署重新构建镜像。无新增环境变量或数据库迁移。仅做静态检查，未运行测试或浏览器验证。

## 当前进度

- [x] 项目骨架、环境变量配置、独立业务提示词
- [x] 基础对话与多轮上下文管理
- [x] QQ 私聊、群聊消息接收与回复
- [x] 后台管理、管理员登录及供应商配置
- [x] 多机器人管理与独立身份角色
- [x] MySQL 聊天记录存储、查询与 Alembic 迁移
- [x] 聊天 Token 统计
- [x] 多模态输入与回复：图片、GIF、ASR、TTS
- [x] 后台手动发送文字、图片和语音
- [x] 对话页面、用户头像昵称及语音文本展示
- [x] 定时任务、消息流程与模板
- [ ] Memory 模块：持久化记忆、检索与对话上下文衔接（当前主线）
- [ ] 现有功能优化：交互体验、稳定性与问题排查
- [ ] 工具调用（后续规划）

## 如何运行

### Docker Compose 部署（已编写，待部署验证）

长期运行可使用根目录 `compose.yaml` 和 `Dockerfile`，完整步骤见 [Docker 部署说明](docs/docker-deployment.md)。使用 Python 3.13、单进程运行，镜像包含 `config/` 和 `图片/`；复用已有 MySQL 的外部网络，不创建 MySQL 容器，启动不自动迁移。

保留已有 `.env`，新机器才从 `.env.example` 复制。配置 `QBOT_DOCKER_NETWORK` 为已有 MySQL 所在的用户自定义网络，`MYSQL_HOST` 为该网络上的容器名/别名，`MYSQL_PORT` 为内部端口。含 `$` 的管理员密码哈希须用单引号包裹。完成配置、停止旧服务并备份数据库后执行：

```sh
docker compose build qbot
docker compose run --rm --no-deps qbot python -m alembic upgrade head
docker compose up -d qbot
docker compose logs --tail=100 -f qbot
```

迁移成功后再启动。后台默认映射到宿主机 `127.0.0.1:8092`，可通过 SSH 隧道管理；`QBOT_BIND_IP` / `QBOT_HTTP_PORT` 可调整宿主机映射。当前未构建镜像、执行迁移或启动容器，Linux 依赖兼容性待实际部署确认。

### 本地 Python 运行

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

默认监听 `127.0.0.1:8092`，访问 `/admin` 管理配置，`/health` 仅表示 HTTP 进程正常，不代表 QQ 已连接。仅运行一个进程，不启用多 worker。

### QQ 接入（默认 WebSocket，无需公网 IP）

1. 在 QQ 开放平台「事件订阅与回调地址」选择 WebSocket，并配置机器人服务范围或内部体验号码，将机器人添加到有权限的群。
2. 在本项目后台「机器人管理」填写 AppID、AppSecret，接入方式选择 WebSocket，保存后自动重新连接对应机器人。
3. 在「模型供应商」添加模型 API Key、启用并设为默认。
4. 在「机器人管理」点击刷新连接状态，连接成功后在群里 @机器人发送文字。

程序主动连接官方 WSS 网关，订阅 `GROUP_AND_C2C_EVENT (1 << 25)`，处理其中的 `GROUP_AT_MESSAGE_CREATE` 群聊事件和 `C2C_MESSAGE_CREATE` 私聊事件。回复仍通过 QQ HTTP API 发送。本机无需域名、公网 IP 或端口转发，但需要能访问 QQ 和模型服务并持续运行。

连接具有心跳、确认超时检测、短线 Resume 和指数退避重连；连续失败 10 次停止重连，修复配置后重启。当前仅单分片，恢复会话只在内存保存，进程重启后重新登录，不能恢复丢失的运行队列。限流消息记录日志后跳过，队列满或数据库失败会断线尝试恢复，超过平台保留窗口不保证补发。

### 可选 Webhook

在本项目后台及 QQ 开放平台同时选择 Webhook，保存后自动重新连接对应机器人。配置公网 HTTPS 地址 `https://你的域名/qq/webhook/{bot_id}`，通过反向代理转发到 `http://127.0.0.1:8092/qq/webhook/{bot_id}`，订阅 `GROUP_AT_MESSAGE_CREATE` 和 `C2C_MESSAGE_CREATE`。此模式需要公网入口。回调验签允许时间偏差 5 分钟，请保持服务器时钟同步。WebSocket 模式拒绝 Webhook 请求，避免同时接入。

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
| `HOST` / `PORT` | `127.0.0.1` / `8092` |
| `SESSION_TTL_SECONDS` / `SESSION_MAX_TURNS` | 闲置 1800 秒过期，保存最近 10 轮 |
| `SESSION_MAX_COUNT` | 最多 1000 个会话，达到上限淘汰最久未使用的会话 |
| `MAX_INPUT_CHARS` / `MAX_REPLY_CHARS` | 输入上限 4000 字符，回复超过 1500 字符截断 |
| `RATE_LIMIT_PER_MINUTE` / `GLOBAL_RATE_LIMIT_PER_MINUTE` | 每个群内用户每分钟最多 6 条，全局最多 30 条 |
| `QUEUE_MAX_SIZE` | 最多 32 条待处理消息 |

默认关闭 DeepSeek 深度思考并一次性回复文本。会话按“群 + 用户”隔离，模型上下文仅在内存保存，重启清空；启用后台后聊天记录单独保存至 MySQL。`/reset` 仅清空发起者在当前群的会话。后台模式可按机器人开启图片理解和短语音转写；无后台模式仍仅支持文字。

## 后台与 MySQL

### 登录会话持久化（已编写，待验证）

管理员登录会话改为保存到 MySQL 的 `admin_sessions` 表，Cookie 仍为带签名的 HttpOnly Cookie。有效期固定为登录后 1 小时，服务重启不会清空会话，也不会延长有效期；退出登录立即删除对应会话。修改管理员账号、密码哈希或 `ADMIN_SESSION_SECRET` 后旧登录失效。数据库只保存登录 Token 的 SHA-256 摘要；CSRF 校验保持不变。

升级前停止服务，执行 `.\.venv\Scripts\python -m alembic upgrade head`（新增迁移 `0011_admin_sessions`），再启动服务并重新登录一次。旧版内存中的登录无法迁移，之后未过期的登录可跨重启保留。保持原有 `.env` 中的会话签名密钥不变。登录时清理过期会话并保留最多 100 个有效会话，超过上限淘汰最早创建的会话；数据库不可用时接口报告失败。运行账号需拥有 `admin_sessions` 表的 SELECT、INSERT、DELETE 权限。

2026-10-25 已执行迁移至 `0011_admin_sessions`，核对会话表字段并重启服务，`/health` 返回正常。未运行测试或浏览器验证，登录后跨重启保留仍待实际验收；无新增环境变量。

复用已有 Docker MySQL，兼容 MySQL 5.6.5+（本次验证为 5.6.51）。先创建项目库并配置 `.env`，然后在项目根目录显式执行：

```powershell
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m alembic current
```

初始迁移建立四张业务表及 `alembic_version` 版本表，后续版本继续扩展，当前表职责见 [存储与记录](docs/storage.md)；重复执行不会清空数据或重置默认供应商。普通启动不会自动改表。已有 SQL 初始化的库使用同一命令，结构核对通过才登记版本，不能用 `stamp` 绕过错误。

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

访问 [http://127.0.0.1:8092/admin](http://127.0.0.1:8092/admin) 登录。先添加供应商，填写服务地址、模型及 API Key，启用后点击“设为默认”。编辑时密钥留空表示保留。默认项必须先切换才能停用；取消默认项后需要重新选择供应商才能聊天，不再回退到环境变量。

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

- 以 Memory 模块为开发主线，逐步实现记忆持久化、检索及对话上下文衔接，保持机器人和用户之间的数据隔离。
- 配合主线优化现有功能，重点关注后台交互、定时任务、消息收发稳定性和日志诊断。
- 当前模型上下文仍保存在内存中，重启后清空；数据库聊天记录与可供模型调用的持久化记忆分开管理。
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


## 家庭成员绑定（机器人 + 用户）

后台新增独立的「身份绑定」菜单，可按机器人和用户 OpenID 筛选，按 ID 绑定姓名、爸爸/妈妈/朋友角色及 QQ 备注，也可编辑或解除身份。列表将同一机器人下同 OpenID 的群聊、私聊合并为一行，未绑定的已知用户也可直接选择。手填 ID 必须是该机器人已经收到过 QQ 消息的用户 OpenID，无需指定群号或消息来源。

绑定一次后，当前机器人在所有群聊和私聊收到同一 OpenID 的消息时读取同一身份，下一次对话生效；不同机器人独立。如果 QQ 返回不同 OpenID，需分别绑定，不能仅凭 QQ 备注自动关联。「用户绑定」菜单仍用于接收人名称，与聊天身份独立。

新增 `GET /api/admin/identity-bindings` 和 `PUT /api/admin/bots/{bot_id}/identity-bindings`，解除身份复用记录绑定接口；均校验管理员身份，写操作校验 CSRF。复用 `0009` 的身份表，本次菜单更新无需新增迁移，重启服务并刷新后台后使用。本次仅完成代码与差异检查，未运行测试、浏览器验证或真实 QQ 联调。

家庭身份按 `bot_id + user_id` 唯一保存到 `bot_user_identities`，与某条消息、私聊或群聊会话无关。从「聊天记录 → 查看」填写姓名、角色（爸爸/妈妈/朋友）和可选 QQ 号备注，服务端只借助该记录确认真实发送者。保存、修改或解除后，该用户在当前机器人下所有同 OpenID 会话读取同一身份；其他机器人独立。聊天历史与模型上下文仍按原有会话隔离，身份变化后各会话在下一次对话时清空旧上下文。

QQ 号仅是备注，不能用于鉴权或合并身份。如果 QQ 在不同场景返回不同 OpenID，不会仅凭同名或 QQ 备注自动认定为同一用户，需分别确认。接收人名称和任务的发送目的地仍保留私聊/群聊范围，群聊发送给整个群；它们与家庭角色绑定是不同配置。

新增手写迁移 `0009_bot_user_identity`。停止旧服务后执行 `.\.venv\Scripts\python -m alembic upgrade head`，再启动并刷新后台；本次未实际执行迁移。旧绑定按机器人和用户归并，已绑定的姓名、角色、QQ 备注完全一致时迁入。冲突用户不自动选择身份，需管理员从任意相关 QQ 记录重新保存；原 conversations 字段保留用于核对，新代码不再把它们作为身份来源。

接口仍为 `PUT/DELETE /api/admin/bots/{bot_id}/records/{record_id}/binding`，写操作校验管理员和 CSRF；参数不接受客户端指定身份 OpenID。详情及模型对话均从新绑定表读取。家庭角色不授予管理权限。按约定未新增或运行测试、未实际调用模型。


## 官方资料显示修复

QQ 官方头像可能返回 HTTP 的 qlogo.cn 地址，服务端仅对该官方域名升级为 HTTPS，避免头像被校验和页面安全策略丢弃。聊天列表、机器人筛选和详情同步显示官方昵称，获取失败时使用本地备注及机器人编号。头像加载失败会单独提示。无需数据库迁移，重启服务并刷新后台生效；本次未运行测试或打开浏览器。


## 定时主动发送（已编写，待真实 QQ 联调）

后台「定时任务 → 新建任务 → 选择机器人 → 选择绑定用户」直接打开任务表单，无需进入聊天记录。支持按接收人姓名搜索或完整 OpenID 精确查询，并分页选择；先在「用户绑定」设置接收人名称。已有配置显示「查看／编辑已有任务」，打开原配置，不重复新建。私聊发送给本人，群聊发送给整个群。

每个接收范围仍配置早间、午间、晚间三个时段，可设置启用状态、发送时间、星期和最多 200 字的固定文案。使用北京时间 UTC+8，默认发送时间为 07:00、12:00、21:00，新增午间任务默认关闭。各任务独立按日去重，不再配置最晚执行时间。新建入口复用已绑定的 QQ 目标，不支持输入任意 QQ 号发送；本次入口调整无需数据库迁移。详细接口和发送限制见 [定时任务](docs/scheduled-tasks.md)。

服务运行时每轮扫描后间隔 10 秒，逐条主动发送，无需用户先发消息，也不再向普通聊天回复附加问候。仅在发送时间对应分钟领取任务；启动或恢复时不补发已错过时间的任务。串行发送和接口耗时可能使执行晚于设定时间。每个机器人、会话、早午晚任务每天最多尝试一次；修改配置、重启及 `/reset` 不清除当天领取记录。

每次领取任务时，同一事务创建独立「【定时主动发送】」聊天记录和每日去重记录；发送失败、限流、机器人未运行、送达未知均记录原因，不自动重试。进程中断留下的处理中记录也不重放。定时发送与后台手动发送共享频率限制，单次 QQ 调用超时 25 秒。固定文案不调用模型，也不进入模型上下文。

私聊发送给对应用户，群聊发送给整个群。同一群若为多个成员分别配置任务，会分别发送，请避免重复配置。解除接收人名称绑定或更换机器人 AppID 会停用相关任务。QQ 主动发送仍受平台权限和额度限制，不携带旧消息 ID 冒充回复；服务到点发起请求不代表平台一定允许送达，可在任务历史查看具体状态及错误。

复用已有 `interaction_greetings` 和 `interaction_greeting_runs` 表，无新增迁移、依赖或环境变量。已有启用规则会按新方式执行，原开始时间为发送时间，原结束时间不再参与调度；旧 end_time 列仅保留结构兼容，新增午间配置无需迁移。重启服务并刷新后台生效；部署前可先核对原规则。配置接口仍为 `GET/PUT /api/admin/bots/{bot_id}/records/{record_id}/greetings`，沿用管理员鉴权及 CSRF。

每个早午晚任务新增「执行一次」：立即执行已保存文案，允许未启用的任务手动执行，跳过星期和发送时间限制，但仍要求接收人绑定、机器人运行并遵守限流。每次点击均独立执行，可以重复发送，不占用自动任务每日额度；执行中按钮禁用，失败不自动重试，结果不明时先查询记录，再次点击会重新发送。修改文案后先保存再执行。接口为 `POST /api/admin/bots/{bot_id}/records/{record_id}/greetings/{morning|noon|evening}/run`，同样要求管理员和 CSRF，执行结果在最近记录中显示。

按项目约定未新增或运行测试，未打开浏览器，未实际发送 QQ 消息，真实平台验收待完成。


## 问候文案生成

定时任务的早间、午间、晚间文案旁新增「按人设生成文案」：使用对应机器人最新保存的系统提示词及聊天供应商（未单独指定时使用全局默认），生成最多 200 字的问候并填入草稿。私聊可参考已绑定的姓名与家庭角色，群聊生成面向全群的问候。生成会产生模型调用费用；可修改后点击「保存设置」，生成本身不保存、不发送、不占用当天任务次数，也不写入聊天上下文。定时发送仍使用保存的固定文案，不会每天自动重新生成。

接口为 `POST /api/admin/bots/{bot_id}/records/{record_id}/greetings/{morning|noon|evening}/generate`，要求管理员登录及 CSRF，校验记录归属。单次生成最多调用模型一次、不自动重试，模型超时 30 秒、生成总超时 35 秒；后台最多同时生成一条，开始生成间隔至少 10 秒。失败保留原文案。无需新增依赖、环境变量或迁移；重启服务、刷新后台后使用。按约定未运行测试、浏览器验证或真实模型调用。

## 接收人名称绑定

「用户绑定」用于将 OpenID 映射为接收人名称（例如宇哥、雯雯），供「定时任务」和「发送消息」选择，不设置爸爸/妈妈等家庭角色。选择机器人、私聊或群聊，填写已有 OpenID、接收人名称及可选 QQ 备注；群聊还需群 OpenID。发送群消息仍面向整个群，不会把群内成员 OpenID 当作私聊 ID。

新增迁移 `0007_recipient_names`，将接收人名称/QQ备注保存到独立字段。升级时从原有姓名初始化显示名称，之后与家庭角色独立维护。原聊天详情中的家庭绑定继续用于模型称呼；新的用户绑定只影响接收人列表。修改接收人名称不改变身份及问候次数；解除接收人绑定会停用该会话问候。定时任务现为主动发送，触发方式见上文。

`GET /api/admin/user-bindings` 查询用户，`PUT /api/admin/bots/{bot_id}/user-bindings` 保存 contact_name、contact_qq、source、user_id、group_id；`DELETE /api/admin/bots/{bot_id}/records/{record_id}/user-binding` 解除名称绑定。沿用管理员鉴权、CSRF 校验及已有消息身份校验。本次未运行测试或浏览器验证。


## 后台发送图片和语音

「发送消息 → 选择接收人 → 编写消息」或聊天详情的发送入口，可选择文字、图片、语音。图片支持 PNG/JPG；按当前官方接口页，私聊语音支持 SILK，群聊语音支持 SILK/MP3/WAV。文字转语音见下方「TTS 文本试听与发送」；不包含录音；私聊 PCM WAV 自动转为 SILK，见下文。图片可本地预览，MP3/WAV 可在浏览器支持时试听，SILK 仅显示文件名和大小。

本地文件上限 3,500,000 字节（3.5 MB），为 Base64 后的官方 5M 直传限制预留空间。服务端限制读取体积和上传时长，检查扩展名与文件头，不接受任意本地路径或远程 URL，不新增 OSS 或公网服务；私聊 WAV 转码使用下述 silk-python 依赖。先以 `srv_send_msg=false` 上传至相应 users/groups files 接口，再以 `msg_type=7` 和返回的 `media.file_info` 发送。文件信息即取即用，不跨机器人或会话类型缓存。上传成功不代表发送成功；上传阶段失败明确为未发送，发送超时标记送达未知，不自动重发。

接口：`POST /api/admin/bots/{bot_id}/records/{record_id}/send-media?kind=image|audio&filename=文件名&request_id=UUID`，请求体为原始文件字节，沿用管理员登录及 CSRF 校验；一次只处理一个媒体请求。复用接收人权限、发送限流和聊天记录幂等，记录保存文件名、大小及 SHA256，不保存文件原文、Base64、file_info 或临时公开下载地址。历史记录仅提供文件摘要，不支持从历史重新下载；关闭发送窗口后如需重新发送，需重新选择文件。

图片和语音发送仍受 QQ 平台主动发送权限约束，不利用旧消息 ID 冒充回复。升级仅需重启服务并刷新后台，无数据库迁移。本次未运行测试或浏览器验证，未实际上传或发送用户文件，平台联调待人工操作。

官方接口：[私聊上传](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_files.post.html)、[群聊上传](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_files.post.html)。GitHub 旧文档关于不支持 file_data 的说明与当前官网不同，此实现参考官网直传参数。

### 模型类型

模型供应商支持 `model_type`：`chat`（聊天）、`image`（图片生成）、`asr`（语音识别）、`tts`（语音合成）。接口类型 `api_type` 单独配置。升级先执行 `python -m alembic upgrade head`，`0008_model_types` 将已有模型设为 `chat`，再重启服务。

机器人绑定、全局默认仅支持 `chat`，连接测试支持聊天及下述 TTS 文本试听；已绑定或作为默认的模型，须先解除引用才能改为其他类型。图片生成当前仅支持保存配置，尚未接入生成调用。ASR 已接入同步短语音与 Filetrans 异步转写，见 [多模态处理](docs/multimodal.md)；TTS 支持下述 Qwen Realtime 合成流程。


### TTS 文本试听与发送（已编写，待真实联调）

供应商模型类型选择 `tts`、接口类型选择「OpenAI 兼容接口」，模型填写 `qwen3-tts-flash-realtime`（也接受其快照名称），保存百炼 API Key。此模型实际通过专用 WebSocket 协议调用，不经过 Chat Completions。国内服务地址填写 `https://dashscope.aliyuncs.com`；国际站使用对应地域的域名和密钥。根地址和 `/compatible-mode/v1` 自动映射到 `/api-ws/v1/realtime`，也可以直接填写完整的 HTTPS 形式端点。

- 供应商卡片输入文本和音色，点击「测试连接」，生成后点击播放器试听。默认音色 `Bunny`（萌小姬，可爱童声风格），可选 `Bella`（萌宝）、`Mochi`（沙小弥，童真男声）、`Momo`（茉兔，俏皮）或填写官方音色 ID。无需启用即可测试已保存的供应商。
- 「发送消息 → 编写消息 → 语音」可选择已启用的 TTS 供应商，输入文本并生成语音。群聊和私聊均可试听后直接点击「立即发送」，复用本次音频及既有媒体发送幂等逻辑，不再次合成。修改合成设置后需重新生成。
- 合成输出 24 kHz 单声道 16 位 PCM WAV 供试听；私聊点击发送后自动转为腾讯 SILK 再上传，群聊继续发送 WAV。自行上传的单声道 16 位 PCM WAV（8/12/16/24 kHz）也可自动转换；其他 WAV 或私聊 MP3 不支持。

`POST /api/admin/providers/{provider_id}/speech` 接收 `text`（1～500 字符）及 `voice`（默认 Bunny），返回 `audio/wav`，要求管理员登录和 CSRF。密钥从数据库解密读取；每次生成一次付费调用，无自动重试，60 秒总超时，音频上限 3.5 MB，与连接测试共用并发限制和 10 秒间隔。保存配置不调用模型。音频只保留在响应及当前页面内存，不持久化。

合成位于 `qbot/tts.py`，转码位于 `qbot/audio_codec.py`。新增 `silk-python==0.2.8` 依赖，已安装到项目 `.venv`；使用其他 Python 环境启动时，需在对应环境安装依赖。更新依赖后重启服务并刷新后台：

```powershell
.\.venv\Scripts\python -m pip install -c constraints.txt -e .
.\.venv\Scripts\python -m qbot serve
```

转码不调用外部服务或模型、不落地音频文件，使用独立隐藏子进程，15 秒超时，不自动重试。失败记录为未发送；请求去重仍以原始 WAV 为准，重试不会重新合成或重复发送。音色和转码更新本身无需数据库迁移或新增环境变量（之前的身份绑定迁移仍需执行）。按项目约定未运行测试、未打开浏览器、未调用真实 TTS 或 QQ 接口。

协议参考：[阿里云 Qwen Realtime 客户端事件](https://www.alibabacloud.com/help/en/model-studio/qwen-tts-realtime-client-events)。


#### 能否自定义音色

`qwen3-tts-flash-realtime` 使用预设音色，音色栏填写的是官方 ID，不是“可爱小孩”等描述。若要用文字设计专属声音，需要 Qwen3-TTS-VD 系列；若用音频样本复刻声音，需要 Qwen3-TTS-VC 系列，并先创建对应模型可用的音色。当前项目尚未接入 VD/VC 音色创建及合成，不能把自定义音色 ID 直接用于 Flash。

参考：[官方音色列表](https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list)、[模型及定制方式](https://help.aliyun.com/zh/model-studio/tts-model/)、[SILK 编码库](https://github.com/synodriver/pysilk)。

## 多模态部署与使用

新增迁移 `0010_multimodal`（依赖 `0009_bot_user_identity`）及 Pillow、miniaudio 解码依赖。停止服务后在项目目录执行以下命令，再启动服务、刷新后台：

```powershell
.\.venv\Scripts\python -m pip install -c constraints.txt -e .
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m qbot serve
```

1. 在「模型供应商」添加支持图片输入的 `chat` 模型，选择兼容接口并勾选「支持图片输入」。该勾选是管理员声明，仍需通过上传图片试用确认账号和模型实际支持。
2. ASR 使用 `asr` 类型，填写 `qwen-audio-3.1-asr-flash`、兼容的 `qwen-audio-3.0-asr-flash` 或 `fun-asr-flash` 模型及百炼服务地址（例如 `https://dashscope.aliyuncs.com`）。ASR 使用百炼原生接口，不是 OpenAI 聊天接口。其他 ASR 协议暂不支持。
3. TTS 沿用 `qwen3-tts-flash-realtime`。在「机器人管理 → 编辑配置 → 多模态与回复方式」开启图片、动态表情、短语音转写，选择对应供应商。必须单独选择视觉理解模型：它只提取图片内容，再由机器人的聊天模型结合人设、绑定身份和上下文生成回复。聊天模型无需支持图片；视觉供应商仍在模型供应商中配置为支持图片输入的兼容 chat 模型。
4. 私聊和群聊独立选择「文字」「收到语音时用语音回复」「优先语音回复」，音色默认 `Bunny`，也可填写供应商支持的音色 ID。声音复刻与声音设计未接入。
5. 供应商卡片可上传图片/GIF 或短语音检查效果，TTS 可输入文本试听。这些按钮会实际调用付费接口；不会向 QQ 发送。聊天记录详情显示转写、附件处理状态、模型用量、耗时、回复形式及回退原因。

每条最多处理前4个附件，其中最多3张图片、1段语音；单文件8 MB、合计16 MB、短语音60秒。图片支持 PNG/JPEG/WebP/GIF，GIF 最多抽4帧，总图片输入不超过8帧；单图最多2000万像素，动画扫描最多100帧且累计最多1亿像素，超出动画预算仅识别首帧并提示。音频可解码 WAV/MP3/FLAC/Ogg/SILK，具体编码兼容性仍需实际文件验收。附件必须来自允许的 QQ CDN；URL、原始音频、图片和 base64 不写入聊天记录。

QQ 表情优先读取事件 `ext` 中携带的名称；只有 ID 而没有名称的表情保留未知 ID 占位，不猜含义。Unicode Emoji 保持原样。图片只传给视觉模型，聊天模型接收文字说明；视觉摘要保留在当前及后续会话上下文中，后续没有原图，需要精确复看时重新发送。

自动语音仅用于不超过300字符、无代码块/链接/表格的短回复，且必须有充足回复时间。私聊 WAV 自动转 SILK；合成、转码、上传失败回退文字，最终发送开始后不补发，超时记为送达未知。附件失败时明确提示，全部附件失败且没有文字时不调用聊天模型。群聊仍只处理平台推送的 @机器人事件。定时任务、后台手动发送保持独立策略。

处理主链路最多120秒（含排队时间），错误文字通知另限10秒且不超过平台回复窗口。模型与 ASR 不自动重试；识别和合成共享最多2个并发媒体处理名额。原始附件不持久化，但转写与处理详情会随聊天记录保存。只展示供应商返回的用量，不估算实时价格。

本次仅执行静态检查，不新增或运行测试、不打开浏览器、不调用真实模型/QQ，不自动迁移数据库或重启机器人。上线前需人工执行迁移并验收图片、GIF、真实 QQ 语音格式及私聊/群聊发送权限。

### 独立视觉理解更新

图片和 GIF 每条最多调用一次视觉模型，再调用一次聊天模型，不自动重试。视觉阶段上限 30 秒（客户端超时 25 秒），仍受整条消息 120 秒期限约束；聊天记录保留聊天模型快照，多模态详情单独记录视觉模型、摘要、用量、耗时和失败原因。视觉失败会明确提示，不继续编造图片回复。后台图片试理解也使用相同视觉分析逻辑。

原先视觉模型留空的机器人，需在「机器人管理 → 多模态与回复方式」补选视觉理解模型并保存；普通文字聊天不受影响。本次无需新增迁移或依赖，重启后端并刷新页面后生效。未运行测试、浏览器验证或真实模型联调。

## DeepSeek / 千问供应商预设与 Filetrans ASR

在「模型供应商 → 添加供应商」选择 DeepSeek、Qwen（千问）或自定义。预设只填写表单，不自动创建供应商或更改现有密钥；可编辑地址和模型。切换预设或模型类型会重填推荐字段，编辑旧配置仅打开表单不会覆盖原值。更换服务域名时必须重新填写密钥。

| 预设 | 类型 | 默认模型 | 地址 |
| --- | --- | --- | --- |
| DeepSeek | chat | `deepseek-flash` | `https://api.deepseek.com` |
| Qwen | chat，开启图片声明 | `qwen3.8-flash` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Qwen | asr | `qwen-audio-3.0-asr-flash-filetrans` | `https://dashscope.aliyuncs.com` |
| Qwen | tts | `qwen3-tts-flash-realtime` | `https://dashscope.aliyuncs.com` |
| 自定义 | 自行填写 | 自行填写 | HTTPS 服务地址 |

千问预设为百炼北京地域，使用对应地域的 API Key；其他地域或工作空间域名可以手动填写。自定义聊天支持现有 DeepSeek / OpenAI 兼容协议；自定义 ASR/TTS 仍需符合已接入的百炼协议，并不自动支持任意供应商。

`qwen-audio-3.0-asr-flash-filetrans` 与同步 `qwen-audio-3.0-asr-flash` 分开适配。Filetrans 会将解码后的 WAV 上传至百炼临时存储，以 `file_urls` 提交到 `/api/v1/services/audio/asr/transcription`，携带异步和 OSS 解析请求头以及 `parameters`，取得任务 ID 后查询 `/api/v1/tasks/{task_id}`，确认任务及子任务成功后下载 JSON 并读取 `transcripts[].text`。结果下载和 OSS 上传不携带百炼 API Key。没有将 Filetrans 当成同步聊天请求。

使用步骤：选择 Qwen → 模型类型选 ASR → 填写密钥并启用保存 → 在供应商卡片上传短语音试用 → 在机器人多模态设置中选择该 ASR 并启用短语音转写。后台试用与 QQ 收到的语音共用转写链路。仍限制8 MB、60秒，不因供应商支持长录音而放开机器人限制。Filetrans 总预算55秒、每2秒查询一次、最多20次，不自动重试提交；超时后上游可能继续执行并计费，不自动补发结果。QQ 消息详情记录任务 ID、处理阶段、任务状态与返回用量，后台媒体试用最多80秒。

**临时上传仅供开发试用**：音频会存储在百炼临时空间，官方说明48小时后自动删除；本项目不宣称即时删除云端文件，也不会记录临时签名 URL。正式部署应改用自己的 OSS 托管方案，当前尚未接入自有 OSS 配置。

本次无新增依赖或迁移，未调用真实模型、未执行测试或浏览器验证。重启后端并刷新页面才能加载新的 Filetrans 分流。

官方参考：[Filetrans HTTP 接口](https://help.aliyun.com/en/model-studio/fun-asr-recorded-speech-recognition-http-api)、[百炼临时文件上传](https://help.aliyun.com/en/model-studio/get-temporary-file-url)、[千问兼容聊天接口](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)、[DeepSeek 接入](https://api-docs.deepseek.com/)。

## 定时组合消息

定时任务现可选择文字、文字＋图片、语音＋图片，组合按两条消息依次发送；语音朗读任务文案，执行时调用所选 TTS，私聊自动转 SILK。PNG/JPG 图片最多3.5 MB，保存于任务配置供以后执行。部分失败不自动重复发送，详见 [定时任务](docs/scheduled-tasks.md)。

更新时停止服务，执行 `.\.venv\Scripts\python -m alembic upgrade head`（新增 `0013_greeting_media`），再启动并刷新后台。无新增依赖或环境变量。生成文案已区分错误原因并调整输出预算、超长裁剪；未执行付费接口或真实组合消息验收。

### 狸花猫问候插画素材

`图片/` 保存参考同一只狸花猫生成的 100 张可爱绘本插画（`kitten_001.png` 至 `kitten_100.png`，1254 × 1254 PNG）。`图片/greetings.json` 包含图片相对路径、姿势描述、早晚问候文案、生成提示和可用状态；该文件是素材清单，不是后台接口导入格式。定时任务可在「发送内容」选择「预设 1 · 小猫随机图片＋AI 文案」接入这些素材，每次执行随机选择一张，并由聊天模型结合姿势和时段生成文案；支持早、午、晚，随机允许重复，生成失败不发送且不自动重试。部署时须保留整个 `图片/` 目录，详见 [定时任务](docs/scheduled-tasks.md)。`图片/validation.json` 记录文件尺寸、大小和 SHA-256 校验结果。
定时任务另支持「预设 2 · 小猫随机图片＋AI 语音」：选择 TTS 供应商和音色，每次随机选图、生成配套文案并合成语音，先发语音再发图片，私聊自动转 SILK。会产生聊天模型和 TTS 费用；准备失败不发送，自动执行失败仍占当天次数，手动执行可再次点击发送。与预设 1 共用 100 张素材，无需新迁移，详情见 [定时任务](docs/scheduled-tasks.md)。

## 定时消息流程与模板

后台定时任务可「套用模板」或独立选择文案来源（固定 / AI）、图片来源（无 / 上传 / 素材库随机）、输出方式（文字 / TTS）。预设 1、2 是相同流程的两份配置，新增同类组合无需修改执行代码。模板和素材库注册在 `config/scheduled_templates.json`，保存任务时持久化 workflow 快照；旧任务兼容转换，无需迁移。

部署需同步 config 目录和素材目录。编辑配置后重新打开任务窗口即可加载模板；已保存快照不随模板改动，素材库内容按当前清单读取。当前支持线性消息流程，未提供任意节点拖拽或条件分支；被动聊天链路保持现状。格式和扩展方式见 [定时任务](docs/scheduled-tasks.md)。

手动「执行一次」可重复发送需升级迁移 `0014_manual_greeting_runs`（停止服务后执行 `.\.venv\Scripts\python -m alembic upgrade head`）。自动定时任务仍每日一次；手动每次建立独立记录，AI/TTS 每次重新执行。

## 智能体独立身份目录

角色定义保存在数据库 `bot_roles`，在「机器人管理 → 身份角色」新增、编辑、删除。支持自定义编号、名称、关系说明和可选预设称呼；有用户绑定的角色不能删除，编号不可修改。小苍和大肥鱼只是初始配置；新智能体自行添加角色。先停止旧服务并执行 `.\.venv\Scripts\python -m alembic upgrade head`（新增 `0015_bot_roles`），再启动并刷新后台；以后编辑角色无需重启。详见 [后台与模型](docs/admin-and-models.md)。旧 JSON 已固化为迁移快照，运行时不再读取配置文件。


## 用户与群备注（已编写，待界面验收）

后台「用户与群备注」按机器人维护通讯录。用户备注在相同 OpenID 的私聊/群聊会话中共享；群备注单独保存，并显示在聊天记录、主动发送及定时任务对象中。不同机器人 AppID 的 OpenID 不通用，私聊与群聊的用户标识不保证相同，不按 QQ 号或姓名自动合并。身份角色保持独立。

更新前备份 conversations 和版本信息，停止服务后执行 `.\.venv\Scripts\python -m alembic upgrade head`（新增 `0016_contact_notes`），启动后强制刷新后台。无冲突的旧用户备注自动导入；冲突旧备注保留，重新保存后统一。清除用户备注会停用同 OpenID 会话的定时任务；清除群备注不影响任务。详见[后台与模型](docs/admin-and-models.md)。

## 同一对象的多条定时任务

「定时任务 → 新建任务」可以为已有任务的对象继续新增。每条新任务填写独立名称、文案时段和发送设置，已有早午晚配置保留在列表中。需要先备份并执行迁移 `0017_independent_tasks`，再重启后端、强制刷新后台；详见 [定时任务](docs/scheduled-tasks.md)。

## 定时任务触发规则更新

文案时段新增「其他时段」，AI 参考实际北京时间，适合不限早午晚的关心和提醒。保存只更新配置；只有点击「执行一次」或后续到达设定分钟才触发发送。保存时已到或已过的时间不补发，服务错过时间也不补发。部署需备份后迁移至 0018_schedule_timing，再重启后端并刷新后台。

## 对话页面

原「发送消息」菜单现为「对话」。选择机器人后，通过「私聊 / 群聊」页签查找用户或群，右侧查看历史消息与机器人回复，底部直接发送文字；Enter 发送、Shift + Enter 换行，图片/语音按钮沿用上传和 TTS 试听窗口。发送以机器人身份到 QQ，不调用聊天模型，仍受平台主动消息权限限制。用户头像按机器人 AppID 与 OpenID 加载，机器人使用官方头像；名称优先显示后台备注，其次平台推送的昵称及绑定名称。语音消息展示已有 ASR 转写或 TTS 文本，不保存音频文件、不提供历史播放，历史缺失文本不补造。部署前停止服务、备份会话表，执行 `python -m alembic upgrade head` 升级至 `0019_user_nickname`，再重启服务并强制刷新后台；交互和真实发送待验收。
