FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY app ./app
COPY adapters ./adapters
COPY backlog.json ./
COPY prompts ./prompts
COPY migrations ./migrations
COPY fixtures ./fixtures
COPY scripts ./scripts
COPY alembic.ini ./

ENV APP_DATABASE_URL=postgresql+psycopg://recruitment:recruitment@postgres:5432/recruitment
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
