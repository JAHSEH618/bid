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

# ──── Stage 2: 后端运行时 ────
FROM python:3.12-slim-bookworm AS runtime

ARG DEBIAN_APT_MIRROR=http://mirrors.cloud.tencent.com/debian
ARG DEBIAN_SECURITY_APT_MIRROR=http://mirrors.cloud.tencent.com/debian-security
ARG PYTHON_PACKAGE_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_SKIP_DOWNLOAD=true \
    PIP_INDEX_URL=${PYTHON_PACKAGE_INDEX_URL} \
    UV_INDEX_URL=${PYTHON_PACKAGE_INDEX_URL} \
    UV_DEFAULT_INDEX=${PYTHON_PACKAGE_INDEX_URL}

RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_APT_MIRROR}|g" \
        -e "s|https://deb.debian.org/debian-security|${DEBIAN_SECURITY_APT_MIRROR}|g" \
        -e "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_APT_MIRROR}|g" \
        -e "s|https://security.debian.org/debian-security|${DEBIAN_SECURITY_APT_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian|${DEBIAN_APT_MIRROR}|g" \
        -e "s|https://deb.debian.org/debian|${DEBIAN_APT_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        pandoc \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        tzdata \
        supervisor \
        cron \
        ca-certificates \
        libreoffice-core \
        libreoffice-writer \
        postgresql-client \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY docker/mermaid-config.json /etc/mermaid-config.json
COPY docker/puppeteer-config.json /etc/puppeteer-config.json
COPY docker/mermaid.css /etc/mermaid.css

WORKDIR /app
COPY backend/ /app/backend/
RUN cd /app/backend \
    && python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

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
