# Imagen del pipeline y del dashboard.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 1000 etl

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY dashboard ./dashboard
RUN pip install ".[dashboard]"

USER etl
ENTRYPOINT ["etl-chile"]
CMD ["ejecutar"]
