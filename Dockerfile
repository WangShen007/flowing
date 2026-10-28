# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-build

ENV NODE_OPTIONS=--max-old-space-size=512

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    API_PREFIX=/api/v1 \
    FRONTEND_DIST_DIR=/app/frontend/dist \
    LARK_CLI_COMMAND_TIMEOUT=120

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        tini \
    && rm -rf /var/lib/apt/lists/*

# The skills installer requires Node >=22.20; Debian bookworm's nodejs is 18.
COPY --from=frontend-build /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-build /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

ARG LARK_CLI_VERSION=1.0.98
ARG SKILLS_CLI_VERSION=1.7.4
RUN npm install -g "@larksuite/cli@${LARK_CLI_VERSION}" --no-audit --no-fund \
    && npx --yes "skills@${SKILLS_CLI_VERSION}" add larksuite/cli -y -g

COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY .env.example /app/.env.example
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

RUN mkdir -p /app/.feishu_cli_data

WORKDIR /app/backend
EXPOSE 8000
VOLUME ["/app/.feishu_cli_data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()" || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
