# Образ API. Контекст сборки — корень репозитория: нужен и core/, и data/.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Зависимости ставятся отдельным слоем: правка кода не должна ронять кеш pip.
COPY pyproject.toml README.md ./
COPY core ./core
RUN pip install --no-cache-dir ".[api]"

COPY api ./api
COPY data ./data
COPY scenarios ./scenarios

# Каталог техники и хранилище сценариев — по абсолютным путям, чтобы не
# зависеть от рабочей директории процесса.
ENV GEOSCAN_VEHICLES=/app/data/vehicles.yaml \
    GEOSCAN_STORAGE=/app/storage

EXPOSE 8000

# Одного воркера достаточно: сервис однопользовательский, а состояние задач
# расчёта живёт в памяти процесса (api/jobs.py). Второй воркер увидел бы
# чужие job_id как несуществующие.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
