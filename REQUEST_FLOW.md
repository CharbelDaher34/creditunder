# Request Flow: Submit Application → Download PDF Report

End-to-end trace of every service call, from the user clicking **Submit** in the Workbench UI to downloading the final PDF credit report.

---

## Overview

```
SubmitModal.vue
    → Workbench Demo API  (POST /api/_demo/submit)
    → CRM Mockup          (POST /publish)
    → Kafka               (credit-applications topic)
    → ApplicationProcessor
        → DMS Mockup           (fetch source docs)
        → AI Service           (verify + extract fields)
        → ProductHandler       (apply business rules)
        → ReportGenerator
            → AI Service       (generate narrative)
            → DMS Mockup       (fetch source docs again for embedding)
        → DMS Mockup           (upload final PDF)
    → Workbench Cases API (GET /api/v1/cases/{id}/report)
    → DMS Mockup          (proxy PDF bytes to browser)
```

---

## Step 1 — User submits the application (SubmitModal.vue)

**Who:** Browser → Workbench Demo API  
**File:** [src/workbench-ui/src/components/SubmitModal.vue](src/workbench-ui/src/components/SubmitModal.vue)

The user fills in applicant data, picks a product type, and attaches documents (either file upload or pre-seeded DMS IDs).

If files are uploaded directly, the modal first converts each file to base64 and calls `POST /api/_demo/dms/upload` once per document. That endpoint proxies the upload to the DMS mockup and returns a `document_id`. This happens before submission so the main event only carries IDs, never raw bytes.

Once all document IDs are in hand the modal calls:

```
POST /api/_demo/submit
{
  product_type, branch_name, validator_id, supervisor_id,
  document_ids: { "ID_DOCUMENT": "DMS-00192", ... },
  applicant_data: { ... employer_snapshot ... }
}
```

---

## Step 2 — Workbench Demo router forwards to CRM (demo.py)

**Who:** Workbench Demo API → CRM Mockup  
**File:** [src/workbench/routers/demo.py](src/workbench/routers/demo.py)

`POST /api/_demo/submit` does almost nothing itself — it just proxies the request body to the CRM mockup:

```
POST {CRM_BASE_URL}/publish
```

The workbench has no Kafka producer of its own. All event publishing is owned by the CRM service, which is the system of record for application origination.

---

## Step 3 — CRM mockup publishes a Kafka event (crm/app.py)

**Who:** CRM Mockup → Kafka  
**File:** [mockups/crm/app.py](mockups/crm/app.py)

`POST /publish` generates:
- `event_id` — UUID, used for Kafka dedup
- `application_id` — `APP-{8-char hex}`, the business key

It wraps those together with the applicant data and document IDs into a JSON event and publishes it to the `credit-applications` Kafka topic. The HTTP response returns both IDs to the caller.

---

## Step 4 — ApplicationProcessor consumes the event (processor.py)

**Who:** ApplicationProcessor ← Kafka  
**File:** [src/creditunder/pipeline/processor.py](src/creditunder/pipeline/processor.py)

The processor is a long-running asyncio Kafka consumer. On each message:

1. **Dedup** — inserts an `InboundApplicationEvent` row. The `event_id` column has a unique constraint, so a duplicate Kafka delivery is silently dropped here.
2. **Case creation** — inserts or finds an `ApplicationCase` row (unique on `application_id`) and transitions it `CREATED → IN_PROGRESS`.
3. **Dispatch** — calls `_process_case()` which orchestrates all subsequent stages.

---

## Step 5 — Fetch source documents from DMS (processor.py → dms_client.py)

**Who:** ApplicationProcessor → DMS Client → DMS Mockup  
**Files:** [src/creditunder/pipeline/processor.py](src/creditunder/pipeline/processor.py), [src/creditunder/services/dms_client.py](src/creditunder/services/dms_client.py)

For each document ID in the event the processor calls:

```
GET {DMS_BASE_URL}/documents/{document_id}
```

All document fetches run concurrently via `asyncio.gather`. The DMS mockup returns the file as a base64-encoded payload. The DMS client decodes it and returns a `DMSDocument` object (raw bytes + content type + metadata).

Why fetch here and not later? The processor needs the bytes immediately to pass to the AI for extraction in the next step.

---

## Step 6 — AI verification and field extraction (processor.py → ai_client.py)

**Who:** ApplicationProcessor → AI Client → LLM  
**File:** [src/creditunder/services/ai_client.py](src/creditunder/services/ai_client.py)

For each fetched document the processor calls `verify_and_extract()`. A single LLM call does two things at once:

1. **Verify** — confirms the document is actually the expected type (e.g. this is really a national ID, not a utility bill).
2. **Extract** — pulls structured fields according to the document's pydantic-ai schema (e.g. `IDDocumentExtraction` or `SalaryCertificateExtraction`).

If the LLM raises a technical error (timeout, 500, context overflow) the exception propagates and the case lands on `FAILED`. If the LLM succeeds but says the document is the wrong type, it returns `is_correct_type=False` — a `DocumentResult(verification_passed=False)` is recorded and the handler will emit a `HARD_BREACH → DECLINE` in the next step.

Results are persisted as append-only `StageOutputVersion` rows so every re-run is auditable.

---

## Step 7 — Business rule validation (processor.py → PersonalFinanceHandler)

**Who:** ApplicationProcessor → ProductHandler  
**Files:** [src/creditunder/handlers/personal_finance.py](src/creditunder/handlers/personal_finance.py), [src/creditunder/handlers/base.py](src/creditunder/handlers/base.py)

The processor looks up the right handler from the registry (`get_handler(product_type)`) and calls `handler.validate()` with all extracted documents.

`PersonalFinanceHandler` applies:

| Rule | Type | Outcome |
|---|---|---|
| ID_NUMBER_MISMATCH | ID vs applicant_data | HARD_BREACH → DECLINE |
| ID_NAME_CHECK | ID vs applicant_data | SOFT_MISMATCH → HOLD |
| ID_EXPIRED | expiry date check | HARD_BREACH → DECLINE |
| SALARY_EMPLOYER_MATCH | cert vs applicant_data | SOFT_MISMATCH → HOLD |
| SALARY_DEVIATION | declared vs certified (tolerance from validation_config.yaml) | SOFT_MISMATCH → HOLD |
| LOW_CONFIDENCE | per-doc AI confidence below threshold | LOW_CONFIDENCE → HOLD |

Each `ValidationResult` row is stamped with `config_version` (from `validation_config.yaml`) and `employer_rule_version` (from the employer snapshot in the event) so the audit trail is reproducible.

The worst outcome across all rules determines the overall `recommendation`: any HARD_BREACH → DECLINE, any MANUAL_REVIEW_REQUIRED / SOFT_MISMATCH / LOW_CONFIDENCE → HOLD, otherwise → APPROVE.

---

## Step 8 — Report generation (report_generator.py)

**Who:** ApplicationProcessor → ReportGenerator → AI Client + DMS Client  
**File:** [src/creditunder/pipeline/report_generator.py](src/creditunder/pipeline/report_generator.py)

`ReportGenerator.generate()` runs after validation succeeds:

### 8a — AI narrative

Calls `ai_client.generate_narrative(case_summary)`. The LLM writes one concise paragraph that opens with APPROVE / HOLD / DECLINE, cites the key findings, and flags mandatory manual checks. Temperature is set to 0.3 here (slightly creative) versus 0 for extraction.

### 8b — Re-fetch source documents for embedding

Calls `dms_client.fetch_document()` once per source document again. The bytes are base64-encoded and injected into the HTML template as `data:` URIs so the final PDF is fully self-contained with no external references at render time.

### 8c — HTML + PDF render

Fills the Jinja2 template (`templates/report.html`) with:
- Case summary, recommendation, narrative
- Validation table (4-column: rule / outcome / details / manual-review flag)
- Document cards with embedded images/PDFs
- Manual checks panel
- Technical exceptions panel (grey, dashed border — never confused with a business DECLINE)

WeasyPrint converts the HTML to PDF bytes.

---

## Step 9 — Upload PDF to DMS (processor.py → dms_client.py)

**Who:** ApplicationProcessor → DMS Client → DMS Mockup  
**Files:** [src/creditunder/pipeline/processor.py](src/creditunder/pipeline/processor.py), [src/creditunder/services/dms_client.py](src/creditunder/services/dms_client.py)

```
POST {DMS_BASE_URL}/documents
{ content: <base64 PDF>, document_name: "report_APP-xxx.pdf", document_type: "CREDIT_REPORT", ... }
```

The DMS mockup assigns a new `DMS-{uuid}` ID and stores the file on disk. The processor persists that ID on the `CaseReport` row (`pdf_dms_document_id`) and sets `ApplicationCase.completed_at = now()`.

`completed_at` is only set after a successful DMS upload. If the upload fails, `completed_at` stays null and the error is recorded on `case_report.error_detail` — the case status is still `COMPLETED` (the business processing succeeded) but the report delivery is flagged as failed.

---

## Step 10 — User downloads the PDF (cases.py → DMS Mockup)

**Who:** Browser → Workbench Cases API → DMS Mockup  
**File:** [src/workbench/routers/cases.py](src/workbench/routers/cases.py)

When the user clicks "Download Report" in the UI:

```
GET /api/v1/cases/{application_id}/report
Headers: X-User-Id, X-User-Role
```

The Cases router:
1. Authenticates the user from the headers and applies role-based row scoping (`scope_query`): a VALIDATOR only sees cases where `validator_id == user_id`; a SUPERVISOR only sees `supervisor_id == user_id`.
2. Looks up `ApplicationCase` and its `CaseReport` to get `pdf_dms_document_id`.
3. Proxies a fetch from DMS:
   ```
   GET {DMS_BASE_URL}/documents/{pdf_dms_document_id}
   ```
4. Decodes the base64 payload and streams the raw bytes back to the browser as `application/pdf` with a `Content-Disposition: attachment` header.

The browser receives the PDF directly — the workbench API acts as an auth-gated proxy so the DMS is never exposed to the client.

---

## Service call map

```
Browser
  │
  ├─ POST /api/_demo/dms/upload (per file)     → Workbench Demo API
  │                                             → DMS Mockup  POST /documents
  │
  ├─ POST /api/_demo/submit                    → Workbench Demo API
  │                                             → CRM Mockup  POST /publish
  │                                                          → Kafka (publish)
  │
  │   [async — processor picks up from Kafka]
  │
  │   ApplicationProcessor
  │     ├─ DMS Mockup  GET /documents/{id}     (×N, concurrent, fetch source docs)
  │     ├─ LLM         verify_and_extract      (×N, concurrent)
  │     ├─ ProductHandler.validate()           (in-process)
  │     └─ ReportGenerator.generate()
  │           ├─ LLM   generate_narrative      (1 call)
  │           ├─ DMS Mockup  GET /documents    (×N, embed as data URIs)
  │           ├─ WeasyPrint  HTML → PDF        (in-process)
  │           └─ DMS Mockup  POST /documents   (upload PDF)
  │
  └─ GET /api/v1/cases/{id}/report             → Workbench Cases API
                                               → DMS Mockup  GET /documents/{pdf_id}
                                               ← stream PDF to browser
```
