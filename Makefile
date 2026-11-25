.PHONY: install lint test up down run dashboard

install:  ## Dependencias de desarrollo y del dashboard
	uv sync --all-extras

lint:
	uv run ruff format --check src tests dashboard
	uv run ruff check src tests dashboard
	uv run mypy

test:  ## Necesita TEST_DATABASE_URL para los tests de integración
	uv run pytest

up:  ## PostgreSQL + pipeline + dashboard con Docker
	docker compose up --build

down:
	docker compose down

run:  ## Corre el pipeline contra DATABASE_URL
	uv run etl-chile ejecutar

dashboard:
	uv run streamlit run dashboard/app.py
