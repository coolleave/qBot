# Docker Compose 部署

已编写配置：Python 3.13 单实例应用容器，复用已有 MySQL，通过外部 Docker 网络连接；镜像包含后台静态文件、提示词、迁移、定时模板和图片素材。数据库升级独立执行，不随启动自动迁移。未构建镜像或进行部署联调，Linux 依赖安装与运行待验证。

## 环境和配置

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

更新代码后，先构建新镜像，再停止旧应用、备份并迁移；迁移成功后启动：

```sh
docker compose build qbot
docker compose stop qbot
docker compose run --rm --no-deps qbot python -m alembic upgrade head
docker compose up -d qbot
```

聊天记录、后台配置、上传的定时图片在现有 MySQL 中；`.env` 中的加密主密钥必须与数据库一起妥善备份。模型上下文和队列在内存中，重启不恢复。无需额外应用数据卷；日志使用 Docker 日志并轮转。

`config/` 和 `图片/` 随镜像复制，修改后重新构建部署；不要省略这两个目录。镜像使用非 root 用户运行，多阶段构建将原生音频依赖的编译工具留在构建阶段。现有 constraints.txt 来源于 Windows，Linux 依赖安装及 SILK 解码/编码仍需实际构建与联调确认。
