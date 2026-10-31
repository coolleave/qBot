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
# 虚拟环境只包含第三方依赖，业务源码不参与此阶段的缓存键。

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
# 大体积且低频变化的素材置于依赖和业务代码之前，保留原资源路径。
# COPY 时统一权限，兼容宿主机旧的600文件/700目录，无需额外扫描或写层。
COPY --chmod=0755 图片/ ./图片/
COPY --chmod=0755 res/dafeiyu_100/ ./res/dafeiyu_100/
COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0755 config/ ./config/
COPY --chmod=0755 migrations/ ./migrations/
COPY --chmod=0755 alembic.ini ./
# 从工作目录直接运行源码，避免业务变化重建依赖环境和素材层。
COPY --chmod=0755 qbot/ ./qbot/
USER qbot
EXPOSE 8092
CMD ["python", "-m", "qbot", "serve"]
