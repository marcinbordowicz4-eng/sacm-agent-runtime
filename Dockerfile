FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY sacm ./sacm
COPY apps ./apps
COPY cli ./cli
RUN pip install -e ".[mlflow,temporal]"

RUN addgroup --system sacm && adduser --system --ingroup sacm sacm
COPY --chown=sacm:sacm . .
RUN chown -R sacm:sacm /app
RUN chmod 0755 /app/docker-entrypoint.sh
USER sacm

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/ready', timeout=3)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["sh", "-c", "sacm-migrate && exec uvicorn apps.api.main:app --host 0.0.0.0 --port 8000"]
