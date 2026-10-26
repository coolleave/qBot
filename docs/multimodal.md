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

部分失败明确告知；除下述未识别语音的人设澄清路径外，全部失败且无文字不调用聊天模型。`/reset` 和超长输入跳过附件处理。

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

## 语音口语与首尾缓冲（已实现，待实际试听）

普通聊天在生成前根据 private_reply / group_reply 和语音输入决定是否加入语音口语提示；与定时任务 AI＋TTS 共用提示词。只直接说话，不插入“从纸箱里探出脑袋”等角色动作、姿态或神态描述，不把动作改成“我正在……”继续朗读；图片仅用于把握问候氛围。用户明确询问图片内容时仍可正常解释图片，不影响正常问答。文字模式不加这项约束，手写 TTS 文本不自动改写。

合成后的完整 PCM 在封装 WAV 前增加 200 ms 开头静音、350 ms 结尾静音，原音频边缘仅做 5 ms 的淡入淡出以减轻波形突变；不裁剪原音频、不做长时间淡出，以免吞掉首尾字音。按处理后的总字节数检查 3.5 MB 上限，预留缓冲空间。统一作用于后台试听、定时语音和自动语音，私聊在处理后转 SILK。无需新增依赖或数据库迁移。该处理改善播放缓冲和边缘突变，不能修复供应商已经漏字的音频；实际听感待试听。

实现由 Dispatcher 将语音偏好传给 RecordedChat / ChatService，使用 LangChain 消息模板的 output_style 注入共享口语提示词；最终发送仍按内容长度、时间预算和失败回退策略决定。synthesize 统一处理 PCM 边缘后封装 WAV，因此后台试听也可直接对比。已通过 Python AST 和差异静态检查；按约定未新增或运行测试，未调用付费 TTS 或发送消息验证。

## 未识别语音的自然回复（已实现，待真实联调）

空白语音、ASR 成功但正文为空，以及转写服务失败/取消，改为通过既有聊天模型按人设简短说明“刚才没听清你说什么”，请用户重说或发文字。不能编造转写、推测用户意图，不能把失败声称为“用户没说话”。明显全零 PCM 在本地判空，不调用 ASR；其它识别失败保留真实错误类别和任务状态，不误归类为空语音。

识别失败的技术详情仍保存在附件诊断中，不拼入对用户回复或模型输入。普通下载/类型/权限校验仍保持原错误路径。没有其他内容时只提供未识别语音事件；同时有文字/图片时处理可用内容并简短说明没听清语音。回复走原聊天人设、身份与用量记录，沿用文字/跟随/语音策略，额外最多一次原聊天调用；没有时间或模型也失败时使用简短自然的文字兜底，不伪装为模型输出。不新增重试或迁移，真实空语音和供应商失败联调待验收。

实现使用 NoSpeechError 区分空正文，附件诊断 recognition_reason 标记 no_speech / asr_unavailable；audio_unrecognized 经 Dispatcher / RecordedChat 传入 ChatService 的 recognition_notice 消息模板，业务提示词为 prompts/asr_clarification.txt。Python AST 和差异静态检查通过，未新增或运行测试，未调用付费 ASR/聊天/TTS 或实际发送验证。

部署核对：2026-10-26 恢复服务前数据库已处于 0015_bot_roles，本次只备份其当前状态并核对 2 条身份均有有效关联，未重复导入。空语音回复已加载，健康接口正常；真实 ASR/模型回复效果仍待联调。
