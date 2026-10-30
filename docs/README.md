# 模块文档索引

模块文档最初于 2026-10-25 按工作区代码整理，后续核对范围以各文档说明为准；2026-10-26 新增数据库表结构指南，已只读核对本地实际结构至 `0015_bot_roles`。这些说明不代表测试或真实平台验收通过。启动、环境变量及历史更新见[项目 README](../README.md)，开发约定见[AGENTS.md](../AGENTS.md)。

## 按功能查阅

| 要处理的功能 | 优先阅读 | 主要代码 |
| --- | --- | --- |
| Docker 镜像、Compose、部署网络与更新 | [Docker 部署](docker-deployment.md) | Dockerfile、compose.yaml、.env.example |
| 智能体链路、人设、上下文、回复与 token 提取 | [智能体链路与会话](agent-flow.md) | chat.py、recording.py、model.py |
| 图片/GIF、ASR、Filetrans、TTS、自动语音 | [多模态处理](multimodal.md) | multimodal.py、vision.py、asr_filetrans.py、auto_voice.py |
| 网页语音通话、实时 ASR、供应商配置、插话和通话记录 | [语音通话](voice-calls.md) | voice_calls.py、voice_call_routes.py、voice_call_store.py、static/voice-calls.js |
| 定时任务、可组合流程与模板、绑定用户选择、每日去重、执行一次 | [定时任务](scheduled-tasks.md) | greetings.py、greeting_workflows.py、proactive.py |
| QQ WebSocket/Webhook、重连、多机器人启停、队列 | [QQ 接入与运行](qq-runtime.md) | gateway.py、qq.py、bots.py、server.py |
| 管理员登录、供应商、模型类型、家庭身份和接收人绑定 | [后台与模型](admin-and-models.md) | admin.py、security.py、static/ |
| 对话页、私聊/群聊历史、手动发文字/图片/语音、发送幂等 | [主动发送](proactive-messages.md)、[存储与记录](storage.md) | static/conversation.js、static/conversation.css、proactive.py、storage.py |
| 聊天记录、数据隔离、token 汇总、表职责与版本定位 | [存储与记录](storage.md) | storage.py、migrations/versions/ |
| Token 统计菜单、机器人/对话/模型筛选、趋势与排行 | [存储与记录](storage.md)、[后台与模型](admin-and-models.md) | storage.py、admin.py、static/token-stats.js |
| 数据库表、字段、索引、外键、隔离规则与 SQL 排查（Agent） | [数据库表结构指南](database-schema.md) | storage.py、migrations/versions/ |
| 数据库初始化、升级命令、权限、迁移失败处理 | [数据库迁移](database-migrations.md) | alembic.ini、migrations/ |

## 阅读与维护顺序

先读相关模块说明，再阅读列出的实际代码；跨模块修改继续读文内关联链接，不必每次加载全部文档。例如定时发送从 scheduled-tasks.md 开始，涉及媒体发送再读 proactive-messages.md，涉及持久化再读 storage.md。

[admin-plan.md](admin-plan.md) 和 [multimodal-plan.md](multimodal-plan.md) 保留设计背景、历史阶段和待验收清单。维护当前实现先读模块文档，具体行为以工作区代码为准；不要把历史草案或未完成验收写成现有能力。

功能行为、入口或约束变化时更新对应模块文档；新增模块同步维护本索引和根 AGENTS.md 的查阅表。启动/环境变量变化仍更新项目 README 和 .env.example。文档不作为业务提示词。

身份角色目录的配置与扩展请读 [后台与模型](admin-and-models.md)：入口 `qbot/identity_roles.py`（输入校验）和 `qbot/storage.py`（数据库 CRUD），通过后台「机器人管理 → 身份角色」维护 `bot_roles`，按 bot_id 隔离，自定义角色不依赖固定枚举。
