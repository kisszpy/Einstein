# 后端镜像：多阶段构建，运行阶段非 root、无编译工具链。
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
# e2b 2.31 的元数据（wcmatch<11）与 deepagents 的（wcmatch>=11）互相矛盾，单次
# pip install 会被解析器判死；但 e2b 2.31 实际已不 import wcmatch（见 requirements
# 同一段注释，开发环境也是这么绕的）。分两步：先装其余依赖（wcmatch>=11 由
# deepagents 带入），再 --no-deps 装 e2b 并显式补上它真正引用的运行时依赖。
RUN grep -v '^e2b==' requirements.txt > /tmp/req-noe2b.txt \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r /tmp/req-noe2b.txt \
    && /opt/venv/bin/pip install e2b==2.31.0 --no-deps \
       attrs dockerfile-parse h2 httpcore httpx packaging \
       protobuf python-dateutil rich typing-extensions


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 nodejs npm chromium fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 10001 einstein

# 浏览器实测排版（htmlkit/measure）与 browser_pro 工具走 Node 兜底路径：
# 需要 node + playwright-core + chromium。CWD 是 /app，_node_modules() 会找到
# /app/web/node_modules；chromium 路径经 EINSTEIN_CHROME_PATH 传给渲染脚本。
# 中文字体必须装：没字体时 Chromium 用 fallback 测行宽，中文排版的几何全是错的。
RUN mkdir -p /app/web \
    && cd /app/web \
    && npm install --omit=dev playwright-core \
    && chown -R einstein:einstein /app/web

ENV EINSTEIN_CHROME_PATH=/usr/bin/chromium

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=einstein:einstein app ./app
COPY --chown=einstein:einstein tools ./tools

# 数据、上传、导出、项目文件都落在这里，生产用 volume 挂载
RUN mkdir -p /app/data && chown -R einstein:einstein /app

USER einstein
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
