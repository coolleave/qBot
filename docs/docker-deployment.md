# Docker Compose 部署

## 自动部署文件权限修复（2026-10-29，待服务器验证）

旧部署脚本对全部操作使用 umask 077，Git 更新的源码可能成为 600 权限；Docker COPY 保留权限但默认归属 root，导致非 root 的 qbot 用户无法读取源码并循环重启。修复方案：仅在 Git 快进更新的子 shell 中使用 umask 022，日志、锁与成功标记仍沿用 077；运行镜像在复制资源后为 /app 增加读取权限和目录遍历权限，兼容此前已经产生的 600 文件，不修改宿主机 .env 权限。修正失败日志退出码捕获，避免显示错误的 0。

本次继续使用项目内现有镜像源和依赖缓存，不修改 Docker 全局配置；无需数据库迁移。修复仅影响源码更新和运行镜像末尾的权限层，依赖安装层可复用。部署完成后以容器 healthy、/health、/admin 和非 root 文件读取结果核对。

## 构建下载源（实验室服务器构建已通过）

基础镜像默认使用 `m.daocloud.io/docker.io/library/python:3.13-slim-bookworm`，通过 DAOCloud 获取官方 Python 镜像，避免实验室服务器直接访问 Docker Hub 超时。2026-10-28 已在该服务器完成基础镜像及完整应用镜像构建，并重建生产容器，Docker 健康检查通过；基础镜像层下载约 39 秒，首次依赖安装仍受实验室网络速度影响。Compose 可通过 QBOT_PYTHON_IMAGE 覆盖镜像地址，Dockerfile 对应 PYTHON_IMAGE 构建参数；不要将不同 Python 大版本、Alpine 或非 Debian 镜像直接混用。

Dockerfile 的构建与运行阶段均使用清华 TUNA 的 Debian / Debian Security HTTPS 镜像，保留 Debian 官方签名校验。不修改宿主机的软件源。移除额外 Dockerfile frontend 下载，使用 Docker 自带构建语法。Python 依赖安装使用 `https://pypi.tuna.tsinghua.edu.cn/simple`，网络超时 60 秒，保持 HTTPS 证书校验，不修改本地虚拟环境的 pip 配置。依赖继续由 constraints.txt 锁定。

依赖安装层只读取 pyproject.toml 和 constraints.txt（从项目声明生成临时依赖清单），业务源码随后复制并用 `--no-deps --no-build-isolation` 安装。这样修改 Python 代码、JS 或其他静态资源不会让依赖层失效。首次调整、基础镜像变化、依赖声明变化或缓存被清理时仍需重新安装；普通构建不加 --no-cache，也不主动清理缓存。未运行测试。

Python 3.13 单实例应用容器复用已有 MySQL，通过外部 Docker 网络连接；镜像包含后台静态文件、提示词、迁移、定时模板和图片素材。数据库升级独立执行，不随启动自动迁移。已完成 Linux 镜像构建和容器健康检查；不代表真实 QQ、ASR/TTS 或模型功能全部验收通过。

## 环境和配置

### 测试与线上环境（配置已编写，运行待验证）

保留 `.env` 作为测试环境，`.env.production` 作为线上环境。本次线上配置复制现有 `.env`，仅将 `MYSQL_HOST` 改为 `182.92.111.155`、`MYSQL_PORT` 改为 `3306`，其余账号、密码和加密主密钥保持一致。两份真实配置均被 Git 忽略，需要安全传到对应服务器，不要提交或打入镜像。

Compose 的 `QBOT_ENV_FILE` 选择注入应用容器的配置文件，默认 `.env`；`--env-file` 选择 Compose 端口、网络等变量的来源。线上命令同时指定两者，避免只切换 Compose 插值而应用仍读取测试配置。以下为 Linux shell 命令：

```sh
# 测试环境（保留原有 Compose 项目名，避免另起重复实例）
docker compose --env-file .env up -d --build qbot

# 线上环境：在部署服务器执行
QBOT_ENV_FILE=.env.production docker compose --env-file .env.production -p qbot-prod up -d --build qbot
QBOT_ENV_FILE=.env.production docker compose --env-file .env.production -p qbot-prod logs --tail=100 -f qbot
```

线上查询迁移版本使用同样的配置选择：

```sh
QBOT_ENV_FILE=.env.production docker compose --env-file .env.production -p qbot-prod run --rm --no-deps qbot python -m alembic current
```

需要升级时，先备份并停止对应环境应用，再将 `current` 换为 `upgrade head`，成功后启动；不要不带环境参数执行线上迁移。线上停服务也使用相同命令前缀，将后续操作换成 `stop qbot`。本次没有连接或迁移线上数据库、启动容器。

两套环境默认都映射 8092，适合分别部署在不同服务器；同一宿主机不能同时占用同一地址端口。若数据库复制了相同 QQ 机器人凭证，不要同时启用两套环境的同一个机器人，测试时使用独立机器人或停用对应实例。

远程 MySQL 使用实际 IP 和 3306，需允许应用服务器来源连接。当前 Compose 仍要求 `QBOT_DOCKER_NETWORK` 指定的外部网络存在；若线上没有该网络，创建一次 `docker network create qbot-network`（或实际配置的网络名）即可，无需把远程 MySQL 接入本机网络。

直接运行 `python -m qbot serve` 仍默认读取 `.env`，不会自动读取 `.env.production`；以上环境选择用于 Docker Compose。

需要 Linux Docker Engine 或使用 Linux 容器的 Docker Desktop，以及 Docker Compose v2。在项目根目录执行命令。不要同时运行宿主机旧服务和容器，也不要扩容或开启多 worker。

已有 `.env` 保留原文件，按 `.env.example` 补齐配置；新机器才复制模板为 `.env`。Compose 将 `.env` 注入容器，镜像不包含该文件。密码、密码哈希等含 `$` 或 `#` 的值请用单引号包裹，例如 `ADMIN_PASSWORD_HASH='生成的完整哈希'`，防止 Compose 插值或注释解析改变内容；不要使用 `$$` 替换真实密码。不要分享 `docker compose config` 的完整输出，其中可能包含密钥。

容器内固定 `HOST=0.0.0.0`、`PORT=8092`；宿主机默认只映射 `127.0.0.1:8092`。可通过 `QBOT_BIND_IP`、`QBOT_HTTP_PORT` 调整映射，不影响本地 Python 启动配置。HTTPS 反向代理部署时设置 `ADMIN_COOKIE_SECURE=true`，通过 SSH 隧道或本机 HTTP 访问时保持 false。

## 连接已有 MySQL

本 Compose 不创建 MySQL、数据库或账号。推荐应用加入 MySQL 已有的用户自定义 bridge 网络：将 `.env` 的 `QBOT_DOCKER_NETWORK` 设置为实际网络名，`MYSQL_HOST` 设置为该网络上的 MySQL 容器名或别名，`MYSQL_PORT` 使用容器内部端口（通常为 3306），不是宿主机映射端口。默认 bridge 网络不提供这里所需的容器名解析。

若没有合适的网络，手动创建一次专用网络并把现有 MySQL 接入，以下 `你的MySQL容器名` 必须替换为实际名称；已存在网络或已连接时不要重复执行：

```sh
docker network create qbot-network
docker network connect --alias qbot-mysql qbot-network 你的MySQL容器名
```

对应 `.env`：

```dotenv
QBOT_DOCKER_NETWORK=qbot-network
MYSQL_HOST=qbot-mysql
MYSQL_PORT=3306
MYSQL_DATABASE=qbot
```

配置仅有 qbot 库权限、允许应用网络来源连接的 MySQL 用户。MySQL 容器未来重建时，需在其原有部署配置中保留网络和别名；手动连接只对当前容器有效。迁移权限参考[数据库迁移](database-migrations.md)。

## 构建、初始化和启动

```sh
docker compose build qbot
```

仅新安装、尚未配置登录凭证时执行以下辅助命令，将输出写入 `.env`；已有加密主密钥和会话密钥必须保留，不要重新生成覆盖：

```sh
docker compose run --rm --no-deps qbot python -m qbot generate-secrets
docker compose run --rm --no-deps qbot python -m qbot hash-password
```

确认 `.env` 的 MySQL 指向项目库，备份数据库，停止旧服务，再手动升级并启动：

```sh
docker compose run --rm --no-deps qbot python -m alembic upgrade head
docker compose run --rm --no-deps qbot python -m alembic current
docker compose up -d qbot
docker compose ps
docker compose logs --tail=100 -f qbot
```

迁移失败时先修复，不继续启动，不使用 stamp。浏览器在服务器本机访问 `http://127.0.0.1:8092/admin`；远程管理可使用 `ssh -L 8092:127.0.0.1:8092 用户@服务器`，再打开本机该地址。WebSocket 无需公网回调。Webhook 需配置 HTTPS 反向代理转发 `/qq/webhook/{bot_id}`；上述回环端口适用于宿主机上的反向代理，容器化代理需接入共享网络并转发 `qbot:8092`。

健康检查仅请求 `/health`，不调用模型，也不代表数据库或 QQ 连接正常。自动重启仅在进程退出时触发，unhealthy 本身不会自动重启；QQ 连续失败停止重连时需排查配置后手动重启。

## 更新与数据

### 每 30 分钟自动检查更新（配置已编写，运行待验证）

服务器 cron 调用 `scripts/deploy-if-updated.sh`，检查 origin/main，仅当远端提交不同于上次成功部署提交时构建并更新 qbot-prod。脚本用 flock 防止重叠部署，网络抓取最多 180 秒；先构建、再更新容器并最多等待 120 秒健康检查。构建失败不替换原容器，健康检查失败不登记成功，不自动回滚；两种失败下次都会重试。没有更新时不重启。健康只代表 HTTP 进程，不代表 QQ 已连接。2026-10-28 已验证新增迁移时暂停的路径，并完成手动初始化部署、健康检查和成功基线登记；随后脚本退出码为 0，日志为“无新提交，跳过”。未安装 cron。

成功提交号在 `.deploy/last-success`，锁和日志也在 `.deploy/`，已加入 Git 忽略。日志按服务器日期每日一份，下一次运行时删除超过 14 天的旧部署日志。仓库必须在 main 分支且所有已跟踪文件没有本地修改；脚本不会自动 stash、reset 或覆盖环境文件。运行用户必须有 Docker 权限，并具备 Bash、Git、flock、timeout、find、Compose v2（支持 `up --wait --wait-timeout`）。脚本不依赖交互式 shell 或 conda 环境。

#### 首次安装

以下在实验室服务器用 zhangxiaowen 用户执行，不使用 root 的 crontab。保留服务器已经能免交互访问 GitHub 的 origin：

```sh
cd "$HOME/qBot/qBot"
git diff -- compose.yaml
```

公开仓库可以使用 HTTPS 而无需部署密钥，但需先确认服务器能访问。2026-10-28 检查时，HTTPS 抓取长时间无响应，已有 `git@github.com:coolleave/qBot.git` 在 BatchMode 下抓取成功，因此保留该连接；这台服务器已有可用 SSH 认证，不需要额外配置或替换。

此前服务器曾把 `env_file` 手动改成 `.env.production`，GitHub 现在统一使用 `${QBOT_ENV_FILE:-.env}`。如果上述差异只有这项已知修改，先备份再恢复该文件，然后拉代码；有其他修改则先人工核对，不要直接恢复：

```sh
cp -p compose.yaml "$HOME/qbot-compose.before-auto.yaml"
git restore --source=HEAD -- compose.yaml
git pull --ff-only
```

确认 `.env.production` 保留实际数据库、密钥及 `QBOT_BIND_IP=192.168.1.106` 等生产配置。首次先手动完成当前代码的镜像部署和所需数据库迁移，再登记基线。若当前代码不需要新迁移：

```sh
QBOT_ENV_FILE=.env.production docker compose --env-file .env.production -p qbot-prod up -d --build --wait --wait-timeout 120 qbot
bash scripts/deploy-if-updated.sh --mark-current
bash scripts/deploy-if-updated.sh
```

`--mark-current` 是维护者确认“当前 HEAD 已经完成部署”的操作，只额外核对现有容器健康，不会构建、迁移，也不能验证容器代码是否与 HEAD 相同。因此只能在上述手动部署成功后使用，不能用来跳过部署失败。未登记基线时定时脚本会拒绝自动发布。

执行 `crontab -e`，保留现有任务，只添加下面一行（不要重复添加）：

```cron
*/30 * * * * /bin/bash "$HOME/qBot/qBot/scripts/deploy-if-updated.sh" "$HOME/qBot/qBot"
```

每小时 00、30 分执行一次，日期和时区使用服务器设置；运行跨过下一次触发时间时，新任务因锁而跳过。使用 HOME 适配服务器主目录：实验室实际仓库为 `/mnt/disk/home/zhangxiaowen/qBot/qBot`，不能假定在 `/home/zhangxiaowen`。检查计划任务及日志：

```sh
crontab -l
ls -lt .deploy/logs/
tail -n 100 .deploy/logs/deploy-$(date +%F).log
```

#### 数据库升级与维护

检测到相对成功部署版本有 `migrations/` 变更时，在拉取代码到工作区、构建或重启前暂停，日志提示人工处理。不只检查本地 HEAD，因此拉取成功但构建失败也不会丢掉后续重试。新增、修改或删除迁移均暂停；不会自动运行 Alembic 或 stamp。

维护者按[数据库迁移](database-migrations.md)完成代码更新、备份、停止服务、迁移、新版本部署与健康检查后，运行 `bash scripts/deploy-if-updated.sh --mark-current` 更新基线，之后恢复自动检查。人工维护期间先在 crontab 注释该行，确认没有部署进程仍在运行；结束后再取消注释，避免和手动操作重叠。

如果更新需要手动修改 `.env.production`、外部网络或其他基础设施，也应先暂停 cron 并按发布说明处理；脚本不会自动推断这些前置条件。自动构建仍会沿用当前 Dockerfile 的依赖缓存策略，并非每次只重启容器。

更新代码后，先构建新镜像，再停止旧应用、备份并迁移；迁移成功后启动：

```sh
docker compose build qbot
docker compose stop qbot
docker compose run --rm --no-deps qbot python -m alembic upgrade head
docker compose up -d qbot
```

聊天记录、后台配置、上传的定时图片在现有 MySQL 中；`.env` 中的加密主密钥必须与数据库一起妥善备份。模型上下文和队列在内存中，重启不恢复。无需额外应用数据卷；日志使用 Docker 日志并轮转。

`config/` 和 `图片/` 随镜像复制，修改后重新构建部署；不要省略这两个目录。镜像使用非 root 用户运行，多阶段构建将原生音频依赖的编译工具留在构建阶段。现有 constraints.txt 来源于 Windows，Linux 依赖安装及 SILK 解码/编码仍需实际构建与联调确认。
