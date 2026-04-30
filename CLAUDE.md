# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AI Credit Underwriting Platform** for Alinma Bank. Automates credit application processing: consumes Kafka events from Siebel CRM, fetches documents from DMS, verifies and extracts structured data via AI Service, applies product business rules, generates a PDF report, and delivers outputs to DMS and EDW.

The full system design rationale is in [credit_underwriting_system_requirement.md](credit_underwriting_system_requirement.md).

## Tech Stack

- Python 3.11, **uv** for package management
- PostgreSQL + SQLAlchemy (asyncpg) + Alembic
- Kafka via `aiokafka` (local dev: Redpanda in docker-compose)
- OpenAI-compatible AI client (`openai` SDK, configurable base URL / API key / model)
- FastAPI + uvicorn for mockup services
- Jinja2 + WeasyPrint for HTML-to-PDF report generation
- structlog for structured logging

## Commands

```bash
# Install dependencies
uv sync

# Run the processor (consumes Kafka, processes cases end-to-end)
uv run python -m creditunder

# Start all infrastructure (postgres, redpanda, DMS mockup, EDW mockup)
docker-compose up

# Apply database migrations
uv run alembic upgrade head

# Generate a new migration after model changes
uv run alembic revision --autogenerate -m "description"

# Publish sample CRM events to Kafka (all samples)
uv run python -m mockups.crm.publisher

# Publish a single sample event (0-indexed)
uv run python -m mockups.crm.publisher --event-index 0

# Run tests
uv run pytest
uv run pytest tests/unit/test_handlers.py          # single file
uv run pytest -k "test_name"                        # single test

# Format / lint
uv run black . && uv run isort .
uv run flake8 .
```

## Directory Structure

```
src/creditunder/
├── config.py               # pydantic-settings: all env vars
├── observability.py        # structlog configuration
├── __main__.py             # entrypoint → ApplicationProcessor.run()
├── domain/
│   ├── enums.py            # ProductType, DocumentType, ValidationOutcome, Recommendation, …
│   └── models.py           # ExtractedField[T], DocumentResult, CaseResult, ApplicationEvent
├── documents/
│   ├── __init__.py         # EXTRACTION_SCHEMA_REGISTRY (DocumentType → schema class)
│   ├── base.py             # BaseExtractionSchema
│   ├── id_document.py      # IDDocumentExtraction (Pydantic)
│   └── salary_certificate.py  # SalaryCertificateExtraction (Pydantic)
├── handlers/
│   ├── base.py             # BaseProductHandler (ABC)
│   ├── registry.py         # get_handler(ProductType) → handler instance
│   └── personal_finance.py # PersonalFinanceHandler — rules: ID_NUMBER_MISMATCH, ID_NAME_CHECK, ID_EXPIRED, SALARY_EMPLOYER_MATCH, SALARY_DEVIATION
├── services/
│   ├── ai_client.py        # AIClient — verify_document_type(), extract_document_data(), generate_narrative()
│   ├── dms_client.py       # DMSClient — fetch_document(), upload_document()
│   └── edw_client.py       # EDWClient — export()
├── db/
│   ├── models.py           # SQLAlchemy ORM (all tables)
│   └── session.py          # AsyncSessionLocal, engine
└── pipeline/
    ├── processor.py        # ApplicationProcessor — Kafka consumer + full pipeline orchestration
    ├── report_generator.py # ReportGenerator — AI narrative + Jinja2 HTML + WeasyPrint PDF
    └── templates/
        └── report.html     # Jinja2 report template

mockups/
├── dms/app.py              # FastAPI DMS mockup (port 8001) — pre-seeded sample documents
├── edw/app.py              # FastAPI EDW mockup (port 8002) — idempotent case exports
└── crm/
    ├── publisher.py        # CLI: publishes Kafka events to credit-applications topic
    └── sample_data.py      # SAMPLE_EVENTS: two Personal Finance cases
```

## Architecture

### Processing flow (no synchronous API)

```
Siebel CRM → Kafka → ApplicationProcessor
                           ↓
                      (per document_id)
                      DMSClient.fetch_document()
                           ↓
                      AIClient.verify_document_type()
                           ↓ (if verified)
                      AIClient.extract_document_data()  →  StageOutputVersion (append-only)
                           ↓
                      ProductHandler.validate()          →  ValidationResultRow (append-only)
                           ↓
                      ReportGenerator.generate()
                        ├── AIClient.generate_narrative()
                        ├── Jinja2 HTML → CaseReport.html_content
                        ├── WeasyPrint PDF
                        └── DMSClient.upload_document()   →  CaseReport.pdf_dms_document_id
                           ↓
                      EDWStaging (Postgres) → EDWClient.export()
                           ↓
                      ApplicationCase.status = COMPLETED
```

### Key design constraints

- **Idempotent on `event_id`** — duplicate Kafka messages dropped at `InboundApplicationEvent` unique constraint.
- **Idempotent on `application_id`** — `ApplicationCase` unique constraint; re-consumed events find the existing case.
- **Append-only audit** — `StageOutputVersion` and `ValidationResultRow` are never updated, only inserted.
- **EDW write is last** — `edw_staging` row is written first; if export fails, the row stays `EXPORT_FAILED` and retries without re-running the pipeline.
- **Product handler strategy pattern** — adding a new product = new handler class + registry entry only.

### Adding a new product type

1. Add value to `ProductType` in [domain/enums.py](src/creditunder/domain/enums.py)
2. Create `handlers/your_product.py` extending `BaseProductHandler`
3. Register in [handlers/registry.py](src/creditunder/handlers/registry.py)

### Adding a new document type

1. Add value to `DocumentType` in [domain/enums.py](src/creditunder/domain/enums.py)
2. Create `documents/your_document.py` extending `BaseExtractionSchema`
3. Register in [documents/\_\_init\_\_.py](src/creditunder/documents/__init__.py) `EXTRACTION_SCHEMA_REGISTRY`

## AI Client

Configured via `.env` (`AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL`). Any OpenAI-compatible endpoint works.

Three operations on `AIClient`:
- `verify_document_type` — confirms document matches its expected `DocumentType`, returns `VerificationResult` with confidence
- `extract_document_data` — extracts fields per the registered Pydantic schema, returns per-field `{value, confidence, page_reference, normalized_label}`
- `generate_narrative` — writes prose narrative for the PDF report

All calls use `response_format={"type": "json_object"}` and `temperature=0` (narrative uses 0.3).

## Mockup Services

| Service | Port | Notes |
|---------|------|-------|
| DMS | 8001 | Pre-seeded: `DMS-00192` (ID doc), `DMS-00193` (salary cert). `GET /documents/{id}`, `POST /documents`, `GET /health` |
| EDW | 8002 | Idempotent on `application_id`. `POST /export`, `GET /exports/{application_id}`, `GET /health` |
| Redpanda | 19092 | External listener for host. Internal docker network: `redpanda:9092` |

## Sample Data

[mockups/crm/sample_data.py](mockups/crm/sample_data.py) contains two events:
- `APP-2026-089341` — happy path, salary matches
- `APP-2026-089342` — salary mismatch scenario (declared 25,000 vs certificate 18,500 → `SALARY_DEVIATION` SOFT_MISMATCH → HOLD)

## Environment

Copy `.env.example` to `.env`. Required: `AI_API_KEY`. All other vars default to docker-compose values.
