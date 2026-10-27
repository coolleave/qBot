FROM python:3.13-slim-bookworm AS builder

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
COPY qbot/ ./qbot/
RUN /opt/venv/bin/python -m pip install -c constraints.txt .

FROM python:3.13-slim-bookworm AS runtime

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
COPY migrations/ ./migrations/
COPY alembic.ini ./
USER qbot
EXPOSE 8092
CMD ["python", "-m", "qbot", "serve"]
