FROM python:3.11-slim

ARG VERSION=0.2.0
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG COPILOT_VERSION=1.0.77
LABEL org.opencontainers.image.title="SACM Agent Runtime" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/sacm-ai/sacm-agent-runtime" \
      org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y age curl gh git nodejs npm postgresql-client \
    && npm install --global @openai/codex@latest \
    && curl -fsSL "https://github.com/github/copilot-cli/releases/download/v${COPILOT_VERSION}/copilot-linux-x64.tar.gz" \
      | tar -xz -C /usr/local/bin copilot \
    && chmod 0755 /usr/local/bin/copilot \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY sacm ./sacm
COPY apps ./apps
COPY cli ./cli
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      "torch==2.5.1+cpu" \
    && pip install --no-cache-dir -e ".[auth,mlflow,temporal]" \
    && pip install --no-cache-dir --upgrade \
      "setuptools>=83.0.0" \
      "wheel>=0.46.2" \
      "jaraco.context>=6.1.0"

RUN addgroup --system sacm && adduser --system --ingroup sacm sacm
COPY --chown=sacm:sacm . .
RUN chown -R sacm:sacm /app
RUN mkdir -p /app/.sacm/evidence /app/.sacm/state /app/.sacm/gh /app/.sacm/codex \
    && chown -R sacm:sacm /app/.sacm
RUN chmod 0755 /app/docker-entrypoint.sh

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER sacm

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/ready', timeout=3)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["sh", "-c", "sacm-migrate && exec uvicorn apps.api.main:app --host 0.0.0.0 --port 8000"]
