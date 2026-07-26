FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /service
COPY requirements-ml.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.4,<3"
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements-ml.txt
COPY ml_service ./ml_service

EXPOSE 8090
CMD ["uvicorn", "ml_service.main:app", "--host", "0.0.0.0", "--port", "8090"]
