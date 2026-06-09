FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY sacm ./sacm
COPY apps ./apps
COPY cli ./cli
RUN pip install -e .

COPY . .

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
