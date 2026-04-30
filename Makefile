.PHONY: help install infra infra-down db-migrate db-new-migration processor publish-events publish-event-0 publish-event-1 dms edw test lint format clean

help:
	@echo "AI Credit Underwriting Platform — Make targets:"
	@echo ""
	@echo "Infrastructure:"
	@echo "  make infra              Start postgres, redpanda, DMS, EDW (docker-compose up)"
	@echo "  make infra-down         Stop all services (docker-compose down)"
	@echo ""
	@echo "Setup:"
	@echo "  make install            Install dependencies (uv sync)"
	@echo "  make db-migrate         Apply database migrations (alembic upgrade head)"
	@echo "  make db-new-migration   Create a new migration (prompts for message)"
	@echo ""
	@echo "Running:"
	@echo "  make processor          Start the application processor (Kafka consumer)"
	@echo "  make dms                Start DMS mockup standalone (port 8001)"
	@echo "  make edw                Start EDW mockup standalone (port 8002)"
	@echo ""
	@echo "Publishing events:"
	@echo "  make publish-events     Publish all sample CRM events to Kafka"
	@echo "  make publish-event-0    Publish sample event 0 (happy path)"
	@echo "  make publish-event-1    Publish sample event 1 (salary mismatch)"
	@echo ""
	@echo "Development:"
	@echo "  make test               Run all tests (pytest)"
	@echo "  make test-unit          Run unit tests only"
	@echo "  make test-file FILE=... Run tests in a specific file"
	@echo "  make lint               Run flake8 linter"
	@echo "  make format             Format code (black + isort)"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean              Remove pycache, .pytest_cache, etc."
	@echo "  make clean-db           Reset database (drop and recreate schema)"
	@echo ""
	@echo "Quick start:"
	@echo "  make install"
	@echo "  make infra               # terminal 1"
	@echo "  make db-migrate          # terminal 2"
	@echo "  make processor           # terminal 3"
	@echo "  make publish-events      # terminal 2 (or any)"

# ---- Infrastructure ----

infra:
	docker-compose up

infra-down:
	docker-compose down

infra-logs:
	docker-compose logs -f

# ---- Setup ----

install:
	uv sync

db-migrate:
	uv run alembic upgrade head

db-new-migration:
	@read -p "Migration message: " msg; \
	uv run alembic revision --autogenerate -m "$$msg"

db-downgrade:
	uv run alembic downgrade -1

db-current:
	uv run alembic current

# ---- Running services ----

processor:
	uv run python -m creditunder

dms:
	uv run python -m mockups.dms.app

edw:
	uv run python -m mockups.edw.app

# ---- Publishing events ----

publish-events:
	uv run python -m mockups.crm.publisher

publish-event-0:
	uv run python -m mockups.crm.publisher --event-index 0

publish-event-1:
	uv run python -m mockups.crm.publisher --event-index 1

# ---- Testing & Quality ----

test:
	uv run pytest -v

test-unit:
	uv run pytest -v tests/unit/

test-file:
	@if [ -z "$(FILE)" ]; then \
		echo "Usage: make test-file FILE=tests/unit/test_handlers.py"; \
		exit 1; \
	fi
	uv run pytest -v $(FILE)

lint:
	uv run flake8 src/ mockups/

format:
	uv run black . && uv run isort .

format-check:
	uv run black --check . && uv run isort --check-only .

# ---- Cleanup ----

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	find . -type f -name '*~' -delete

clean-db:
	@echo "Dropping all tables and re-running migrations..."
	uv run alembic downgrade base
	uv run alembic upgrade head

# ---- Convenience targets ----

demo: infra db-migrate processor

demo-quick:
	@echo "Running quick demo with processor and sample events..."
	@echo "Make sure 'make infra' and 'make db-migrate' are running in other terminals."
	sleep 2
	$(MAKE) processor &
	sleep 5
	$(MAKE) publish-events
	wait
