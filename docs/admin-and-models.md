# 后台、模型供应商与用户绑定

导航见 [文档索引](README.md)。[admin-plan.md](admin-plan.md) 保留历史设计和验收记录，本页定位当前实现。

## 后台与登录

[admin.py](../qbot/admin.py) 提供 `/admin`、`/api/admin`；[security.py](../qbot/security.py) 负责密码哈希；[static/](../qbot/static) 使用 Vue 3 + Element Plus 组件及现有业务脚本，无独立构建工程。页面展示登录界面，管理数据与操作由服务端鉴权。

ADMIN_ENABLED 控制后台模式。账号、密码哈希、会话签名密钥、数据库连接和加密主密钥仍来自本地环境；QQ 与供应商凭证由后台管理。

Cookie 签名且启用 HttpOnly / SameSite Strict；HTTPS 配置 ADMIN_COOKIE_SECURE。admin_sessions 保存 Token 摘要、CSRF、账号配置指纹和 UTC 到期时间，固定有效 1 小时。重启不延长，退出删除，账号/密码哈希/签名密钥变更令旧登录失效；登录清理过期会话并最多保留 100 个。

写接口校验 X-QBot-Admin、Origin 同源和 X-CSRF-Token；登录尚无会话，采用来源校验。数据库不可用明确报错。新增路由复用鉴权依赖，不能只隐藏前端按钮。

## 模型类型与协议

### Token 统计菜单（已编写，待验证）

独立页面提供机器人、对话、模型、日期筛选，用量卡片、每日趋势和分组排行；使用原生 SVG/CSS，无外部图表依赖。对话支持关键词搜索及分页，排行可点击筛选。数据接口均要求管理员登录，加载失败清空旧图表并提示，避免把旧筛选结果当成新结果。用量口径和接口见[存储与记录](storage.md)。

| model_type | 当前用途 | 实现 |
| --- | --- | --- |
| chat | 聊天；声明 supports_images 后可作独立视觉模型 | model.py、vision.py |
| asr | 百炼同步短语音及异步 Filetrans | multimodal.py、asr_filetrans.py |
| tts | Qwen Realtime 合成 | tts.py |
| image | 保存图片生成配置 | 尚未接入生成调用 |

api_type 与 model_type 分别表示接口和业务能力。聊天为 DeepSeek 或兼容 Chat Completions；ASR/TTS 使用专用协议，不因表单选择兼容接口就走聊天请求。

全局默认及机器人聊天绑定只允许 chat；机器人可指定或跟随默认，视觉需独立选择。供应商保存不调用模型，下一次请求生效；密钥加密、查询掩码，留空保留。默认/绑定供应商停用或修改类型前需处理引用，后台不回退环境凭证。

DeepSeek / Qwen / 自定义预设仅填表，不自动创建；地域、模型权限和图片支持需确认。更换服务域名需重新填写密钥。

## 连接检查与媒体试听

[provider_probe.py](../qbot/provider_probe.py) 使用已保存供应商发短提示，无需启用或设默认；最多 64 token、25 秒预算、不重试，不写聊天记录或上下文。

POST `/api/admin/providers/{provider_id}/speech` 接收 text、voice，返回 WAV；文本 1～500 字，默认 Bunny。供应商卡片也可上传图片/语音试用，复用视觉/ASR 链路。这些操作会调用真实付费接口。

连接检查和 TTS 试听共用并发及 10 秒间隔限制；具体锁见 admin_router。媒体协议见[多模态](multimodal.md)，试听后发送见[主动发送](proactive-messages.md)。

## 两类绑定

| 绑定 | 范围 | 用途 |
| --- | --- | --- |
| 家庭身份 | bot_id + user_id | 姓名、father/mother/friend 角色，进入模型身份提示 |
| 接收人名称 | 机器人下对应 QQ 会话 | contact_name / contact_qq，供发送和定时任务选择 |

家庭身份在 bot_user_identities，同机器人同 OpenID 的私聊/群聊共享身份；历史不合并，身份变化在下一次对话清理该会话旧上下文。QQ 号只是备注，不作鉴权或合并不同 OpenID。角色不授予管理权限。

接收人绑定不设置家庭角色；解除会停用该会话问候。群会话主动发送给全群。身份查询/保存为 GET `/api/admin/identity-bindings`、PUT `/api/admin/bots/{bot_id}/identity-bindings`；接收人为 GET `/api/admin/user-bindings`、PUT `/api/admin/bots/{bot_id}/user-bindings`。按记录绑定/解绑及参数以 admin.py 为准，必须校验归属与已有真实身份。

## 维护

表单改动通常涉及 admin.html、admin.js、admin.py 和 storage.py；结构变更新增手写迁移。登录问题看会话配置和数据库，模型问题看类型、协议、默认/绑定及启用状态。步骤见[数据库迁移](database-migrations.md)。本次未测试、未开浏览器、未调用真实模型。

## Vue 3 界面重构（已编写，交互待验证）

采用 Vue 3 + Element Plus 渐进式接入：登录、侧边导航、聊天表格与分页、全局提示由组件管理；供应商、多模态、发送和定时任务等现有表单继续复用业务脚本，统一视觉样式。Vue 管理独立挂载点，不接管旧表单 DOM，避免破坏原有事件与 FormData。静态依赖固定版本并随项目分发，不需要 Node 构建或浏览器访问 CDN。接口、鉴权、CSRF 和数据库结构保持现状。本次不运行测试、不打开浏览器，实际交互待人工验收。

组件入口为 `admin-ui.js`，统一主题为 `admin-theme.css`；`admin.js` 负责请求、业务表单和页面切换，`token-stats.js` 保留统计逻辑。Vue 3.5.13、Element Plus 2.9.3 及许可文件保存在静态目录，来源见 `frontend-vendor.txt`。仅重构呈现，不新增接口或迁移。登录、菜单、表格、分页、状态标签与消息提示使用 Element Plus；复杂业务表单和原生 dialog 尚未全面迁移为 Vue 组件。

本次完成源码引用、资源路径和脚本语法静态检查；未运行测试、浏览器、数据库或真实平台联调。更新后强制刷新 `/admin`；Docker 部署需重新构建镜像。

## 大肥鱼主人身份（拟实现，待验证）

身份角色新增 `owner`（主人），原有 father/mother/friend 保持兼容；角色只决定称呼，不赋予后台管理权限。已有 VARCHAR(16) 字段可容纳，无需迁移。聊天详情和身份绑定表单均提供主人选项，模型身份信息同步解释 owner。

智能体 1 的大肥鱼人设补充宇老弟为主人、丁一哥为宇老弟的朋友。实际发送者仍只按该机器人后台绑定的 OpenID 识别，不根据昵称或自称自动绑定。宇老弟绑定 owner，丁一哥绑定 friend；两人的 OpenID 尚待确认。
