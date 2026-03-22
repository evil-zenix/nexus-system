# ============================================================================
# MULTI-STAGE BUILD: Базовый образ Python с зависимостями
# ============================================================================

FROM python:3.11-slim as base

# Установить переменные окружения для Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Установить системные зависимости (минимум для async)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Установить рабочую директорию
WORKDIR /app

# Копировать requirements и установить зависимости
COPY requirements.txt .
RUN pip install --upgrade pip setuptools && \
    pip install -r requirements.txt

# Копировать исходный код
COPY config/ ./config/
COPY core/ ./core/
COPY scripts/ ./scripts/

# Health check для контейнера
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Запуск по умолчанию (будет переопределено в docker-compose)
CMD ["python", "-m", "core.router.main"]

# ============================================================================
# ОПЦИОНАЛЬНЫЙ STAGE: Development образ с дополнительными инструментами
# ============================================================================

FROM base as development

# Установить dev-зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    vim \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install \
    pytest \
    pytest-asyncio \
    black \
    flake8 \
    isort \
    mypy

CMD ["python", "-m", "core.router.main"]
