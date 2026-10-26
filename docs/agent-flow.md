# 智能体链路与会话

按当前代码整理，未代表真实 QQ 或模型验收通过。导航见 [文档索引](README.md)。

## 入口与职责

| 文件 | 职责 |
| --- | --- |
| [__main__.py](../qbot/__main__.py) | 本地 chat、serve 和配置辅助命令 |
| [server.py](../qbot/server.py) | 消息标准化、Dispatcher 入队消费、整体期限和发送策略 |
| [bots.py](../qbot/bots.py) | 每个机器人的独立实例、客户端、队列和会话 |
| [recording.py](../qbot/recording.py) | RecordedChat：供应商、身份、视觉分析及记录状态 |
| [chat.py](../qbot/chat.py) | ChatService：提示词、LangChain 调用和内存历史 |
| [model.py](../qbot/model.py) | DeepSeek / OpenAI 兼容聊天客户端 |
| [token_usage.py](../qbot/token_usage.py) | 提取供应商实际 token 用量 |
| [prompts/](../qbot/prompts) | system.txt 人设、vision.txt 图片描述、greeting.txt 问候生成 |

## QQ 消息流程

1. WebSocket / Webhook 接收群聊 @ 消息或 C2C 私聊，转换为统一消息对象。
2. Dispatcher.accept 检查时间、去重、限流和队列容量；后台模式先持久化记录再入队。
3. Dispatcher.run 顺序取出消息，检查排队期限，后台模式预处理文字、表情及附件。
4. RecordedChat 读取当前机器人绑定的聊天供应商或全局默认；有图片时先调用独立视觉模型生成摘要。
5. 读取管理员绑定身份，ChatService 组装人设、身份、历史、当前文字及视觉摘要并调用模型。
6. 保存生成结果，再由发送回调选择文字或语音。回调成功后才更新内存历史，最后保存记录状态。

每个机器人独立顺序消费，ChatService 锁覆盖生成和发送。当前没有工具调用循环。附件全部失败且无可用文字时直接报告原因，不调用聊天模型。

## 提示词与配置

提示词顺序：系统人设 → 可选身份信息 → 多模态数据边界说明 → 历史 → 当前用户消息。视觉摘要作为用户资料进入当前消息；原图只交给视觉模型，后续历史不保留原图。README、AGENTS.md 和模块文档不进入业务提示词。

后台每次请求从数据库读取供应商并解密密钥，创建模型，结束后关闭客户端；供应商修改下次请求生效。机器人人设在运行实例创建时加载，保存机器人重载对应实例；默认 system.txt 修改后需重启或重载。无后台模式使用环境变量，后台缺少供应商时不回退到环境凭证。

本地 chat 复用聊天服务，但不经过 QQ 附件和发送流程。

## 会话与时限

先按 bot_id 运行实例隔离，再按来源、群和用户隔离。默认闲置 1800 秒过期、保留最近 10 轮、最多 1000 个会话，具体配置见 config.py。`/reset` 清空当前会话，不删除数据库记录；重载机器人和进程重启会清空对应内存历史。

家庭身份按 bot_id + user_id 共享，身份变化后下一次对话清理该会话旧身份上下文；身份角色不授予管理权限。数据库记录尚不用于重启恢复模型历史或重放队列。

后台聊天客户端超时 45 秒、不自动重试，ChatService 还受 CHAT_TIMEOUT_SECONDS 限制；QQ 主流程最多 120 秒（含排队），并受平台回复窗口约束。视觉、ASR、TTS 子时限见[多模态](multimodal.md)。

## 排查与修改

查不到接收记录先读[QQ 接入](qq-runtime.md)；有记录但回复异常，按 Dispatcher → RecordedChat → ChatService → 模型配置排查。区分 expired、model_failed、send_failed、delivery_unknown；网络超时不等于未送达，发送开始后不盲目补发。

用量只记录实际返回值，缺失保持未知，输入与输出均有值时可相加；不估算费用。持久化和汇总见[存储](storage.md)。

## 模型连接生命周期修正（已编写，待真实调用验收）

已安装 LangChain 会缓存默认 HTTP 客户端，与项目每次调用后关闭客户端的策略冲突。模型工厂显式创建独立同步和异步 HTTP 客户端，由当前模型调用结束时释放，不关闭其他调用共享的连接。覆盖问候、聊天、视觉和供应商试用。未调用真实模型验收。
