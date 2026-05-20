# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder

ARG AO_BUILD_CONFIG
ARG AO_BUILD_TOKEN
ARG AO_PACKAGE_INDEX_URL

ENV PIP_NO_CACHE_DIR=1
WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade build \
    && python -m build --wheel --outdir /wheels

FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Agent Orchestrator" \
      org.opencontainers.image.description="Enterprise agent orchestration API" \
      org.opencontainers.image.version="2.4.1"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /wheels/*.whl /tmp/

RUN python -m pip install --no-cache-dir /tmp/*.whl \
    && rm -f /tmp/*.whl \
    && groupadd --system agent \
    && useradd --system --gid agent --home-dir /app agent

USER agent
EXPOSE 8000

CMD ["uvicorn", "src.api.server:create_app", "--factory", "--host", \
     "0.0.0.0", "--port", "8000"]
