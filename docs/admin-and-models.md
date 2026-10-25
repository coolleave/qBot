# 后台、模型供应商与用户绑定

导航见 [文档索引](README.md)。[admin-plan.md](admin-plan.md) 保留历史设计和验收记录，本页定位当前实现。

## 后台与登录

[admin.py](../qbot/admin.py) 提供 `/admin`、`/api/admin`；[security.py](../qbot/security.py) 负责密码哈希；[static/](../qbot/static) 为原生 HTML/CSS/JavaScript，无独立构建工程。页面展示登录界面，管理数据与操作由服务端鉴权。

ADMIN_ENABLED 控制后台模式。账号、密码哈希、会话签名密钥、数据库连接和加密主密钥仍来自本地环境；QQ 与供应商凭证由后台管理。

Cookie 签名且启用 HttpOnly / SameSite Strict；HTTPS 配置 ADMIN_COOKIE_SECURE。admin_sessions 保存 Token 摘要、CSRF、账号配置指纹和 UTC 到期时间，固定有效 1 小时。重启不延长，退出删除，账号/密码哈希/签名密钥变更令旧登录失效；登录清理过期会话并最多保留 100 个。

写接口校验 X-QBot-Admin、Origin 同源和 X-CSRF-Token；登录尚无会话，采用来源校验。数据库不可用明确报错。新增路由复用鉴权依赖，不能只隐藏前端按钮。

## 模型类型与协议

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
