ARG PYTHON_IMAGE=m.daocloud.io/docker.io/library/python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
# 音频库在没有对应平台 wheel 时需要编译；工具不进入最终镜像。
RUN sed -i \
        -e 's|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
        -e 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
COPY pyproject.toml constraints.txt ./
# 依赖层只依赖项目声明，修改业务代码或静态资源时可直接复用。
RUN /opt/venv/bin/python -c 'import pathlib,tomllib; project=tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8")); pathlib.Path("/tmp/qbot-requirements.txt").write_text("\n".join(project["project"]["dependencies"])+"\n", encoding="utf-8")' \
    && PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=60 \
    /opt/venv/bin/python -m pip install -c constraints.txt 'setuptools>=75' -r /tmp/qbot-requirements.txt
COPY qbot/ ./qbot/
# 构建工具已在缓存层安装，本项目安装无需再次联网解析依赖。
RUN /opt/venv/bin/python -m pip install --no-deps --no-build-isolation .

FROM ${PYTHON_IMAGE} AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    HOST=0.0.0.0 \
    PORT=8092
RUN sed -i \
        -e 's|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
        -e 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends tzdata libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home qbot
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
# 资源路径根据 qbot 源文件定位，保留项目目录布局。
COPY qbot/ ./qbot/
COPY config/ ./config/
COPY 图片/ ./图片/
COPY res/dafeiyu_100/ ./res/dafeiyu_100/
COPY migrations/ ./migrations/
COPY alembic.ini ./
# COPY 保留宿主机权限；兼容旧部署脚本生成的 600 源码和 700 目录。
RUN find /app \( -type d -o -perm /111 \) ! -perm -a+rx -exec chmod a+rx {} + \
    && find /app ! -perm -a+r -exec chmod a+r {} +
USER qbot
EXPOSE 8092
CMD ["python", "-m", "qbot", "serve"]
