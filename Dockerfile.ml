FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=1000 \
    PIP_NO_INPUT=1

COPY requirements-ml.txt ./

RUN --mount=type=cache,id=matching-pip-cache,target=/root/.cache/pip,sharing=locked \
    pip install --retries 10 -r requirements-ml.txt

COPY ml_service ./ml_service

EXPOSE 8090

CMD ["uvicorn", "ml_service.main:app", "--host", "0.0.0.0", "--port", "8090"]
