# 后台主动发送

导航见 [文档索引](README.md)。每日任务领取单独见[定时任务](scheduled-tasks.md)。

## 使用边界

从「发送消息」或聊天记录详情选择已有 QQ 接收对象。服务端依据记录确定目的地，检查 bot_id、管理员、CSRF、机器人运行状态，不接受任意 QQ 号。私聊发给用户，群聊发全群。

文字不调用模型、不写内存上下文。主动发送不带旧 msg_id，受 QQ 权限、额度、接收范围和内容限制，不能通过按钮绕过。

## 代码和接口

| 位置 | 职责 |
| --- | --- |
| [proactive.py](../qbot/proactive.py) 的 ProactiveSender.send | 运行状态、目的地、限流、幂等与发送 |
| [media.py](../qbot/media.py) | 文件头、类型、体积校验及摘要 |
| [qq.py](../qbot/qq.py) | send_proactive、upload_media、send_media |
| [audio_codec.py](../qbot/audio_codec.py) | 私聊 WAV 转 SILK |
| [admin.py](../qbot/admin.py)、[admin.js](../qbot/static/admin.js) | API 与页面交互 |

POST `/api/admin/bots/{bot_id}/records/{record_id}/send` 接收 content、UUID request_id；文字受 MAX_REPLY_CHARS 限制。相同前缀下 `/send-media` 接收原始文件字节，查询参数 kind、filename、request_id。

媒体单文件最多 3,500,000 字节，限制读取量和上传时间，不接受任意路径/URL。图片 PNG/JPG；群语音 SILK/MP3/WAV，私聊 SILK，受支持 PCM WAV 自动转码。无录音功能。

## 幂等与阶段

发送期间持有 BotManager.lock，避免启停替换凭证；手动和定时共用主动每目标/全局限流，与被动接收独立。

先以 `admin-send:` 加 request_id 创建独立记录，按数据库机器人及会话范围去重。重复请求返回原结果，不再发送；同标识用于不同内容报冲突。网络出错前端保留标识，不能每次重试都换 ID。

先保存 answer，再发送：QQ 确认是 replied，拒绝是 send_failed，最终发送超时/网络异常是 delivery_unknown。中断留下的待处理记录不重放；核对 QQ 后再决定是否创建新请求。

媒体先以 srv_send_msg=false 上传 users/groups files，预算 60 秒，再以 msg_type=7 和 file_info 发送，预算 25 秒。上传失败尚未执行发送，上传成功不代表送达；file_info 不跨机器人或来源缓存。

## TTS 与数据保留

语音窗口可选启用的 TTS 生成 WAV 试听，发送复用音频，不再次合成；修改文本、音色、供应商需重新生成。私聊 WAV 经隐藏子进程转 SILK，15 秒预算、不重试；支持单声道 16 位 PCM、8/12/16/24 kHz，不支持私聊 MP3。协议及自动回复策略见[多模态](multimodal.md)。

记录保存文件名称、大小、摘要及 SHA256，不存原文、Base64、file_info、临时地址，不支持历史重新下载。去重仍按原始 WAV 摘要。排查时区分校验、转码、上传与最终发送。本次未上传或发送文件，真实平台验收待完成。
