# QQ 接入与多机器人运行

导航见 [文档索引](README.md)，运行命令和配置见[项目 README](../README.md)。

Docker 单实例运行、现有 MySQL 网络和端口映射见 [Docker 部署](docker-deployment.md)。容器健康检查只访问 `/health`，进程退出自动重启，健康检查失败本身不会触发重启；不允许同时运行旧服务或增加副本。配置已编写，容器部署仍待验证。

## 文件与接入方式

| 文件 | 职责 |
| --- | --- |
| [gateway.py](../qbot/gateway.py) | WebSocket 登录、心跳、Resume、重连、事件分发 |
| [qq.py](../qbot/qq.py) | 验签、Access Token、文字及媒体上传发送 |
| [bots.py](../qbot/bots.py) | BotManager / BotRuntime 生命周期 |
| [server.py](../qbot/server.py) | Webhook、标准化、限流、有界队列 |
| [bot_profiles.py](../qbot/bot_profiles.py) | 官方昵称和头像资料 |

默认 WebSocket 主动连接官方网关，无需公网入口；订阅 GROUP_AND_C2C_EVENT，处理 GROUP_AT_MESSAGE_CREATE、C2C_MESSAGE_CREATE。群聊依赖平台推送的 @ 事件，未支持频道私信；回复通过 HTTP API。

Webhook 路径为 `/qq/webhook/{bot_id}`，需要公网 HTTPS 转发与平台事件配置，验签允许时间偏差 5 分钟。WebSocket 模式拒绝对应机器人 Webhook 请求。`/health` 只表示 HTTP 进程正常，不证明 QQ 已连接。

## 生命周期

后台凭证保存在 bots，AppSecret 加密。启动时加载启用机器人，每个实例独立拥有客户端、队列、Dispatcher、ChatService 和可选 Gateway。仅单进程，不启用多 worker。

保存配置会重载对应机器人并清空其内存历史，其他机器人不随之重载；停止保存禁用状态。旧实例停止接收，最多等待队列 10 秒后取消处理任务。队列在内存，未完成记录不重放。BotManager.lock 协调启停和主动发送，避免发送时替换凭证。

供应商配置下一次模型请求生效，一般无需重载机器人。无后台模式从环境变量创建单个机器人实例。

## 去重、限流与恢复

Dispatcher 过滤异常时间及机器人自身消息，检查去重、会话限流和容量。默认每会话每分钟 6 条、Dispatcher 内总量每分钟 30 条、队列 32 条。各机器人有独立 Dispatcher，这些被动接收计数与共享主动发送计数不同。

后台入队确认前落库，用包含 bot_id 的唯一索引去重；队列满、数据库失败返回 503，限流返回 429。无后台仅内存去重。持久化不保证自动执行，进程崩溃后 pending / processing 不自动恢复。

网关有心跳、确认超时、Resume 和退避重连；连续失败达到上限停止重连，排查后重启。恢复状态仅在内存，超出平台事件保留窗口不保证补发。

## 排查

先检查后台运行状态、启用、AppID、接入方式与平台接收范围，再看安全日志和接收记录。有记录后转到[智能体链路](agent-flow.md)；主动消息失败查[主动发送](proactive-messages.md)的平台权限与额度。日志不得输出密钥、完整消息或签名媒体地址。

真实平台联调仍按项目既有验收记录管理，本次文档整理不构成接入验收。

## 群聊仅艾特机器人（代码已编写，待验证）

群聊 @ 事件没有正文且没有附件时，将输入规范化为“【用户在群聊中仅 @ 机器人，没有附加文字】”，正常经过身份、限流、队列与模型回复，简短回应在场并询问需求，不再视为空白消息。兼容纯空白及仅包含平台 <@id>/<@!id> 标记的正文。含图片、语音等附件的消息保持原处理，不把识别失败当作仅艾特。私聊空消息规则不变。WebSocket 和 Webhook 共用入口。

本次仅静态核对差异，未运行测试、浏览器或真实 QQ/模型调用，尚未部署。
