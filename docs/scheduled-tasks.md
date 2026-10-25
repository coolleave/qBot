# 定时任务与问候文案

当前是单进程早晚问候调度，不是通用 cron 系统；真实 QQ 主动发送待验收。导航见 [文档索引](README.md)。

## 配置与入口

后台先在「用户绑定」绑定已有 QQ 接收对象的名称，再在「定时任务」配置 morning / evening：启用、发送时间、最晚执行时间、星期和 1～200 字固定文案。

北京时间 UTC+8，星期为 1～7，窗口包含开始、不含结束；不支持跨午夜，共有星期内启用窗口不能重叠。默认窗口 07:00～10:00、21:00～23:30，默认关闭。

| 文件 | 关键入口 |
| --- | --- |
| [greetings.py](../qbot/greetings.py) | GreetingRule / GreetingInput、默认规则、时间换算 |
| [server.py](../qbot/server.py) | lifespan 在后台模式启动并关闭调度任务 |
| [proactive.py](../qbot/proactive.py) | ProactiveSender.run_schedules / send_scheduled |
| [storage.py](../qbot/storage.py) | due_greetings、claim_scheduled_greeting、规则和历史 |
| [greeting_generation.py](../qbot/greeting_generation.py) | 按人设生成草稿 |
| [admin.py](../qbot/admin.py)、[admin.js](../qbot/static/admin.js) | 配置、执行一次、生成文案 |

## 扫描与每日领取

每轮最多扫描 100 条到期且当天未领取任务，逐条发送，结束后等待 10 秒；发送耗时影响执行时间，不保证精确到秒。恢复服务时仍在窗口内的未领取任务会执行，过期不补发。

claim_scheduled_greeting 在事务中锁定、复核规则与接收人，同时创建独立聊天记录和每日领取记录。唯一范围包含机器人、任务和北京时间日期。领取后才检查机器人运行与限流，因此未运行、限流、失败、送达未知都会消耗当天次数，不自动重试。修改配置、重启或 `/reset` 不清除当天领取。

定时与手动发送共享主动限流和 BotManager 锁；QQ 发送预算 25 秒。固定文案不调用模型、不进入聊天上下文、不携带旧 msg_id。私聊发给用户，群聊发给全群；同群不同成员会话分别配置会重复发群消息。

解除接收人名称绑定或更换机器人 AppID 会停用相关任务。中断留下的 processing 记录不重放。表名仍为 interaction_greetings / interaction_greeting_runs，但任务已不依赖用户聊天触发。

## 执行一次与生成草稿

「执行一次」使用已保存文案，跳过启用、星期和窗口限制；仍检查接收人、运行状态和限流，占用当天同一任务额度。当天已领取返回已有结果，包括失败结果。

「按人设生成文案」读取最新保存的人设及聊天供应商，私聊可参考姓名和角色，群聊面向全群。返回草稿后需保存；生成不保存任务、不发送、不占每日次数、不进入聊天上下文，不会每天自动重新生成。

生成可能收费，最多一次模型调用、不自动重试；模型超时 30 秒，生成预算 35 秒；后台同一时间最多生成一条，开始间隔至少 10 秒。失败保留原草稿。

## 接口与排查

以下后缀位于 `/api/admin/bots/{bot_id}/records/{record_id}`：

| 方法 / 后缀 | 用途 |
| --- | --- |
| GET / PUT `/greetings` | 读取规则、最近记录 / 保存两条规则 |
| POST `/greetings/{morning或evening}/run` | 执行一次 |
| POST `/greetings/{morning或evening}/generate` | 生成草稿 |

均校验管理员与记录归属，写操作校验 CSRF。未执行先看后台模式、服务、接收人、北京时间窗口和当天领取记录；已领取查看关联聊天记录状态。delivery_unknown 需核对 QQ，不能直接重发。平台权限及发送基础设施见[主动发送](proactive-messages.md)。
