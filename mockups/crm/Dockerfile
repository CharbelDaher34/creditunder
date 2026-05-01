FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

WORKDIR /app

COPY mockups/crm/pyproject.toml mockups/crm/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY mockups/crm/app.py ./mockups/crm/app.py
COPY mockups/__init__.py ./mockups/__init__.py
COPY mockups/crm/__init__.py ./mockups/crm/__init__.py
COPY mockups/crm/publisher.py ./mockups/crm/publisher.py
COPY mockups/crm/sample_data.py ./mockups/crm/sample_data.py

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8003
CMD ["python", "-m", "mockups.crm.app"]
