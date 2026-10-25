# 多模态输入与自动语音回复

按当前代码整理，真实模型和 QQ 格式兼容性待验收。导航见 [文档索引](README.md)，背景与未完成验收项见[原方案](multimodal-plan.md)。

## 文件分工

| 文件 | 职责 |
| --- | --- |
| [multimodal_config.py](../qbot/multimodal_config.py) | 开关、供应商及私聊/群聊策略 |
| [multimodal.py](../qbot/multimodal.py) | 表情、下载、解码调用、同步 ASR、消息准备 |
| [media_worker.py](../qbot/media_worker.py) | 隐藏子进程解码与资源预算 |
| [asr_filetrans.py](../qbot/asr_filetrans.py) | 临时上传、异步转写、轮询、结果读取 |
| [vision.py](../qbot/vision.py)、[recording.py](../qbot/recording.py) | 独立视觉摘要，再交给聊天模型 |
| [auto_voice.py](../qbot/auto_voice.py) | 自动语音条件与准备 |
| [tts.py](../qbot/tts.py)、[audio_codec.py](../qbot/audio_codec.py) | Qwen Realtime WAV 合成及私聊 SILK 转码 |
| [server.py](../qbot/server.py) | 整体期限、最终发送与回退 |

## 配置与输入链路

仅后台模式接入，默认关闭图片/GIF/ASR，默认文字回复；配置在 bots.multimodal。GIF 要先启用图片，ASR 需 asr 供应商，自动语音需 tts 供应商。私聊/群聊分别选择 text（文字）、follow（收到语音时语音回复）、voice（优先语音）。

图片需独立绑定支持图片的兼容 chat 供应商：下载解码 → 构造图片块 → 视觉模型提取内容 → 聊天模型结合身份、人设、上下文回复。聊天模型不必支持图片。每条图片/GIF 最多一次视觉调用加一次聊天调用，不自动重试；视觉失败明确说明，不编造。

摘要进入当前及后续文字历史，原图仅本轮视觉调用使用。语音先转 WAV 再 ASR，转写拼入用户文字。QQ 表情用 ext.text 名称，否则保留未知 ID；Emoji 不变。这些内容都是用户资料，不提升为系统指令。

## 限制与安全

| 项目 | 限制 |
| --- | --- |
| 附件 | 前 4 个，最多 3 张图、1 段语音 |
| 体积 | 单个 8,000,000 字节，合计 16,000,000 字节 |
| 图片 | PNG/JPEG/WebP/GIF，单图 2000 万像素 |
| GIF | 最多 4 帧，总图片输入 8 帧；扫描 100 帧、累计 1 亿像素，超预算仅首帧并提示 |
| 音频 | WAV/MP3/FLAC/Ogg/SILK，最多 60 秒，编码兼容性待实际验收 |
| 下载 | 单附件 10 秒，总预算 20 秒 |
| 解码 | 图片 5 秒，音频 10 秒 |
| 并发 | 预处理、视觉与自动合成共享 2 个媒体名额 |
| 视觉 | 阶段 30 秒，客户端 25 秒 |
| QQ 总流程 | 120 秒含排队，另受平台回复窗口约束 |

下载只允许 QQ CDN（qpic.cn、qq.com、gtimg.cn 及子域，以及精确主机 qqbot.ugcimg.cn、multimedia.nt.qq.com.cn），校验公网解析、固定 IP/TLS 主机名并复核重定向，不携带 QQ 凭证。HTTP（含显式80端口）地址仅升级 HTTPS，不降级传输。原媒体、Base64、完整 URL 不入聊天记录，转写和处理详情会保存；附件详情仅记录 source_host/source_scheme 用于排查来源，不保存路径和签名参数。

部分失败明确告知，全部失败且无文字不调用聊天模型。`/reset` 和超长输入跳过附件处理。

## ASR 协议

同步支持 Qwen-Audio-3.0/3.1-ASR-Flash、Fun-ASR-Flash 系列，走百炼原生 multimodal-generation 接口，25 秒预算，不是兼容聊天接口。

Filetrans 支持 qwen-audio-3.0-asr-flash-filetrans 和 qwen-audio-3.1-asr-flash-filetrans：申请临时上传 → 上传 WAV → 提交 transcription 异步任务 → 每 2 秒轮询最多 20 次 → 检查主/子任务 → 下载 JSON。总预算 55 秒，不重试提交；超时上游可能继续运行和计费，不自动补发。详情记录任务 ID、阶段与状态，不保存签名地址。

Filetrans 会把音频存到供应商临时空间，本地不持久化不等于云端不保存。原 README 引用官方 48 小时自动删除说明，项目没有即时删除逻辑。当前临时上传用于开发试用，自有 OSS 配置未接入。OSS 上传/结果下载不携带百炼 API Key，地址限制见 asr_filetrans.py。

## TTS 与自动回复

qwen3-tts-flash-realtime 通过专用 WebSocket 输出 24 kHz 单声道 16 位 PCM WAV，默认 Bunny；支持预设音色，VD/VC 设计与复刻未接入。合成 60 秒预算、不重试，音频最多 3.5 MB。

自动语音要求不超过 300 字、无代码块/链接/表格、满足策略、无附件警告且剩余至少 80 秒。私聊转 SILK，群聊 WAV。准备预算最多 65 秒；合成、转码或上传失败回退文字，最终发送开始后不补发文字，避免重复。最终媒体发送 10 秒，超时记送达未知。

后台试听可到 500 字，与自动 300 字策略不同；手动发送、试听、定时固定文字各走独立流程，见[主动发送](proactive-messages.md)。

## 部署与排查

依赖见 pyproject.toml / constraints.txt；结构始于 0010_multimodal，后续迁移也要完整升级。维护者停止服务、更新依赖、显式迁移再启动，命令见[项目 README](../README.md)，本次不执行部署。

检查开关、供应商类型/启用/独立视觉绑定，再查看 attachments、vision、reply_kind、fallback_reason 和 ASR 阶段。图片声明不证明模型可用，供应商试用会产生真实调用。子阶段均受外层期限约束，不能把子时限相加当作整体预算。
