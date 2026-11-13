# 文件知识库

状态：2026-11-08先文档后代码；2026-11-09已在实验室服务器安装依赖、迁移至0031并部署。服务健康与目录、连接检查通过，真实文件解析和Embedding检索尚未验收。以下功能验收项保持未完成。

## 范围与流程

后台选择智能体，上传 PDF、DOCX、XLSX、TXT、Markdown、CSV。原文件保存本地持久目录，MySQL 保存配置、文件、片段及处理状态，Qdrant 保存向量；不使用 MinIO。上传后由单进程后台 worker 解析、分段、调用独立 Embedding 模型、写入索引，成功后才进入检索。聊天只发送当前问题给 Embedding 服务，检索片段作为本轮参考，不自动写为用户记忆。

首版只支持文字型 PDF；无文字的图片页、加密 PDF、旧 DOC/XLS 明确报告不支持，不能保证识别所有混合扫描版式。DOCX 提取正文段落和表格，不提取图片/OCR、页眉页脚和文本框。Excel 保留工作表和行号，以第一个非空行为表头；读取公式文本，不执行公式、不保证全表统计或跨表计算。TXT/MD/CSV要求UTF-8，可带BOM。解析与索引有大小、时限和片段数量限制，超限明确失败，不悄悄使用截断文件。

## 隔离与权限

配置、文件、片段、任务状态均按 bot_id 隔离。管理员接口复用登录和 CSRF，下载经过鉴权；上传文件不放静态目录。磁盘使用 bot_id/随机文件ID/source.扩展名，原文件名只用于展示。Qdrant collection 按智能体和 Embedding 配置版本区分，查询同时带 bot_id 过滤，并回查 MySQL 文件状态和版本，绝不使用用户/模型提供的范围。所有资料面向该智能体的全部聊天对象；个人私密文档暂不支持用户级 ACL。

同智能体 SHA-256 去重；跨智能体不共享文件。删除先标记 deleting 退出检索，再异步移除向量、片段和原文件；清理失败可重试。worker 提交时验证文件和配置版本，防止删除后复活。管理操作与在途回复沿用机器人锁；配置、文件可用状态变化推进知识库版本和历史恢复边界，避免旧答案及摘要继续作为当前知识依据，长期记忆保留。

## 配置与后台

知识库菜单提供智能体选择、启用开关、辅助/严格回答模式、独立 Embedding 的 HTTPS 基础地址、模型名及加密 API Key。首版采用 OpenAI 兼容 /embeddings 协议，与 chat 供应商分开配置；保存不调用付费接口。更换地址需重新提供密钥，更换模型/地址/密钥后旧索引失效，需要明确重新处理文件。文件列表显示排队、处理中、可用、失败、删除中和清理失败；支持上传、下载、重新处理、删除和检索试用。试用/重建可能产生费用，页面仅保留简短必要提示。

## 检索与回复

后台模式普通 QQ/本地聊天回答前检索，命令跳过；网页语音、定时任务暂不接入。检索总时限默认5秒，无自动重试；最多5个片段、6000 UTF-8字节，并纳入现有 chat_context_budget 输入预算。首版不做查询改写、OCR、重排或结果缓存。辅助模式无命中正常聊天，故障明确提示资料暂不可用；严格模式无命中/故障由代码直接返回。命中结果使用独立参考消息和资料使用规则，文档中的指令不构成权限。来源由后端从本轮片段生成，保留文件名、PDF页码或工作表行号。

## 持久化、成本与恢复

新增 bot_knowledge_settings、knowledge_files、knowledge_chunks，迁移为0031_knowledge（接续0030）。文件行同时承担持久任务账本；每次索引使用新 generation，只有完成全部写入才 ready。重启后 processing 改 failed，不自动重复付费；queued 和 deleting 可继续。失败记录脱敏，外部响应原文、密钥不进入日志。索引调用分批、有最大批次数和总时限；Embedding 实际用量单独记录，不混入聊天 token 统计，未知用量不记成零。文件列表显示累计已知用量和未知调用；删除文件也移除其累计计数，不作为永久账单。检索用量通过聊天诊断保存，后台试用只显示当次用量。

新增手写迁移，兼容 MySQL 5.6/utf8mb4，不修改已执行版本。部署需显式迁移；按用户偏好不执行数据库备份。原文件和 Qdrant 必须持久化；Qdrant 不公开公网端口。关闭知识库不删除原文件。

### 首版固定上限

| 项目 | 上限/行为 |
| --- | --- |
| 文件 | 单文件50 MB，每智能体100个/1 GB，删除未完成仍占配额 |
| 上传 | 全局1个在途上传，120秒总时限；原始二进制请求，不使用multipart |
| 解析 | 独立Python子进程，60秒后终止；Linux额外限制768 MB地址空间 |
| 内容 | 正文2 MB、1000片段；每片段750字符，长段重叠100字符 |
| PDF | 最多300页 |
| Office | ZIP展开总大小60 MB、最多5000条目 |
| XLSX/CSV | 每表20000行、100列；XLSX最多30张表、累计200000单元格 |
| 索引 | 单worker，每批16片段、最多63次Embedding请求、整文件600秒；无自动重试 |
| 检索 | 一次问题向量化、5秒总时限、最多5片段，余弦分数阈值0.3 |

0.3是首版固定经验阈值，不代表真实性或跨模型统一相关度，需要用真实资料调优。知识库没有人民币硬预算或每日配额；管理员上传/手动重建即授权有界索引调用，普通聊天继承现有消息限流。严格模式如果预算未选入资料或模型未返回有效引用，代码返回无法给出可核对答案；这不保证引用覆盖答案每个事实。辅助模式只对模型明确标注且有效的引用补来源。

同一Qdrant实例须专供当前项目数据库使用，开发与生产使用不同实例/Compose项目卷，避免相同bot_id和版本命名冲突。更换Embedding后旧collection只在文件重建/删除时清理其点，空collection暂不自动删除。删除/配置更新不能撤回已发到QQ的消息。进程在上传落盘与数据库登记之间崩溃可能留下孤立文件；数据库提交结果未知时保留原文件以防误删，需对照knowledge_files后人工清理，不自动扫描删除磁盘资料。

## 代码入口与接口

| 入口 | 职责 |
| --- | --- |
| `qbot/knowledge_config.py` | 输入约束、固定上限、安全文件路径 |
| `qbot/knowledge_store.py` | 配置、归属校验、任务状态、版本、片段及用量事务 |
| `qbot/knowledge_parse.py` | 子进程文件解析、位置保留与分段 |
| `qbot/knowledge_clients.py` | OpenAI兼容Embedding和Qdrant REST，无SDK重试 |
| `qbot/knowledge.py` | 索引worker、检索、知识使用规则与引用 |
| `qbot/knowledge_routes.py` | 管理员接口、流式上传及下载 |
| `qbot/static/knowledge.js`、`knowledge.css` | 配置、文件列表、分页和检索试用 |
| `qbot/prompts/knowledge_use.txt` | 独立知识使用提示词 |

接口前缀 `/api/admin/knowledge/{bot_id}`：GET/PUT `/config`；GET `/files?page=1`；POST `/files?filename=原文件名`（Content-Type: application/octet-stream）；GET `/files/{file_id}/download`；POST `/files/{file_id}/retry`；DELETE `/files/{file_id}`；POST `/search`（JSON字段query）。所有接口要求管理员，写接口需要X-QBot-Admin和CSRF。文件列表需手动刷新，不轮询调用模型。上传成功返回queued，不声称已经索引完成。

## 安装与部署

新增依赖为pypdf、python-docx、openpyxl和defusedxml；沿用现有httpx调用Qdrant REST，不新增Qdrant Python SDK。停服务后在项目根目录安装依赖并显式迁移：

```powershell
.\.venv\Scripts\python -m pip install -c constraints.txt -e .
.\.venv\Scripts\python -m alembic upgrade head
```

本地Python可使用已有Qdrant，或手动启动仅绑定本机的独立容器（不要替换已有同名容器）：

```powershell
docker run -d --name qbot-qdrant-local --restart unless-stopped -p 127.0.0.1:6333:6333 -v qbot_qdrant_local:/qdrant/storage qdrant/qdrant:v1.15.1
```

本地`.env`配置 `KNOWLEDGE_STORAGE_DIR=data/knowledge`、`KNOWLEDGE_QDRANT_URL=http://127.0.0.1:6333`。外部受密钥保护的Qdrant另设 `KNOWLEDGE_QDRANT_API_KEY`，不把此凭证放Embedding表单。

Linux Docker部署前创建原文件目录，授予运行用户UID10001权限。以下使用默认开发项目；生产环境每条compose命令继续带既有 `--env-file .env.production -p qbot-prod` 和 `QBOT_ENV_FILE=.env.production`，避免误操作其他项目：

```sh
mkdir -p ./data/knowledge
sudo chown 10001:10001 ./data/knowledge
docker compose stop qbot
docker compose build qbot
docker compose run --rm --no-deps qbot python -m alembic upgrade head
docker compose --profile knowledge up -d qdrant qbot
```

Docker的环境文件中设置 `KNOWLEDGE_QDRANT_URL=http://qdrant:6333`，或不设置使用Compose默认值；不要保留本地127.0.0.1地址。`QBOT_KNOWLEDGE_DIR` 控制宿主机挂载位置，修改时同步创建目录及权限。容器内固定 `/app/data/knowledge`。内置Qdrant仅在internal网络提供服务，不发布端口，默认不启用API Key；若连接外部Qdrant，按外部服务配置密钥。不要执行 `down -v` 删除向量卷。按用户偏好不执行数据库备份。

启动后进入「知识库」，选择智能体，保存兼容的Embedding地址、模型、密钥，上传文件并刷新到“可用”，试用检索后启用知识库。保存设置不验证真实协议；本次未指定真实供应商、未调用付费接口。现有文件不会自动上传或回填。

## 待验收

- [ ] 六种文件解析、来源定位、扫描/加密/超限失败反馈。
- [ ] 跨智能体查询、下载、删除、重试均无法访问他人文件。
- [ ] 上传中断、解析失败、索引失败、服务重启和删除并发可恢复。
- [ ] 配置变化旧索引失效，旧任务不能提交。
- [ ] 辅助/严格回答、来源、输入预算与备用聊天模型兼容。
- [ ] Docker 持久目录权限、Qdrant连接和真实 Embedding 协议联调。

仅用户明确要求时运行测试；本次不调用付费模型、不打开浏览器、不执行迁移或部署。

2026-11-08 静态核对：14个新增/修改Python文件AST解析、knowledge.js/admin-ui.js/admin.js语法、TOML/YAML解析、存储方法引用、UTF-8读取和Git差异检查通过。未安装新增解析依赖、未运行测试、未连接数据库/Qdrant或真实Embedding服务，未提交或部署；文件解析效果、界面交互和完整调用链仍需运行验收。

## 2026-11-09 部署结果

功能提交ac3d970已推送并部署至实验室服务器qbot-prod。持有现有部署锁构建镜像后停qBot，将项目库从0030显式升级至0031；新增三张知识表核对存在，初始为空，未启用任何智能体知识库。Qdrant v1.15.1使用项目私有网络和持久卷，未开放宿主机端口；原文件目录为项目下data/knowledge，UID10001可写。Docker Hub下载超时后使用已有DAOCloud镜像源取得同版本镜像，未修改Docker全局设置。

qBot running healthy、Qdrant running，两容器重启次数0；容器内Qdrant /readyz返回200，/health、/admin与知识库JS/CSS均返回200，知识库接口未登录返回401。13个关键代码/静态资源/迁移文件SHA-256与提交一致，自动部署成功基线已登记。实际安装pypdf 6.21.4、python-docx 1.2.0、openpyxl 3.1.5、defusedxml 0.7.1。未执行数据库备份、测试、浏览器验证或付费Embedding/聊天调用；上传与检索仍需配置模型后验收。


## 2026-11-13 上传上限调整

单文件上限由30 MB调整为50 MB（50 × 1024 × 1024字节），同步前端校验、后端上传与解析限制及提示。其余解析和配额上限保持不变；重启后端并强制刷新后台后生效。功能提交 `ffcab40` 已于2026-11-13部署至实验室服务器qbot-prod。容器running healthy、重启次数0，/health正常、/admin返回200；四个相关运行文件SHA-256与发布仓库一致，容器内上限确认为50 × 1024 × 1024字节。未执行数据库备份或迁移，未运行测试、浏览器或真实文件上传验收。
