# bid-app 单镜像(前端构建 + 后端运行时合并)
# 参考 IMPLEMENTATION_SPEC §17.1

ARG NODE_PACKAGE_REGISTRY=https://registry.npmmirror.com

# ──── Stage 1: 前端构建 ────
FROM node:20-alpine AS frontend-builder
ARG NODE_PACKAGE_REGISTRY
WORKDIR /frontend
RUN npm config set registry "${NODE_PACKAGE_REGISTRY}" \
    && corepack enable \
    && corepack prepare pnpm@9.15.0 --activate \
    && pnpm config set registry "${NODE_PACKAGE_REGISTRY}"
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ──── Stage 2: Mermaid CLI(Node 22) ────
FROM node:22-bookworm-slim AS mermaid-cli
ARG NODE_PACKAGE_REGISTRY
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
RUN npm config set registry "${NODE_PACKAGE_REGISTRY}" \
    && npm install -g @mermaid-js/mermaid-cli@11.4.0 \
    && mmdc --version

# ──── Stage 3: 后端运行时 ────
FROM python:3.12-slim-bookworm AS runtime

ARG PYTHON_PACKAGE_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PIP_INDEX_URL=${PYTHON_PACKAGE_INDEX_URL} \
    UV_INDEX_URL=${PYTHON_PACKAGE_INDEX_URL} \
    UV_DEFAULT_INDEX=${PYTHON_PACKAGE_INDEX_URL}

RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        chromium \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        curl \
        gnupg \
        lsb-release \
        tzdata \
        supervisor \
        cron \
        ca-certificates \
        libreoffice-core \
        libreoffice-writer \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | \
       gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client-16 \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY --from=mermaid-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=mermaid-cli /usr/local/bin/mmdc /usr/local/bin/mmdc
COPY --from=mermaid-cli /usr/local/lib/node_modules /usr/local/lib/node_modules

COPY docker/mermaid-config.json /etc/mermaid-config.json
COPY docker/puppeteer-config.json /etc/puppeteer-config.json
COPY docker/mermaid.css /etc/mermaid.css

RUN python -m pip install --no-cache-dir uv

WORKDIR /app
# Phase 1:仅装第三方依赖,缓存这一层(只在 pyproject.toml / uv.lock 变化时失效)。
# `--no-install-project` 跳过 bid-app 自身,因为此时 src/ 还没 COPY 进来 —
# Hatchling 的 editable wheel 需要源路径存在才能写 .pth 让 import 生效;
# 提前装会写一个空 dist-info,运行时 ModuleNotFoundError: No module named 'bid_app'。
COPY backend/pyproject.toml backend/uv.lock /app/backend/
RUN cd /app/backend && uv sync --frozen --no-dev --no-install-project

# Phase 2:COPY 源码后再装 bid-app 本身(editable .pth 由 Hatchling 写入),
# 这一层在源码变化时才失效。
COPY backend/ /app/backend/
RUN cd /app/backend && uv sync --frozen --no-dev

COPY --from=frontend-builder /frontend/dist /app/frontend/dist

COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY docker/pg-backup.sh /usr/local/bin/pg-backup.sh
RUN chmod +x /usr/local/bin/pg-backup.sh \
    && echo "0 3 * * * root . /etc/bid-app.env && /usr/local/bin/pg-backup.sh >> /var/log/pg-backup.log 2>&1" \
       > /etc/cron.d/bid-app-backup \
    && chmod 0644 /etc/cron.d/bid-app-backup

RUN mkdir -p /var/lib/bid-app/projects /var/lib/bid-app/backups

EXPOSE 12123

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf", "-n"]
