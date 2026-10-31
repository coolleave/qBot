# 项目开发约定

## 项目背景

这是个人维护的 Python + LangChain QQ 对话智能体项目，使用 QQ 开放平台 API。优先采用简单、清晰、易调试的实现，避免过度设计。

## 工作方式

- 开始修改前，阅读 README.md 和相关代码。
- 先完成当前需求，不主动增加无关功能。
- 需求有小范围空白时，采用简单合理的方案并说明；涉及核心功能歧义或不可逆操作时先询问。
- 不覆盖或撤销用户已有的修改。
- 使用中文解释改动和关键设计。

## 按功能查阅模块文档

开始修改时先读 README.md，再按需求读取下表文档及其中的代码入口；跨模块功能继续阅读关联文档，无需每次加载全部文档。完整导航见 [docs/README.md](docs/README.md)。

| 需求涉及 | 优先阅读 |
| --- | --- |
| Docker 镜像、Compose、部署网络与更新 | [docs/docker-deployment.md](docs/docker-deployment.md) |
| 智能体链路、人设、模型调用、上下文、回复 | [docs/agent-flow.md](docs/agent-flow.md) |
| 持久化记忆、异步整理、成本预算、群聊主人优先（待验收） | [docs/memory-system.md](docs/memory-system.md) |
| 图片/GIF、ASR/Filetrans、TTS、自动语音回复 | [docs/multimodal.md](docs/multimodal.md) |
| 网页语音通话、实时识别、流式播报、通话配置和记录 | [docs/voice-calls.md](docs/voice-calls.md) |
| 定时任务、早晚问候、每日去重、执行一次、文案生成、实际时间与天气 | [docs/scheduled-tasks.md](docs/scheduled-tasks.md) |
| QQ 接入、WebSocket/Webhook、多机器人、队列与重连 | [docs/qq-runtime.md](docs/qq-runtime.md) |
| 后台登录、供应商、模型类型、身份及接收人绑定 | [docs/admin-and-models.md](docs/admin-and-models.md) |
| 对话页面、私聊/群聊历史、主动发送、图片/语音上传、发送幂等 | [docs/proactive-messages.md](docs/proactive-messages.md)、[docs/storage.md](docs/storage.md) |
| 聊天记录、token 用量、数据隔离、表结构定位 | [docs/storage.md](docs/storage.md) |
| Token 统计独立菜单、筛选、趋势与排行 | [docs/storage.md](docs/storage.md)、[docs/admin-and-models.md](docs/admin-and-models.md) |
| 数据库表、字段、索引、外键、身份隔离及 SQL 排查 | [docs/database-schema.md](docs/database-schema.md) |
| 数据库升级、迁移权限和失败处理 | [docs/database-migrations.md](docs/database-migrations.md) |

现有 admin-plan.md、multimodal-plan.md 保留历史方案与待验收项；当前实现先看模块文档并核对代码，不把计划当成已支持。功能变化同步更新对应模块说明，新增模块同时更新此表和 docs/README.md。以上文档均不作为业务提示词。

## 代码规范

- 文件统一使用 UTF-8 编码。
- Python 函数、变量和模块使用 snake_case，类名使用 PascalCase。
- 关键函数添加类型注解；注释解释复杂逻辑，不逐行翻译代码。
- 不静默吞掉异常，不为了可能的扩展提前建立复杂抽象。
- 优先复用已有代码，再考虑新增模块和依赖。
- 如涉及 Java，适当添加注释，不执行编译检查。
- 不主动新增测试文件或测试类；仅在用户明确要求测试时按需求补充。

## 智能体开发约定

- 使用与项目依赖版本匹配的 LangChain API。
- 模型配置、业务提示词和工具函数分别管理；README.md 面向维护者，AGENTS.md 面向 AI 编程工具，不作为业务提示词传入模型。
- 后台模式下模型供应商和 QQ 凭证由后台管理，密钥从数据库解密读取；机器人配置保存后仅重载对应机器人，模型供应商配置下次请求生效。无后台模式保留环境变量配置，加密主密钥仍放本地环境变量。不将凭证写进代码或日志，`.env` 不提交到 Git。
- 明确区分不同会话的消息记录，首先按机器人 bot_id 隔离，群聊再按群和用户隔离，数据库去重索引必须包含 bot_id。
- 模型及外部工具调用设置超时、重试和调用次数上限。
- 工具失败要明确报告，不能伪造执行成功。
- 有副作用的操作由代码校验权限和执行条件，不仅依赖提示词约束。

## 后台与数据库约定

- 后台与 MySQL 开发按 `docs/admin-plan.md` 执行，未实现的内容保持标注为计划。
- 复用用户已启动的 Docker MySQL，不自行创建或替换 MySQL 容器，不操作其他业务库。
- 结构变化通过 Alembic 手写迁移；已执行的版本及快照不可修改，新增版本完成变更。应用启动不自动迁移，不用 stamp 跳过错误。
- 数据库使用 utf8mb4；建库建表需可重复执行，不以删库删表方式初始化。
- 管理接口在服务端校验管理员身份，密钥加密存储、掩码展示，不能通过查询接口返回明文。

## 验证要求

- 仅在用户明确要求时运行测试，不在日常修改后自动执行测试；Java 不执行编译检查，前端代码不打开浏览器验证。
- 修复缺陷时也不自动新增或运行回归测试，等待用户明确要求。
- 普通测试优先模拟模型，不默认调用付费接口。
- 没有实际执行的检查，不得声称已经通过。

## 文档维护

- 功能变更优先先更新相关模块文档，注明拟实现行为与待验证状态，再编写代码；完成后核对并同步文档，不能遗漏。

- README.md 和 AGENTS.md 初期保持短小，后续按实际需要补充。
- 启动命令、配置项或目录变化时更新 README.md，填写实际可用的命令和路径。
- 新增环境变量时更新 `.env.example`。
- 不把尚未实现的功能写成已经支持，完成并验证后才勾选进度。

## 完成后回复

简要说明修改内容、验证结果和仍存在的问题。

## Git 提交规范

- 提交说明使用 `类型: 中文说明`，例如 `feat: 新增聊天记录查询`、`fix: 修复重复消息处理`。
- 按职责拆分提交，使用 feat、fix、docs、test、chore 等类型；相互依赖的代码保持在同一提交中。
- 提交前检查暂存内容，不提交 `.env`、真实密钥、虚拟环境、缓存或 IDE 本地配置。

身份角色目录的配置与扩展请读 [后台与模型](docs/admin-and-models.md)：入口 `qbot/identity_roles.py`（输入校验）和 `qbot/storage.py`（数据库 CRUD），通过后台「机器人管理 → 身份角色」维护 `bot_roles`，按 bot_id 隔离，自定义角色不依赖固定枚举。
