# Application Processor — Flow & Integration

How the pipeline app works, how it starts, and how it connects to every adjacent system. Workbench is excluded; it sits downstream as a read-only consumer and is not on the write path.

---

## 1. File structure (processor + DMS + CRM)

### Processor — `src/creditunder/`

```
src/creditunder/
├── __main__.py                    # Entrypoint
├── Dockerfile
├── entrypoint.sh                  # Waits for infra, runs migrations, starts the processor
├── config.py                      # All environment-driven settings
├── observability.py               # structlog configuration
├── validation_config.yaml         # Versioned thresholds + outcome → recommendation mapping
├── validation_config.py           # Config loader (cached)
│
├── domain/
│   ├── enums.py                   # All enum types (ProductType, CaseStatus, DocumentType, …)
│   └── models.py                  # Domain models: event payload, document result, case result, …
│
├── documents/
│   ├── __init__.py                # Registry: DocumentType → extraction schema
│   ├── base.py                    # Base extraction schema
│   ├── id_document.py             # ID document extraction schema
│   └── salary_certificate.py      # Salary certificate extraction schema
│
├── handlers/
│   ├── base.py                    # Abstract product handler (required documents + validate)
│   ├── registry.py                # Resolves ProductType → handler instance
│   └── personal_finance.py        # Personal Finance business rules
│
├── services/
│   ├── ai_client.py               # AI service client (verify & extract, generate narrative)
│   └── dms_client.py              # DMS client (fetch document, upload document)
│
├── db/
│   ├── models.py                  # All database tables (ORM)
│   └── session.py                 # Async database session
│
└── pipeline/
    ├── processor.py               # ★ ApplicationProcessor — orchestrates the full pipeline
    ├── report_generator.py        # Builds HTML report and renders it to PDF
    └── templates/
        └── report.html            # Report Jinja2 template
```

### DMS mockup — `mockups/dms/`

```
mockups/dms/
├── Dockerfile
└── app.py                         # Disk-backed document store
                                   # Fetch by ID, upload, list, preview
                                   # Pre-seeded with sample ID doc + salary certificate
```

### CRM mockup — `mockups/crm/`

```
mockups/crm/
├── Dockerfile
├── app.py                         # HTTP endpoint → publishes event to Kafka
├── publisher.py                   # CLI tool to publish sample events
└── sample_data.py                 # Two sample cases: happy path (APPROVE) + salary mismatch (HOLD)
```

---

## 2. Startup

When the processor container starts, it:

1. Waits for Postgres and Kafka to be reachable.
2. Runs database migrations.
3. Checks for any cases that were left in a non-terminal state from a previous run (e.g. the process was killed mid-flight). Those cases are immediately marked **FAILED** so operators can see them and resubmit — they cannot be safely resumed.
4. Opens the Kafka consumer and begins processing messages.

---

## 3. Pipeline flow

One Kafka message produces one processed case and one PDF delivered to DMS.

```
Kafka message
    │
    ▼
Validate & deduplicate
    │  invalid schema → dead letter, no case created
    │  duplicate event_id → dropped
    │
    ▼
Create application case  (status: IN_PROGRESS)
    │
    ▼
Fetch all documents from DMS  ◄── concurrent, one per document ID
    │
    ▼
AI: verify + extract each document  ◄── single LLM call per document
    │  wrong document type → business DECLINE path
    │  AI error → case FAILED (technical, not a business outcome)
    │
    ▼
Completeness check
    │  missing required documents → MANUAL_INTERVENTION_REQUIRED
    │
    ▼
Product handler: apply business rules  →  recommendation + rationale
    │
    ▼
Case marked COMPLETED, results persisted
    │
    ▼
Report generator
    ├── AI: generate narrative paragraph
    ├── Re-fetch source documents (embedded into PDF as self-contained data)
    ├── Render HTML → PDF
    └── Upload PDF to DMS
    │  report failure tracked separately; case stays COMPLETED
    │
    ▼
Snapshot written to edw_staging  →  Workbench reads from here
```

---

## 4. Integration map

| System | Role |
|---|---|
| **Siebel CRM** | Publishes application events to Kafka. Never called back by the processor — CRM owns all case actions (approve/reject/override). |
| **Kafka** | Durable event bus. The processor consumes one event per application, commits only after the outcome is persisted, so an unhandled crash does not lose the message. |
| **PostgreSQL** | Single source of truth for all pipeline state: case, documents, extractions, validation results, report, audit trail, retry jobs, dead letters. |
| **DMS** | Source of application documents (fetched during extraction and again when building the report). Destination for the final PDF. |
| **AI service** | Two responsibilities per case: (1) verify each document matches its declared type and extract structured fields; (2) write a one-paragraph narrative for the report. Any OpenAI-compatible endpoint can be used. |
| **Product handler** | In-process strategy per product type. Declares what documents are required (which may vary by employer class) and applies the business validation rules. Adding a new product = new handler class only. |
| **Report generator** | In-process. Composes all case data, AI narrative, and source document images into an HTML template, then renders it to a self-contained PDF. |
| **edw_staging** | Denormalised snapshot table. The processor writes here — once at IN_PROGRESS (so the case appears on the board immediately) and once at completion (full payload). The Workbench reads only this table. |

---

## 5. Key design properties

| Property | How it works |
|---|---|
| **Idempotent on redelivery** | Duplicate Kafka messages are dropped on the unique event ID. Duplicate application IDs find the existing case. |
| **Append-only audit** | Extraction versions and validation results are never updated — only new rows are inserted. History is never overwritten. |
| **Business vs technical failure** | An AI service error (timeout, 5xx) fails the case technically. A wrong document type detected by the AI is a business outcome that flows to DECLINE. These are intentionally different. |
| **Delivery is independent of processing** | The case is marked COMPLETED once business validation finishes. Report generation and PDF upload are tracked separately — a delivery failure does not undo the business result. |
| **Startup crash recovery** | Any case stuck in a non-terminal state at startup is failed immediately so it is visible to ops, rather than silently blocking. |
| **Retries on external calls** | DMS and AI calls are retried with exponential backoff. Each attempt is tracked in the database, giving ops a clear picture of where time was spent and what failed. |
| **edw_staging write is non-blocking** | A failure to write the workbench snapshot is logged but does not affect the case outcome. The case stays COMPLETED and the snapshot can be resynced. |
