from enum import Enum


class ProductType(str, Enum):
    PERSONAL_FINANCE = "PERSONAL_FINANCE"
    AUTO_FINANCE = "AUTO_FINANCE"


class DocumentType(str, Enum):
    ID_DOCUMENT = "ID_DOCUMENT"
    SALARY_CERTIFICATE = "SALARY_CERTIFICATE"
    BANK_STATEMENT = "BANK_STATEMENT"
    VEHICLE_QUOTE = "VEHICLE_QUOTE"


class ValidationOutcome(str, Enum):
    HARD_BREACH = "HARD_BREACH"
    SOFT_MISMATCH = "SOFT_MISMATCH"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class Recommendation(str, Enum):
    APPROVE = "APPROVE"
    HOLD = "HOLD"
    DECLINE = "DECLINE"


class CaseStatus(str, Enum):
    """Top-level lifecycle of an `application_case` row.

    CREATED                       — row inserted, processing not yet started.
    IN_PROGRESS                   — handler is running (documents fetched, AI calls in flight).
    COMPLETED                     — business processing produced a recommendation.
                                    Delivery (report upload + EDW write) tracked separately.
    FAILED                        — pipeline could not produce a recommendation
                                    (handler exception, invalid event, etc.). `error_detail` is set.
    MANUAL_INTERVENTION_REQUIRED  — pipeline cannot proceed without human action
                                    (missing required documents, retry exhaustion).
    """

    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    MANUAL_INTERVENTION_REQUIRED = "MANUAL_INTERVENTION_REQUIRED"


class DocumentStatus(str, Enum):
    PENDING = "PENDING"
    FETCHED = "FETCHED"
    VERIFIED = "VERIFIED"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    EXTRACTED = "EXTRACTED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"


class EDWStatus(str, Enum):
    STAGED = "STAGED"
    EXPORTED = "EXPORTED"
    EXPORT_FAILED = "EXPORT_FAILED"


class CaseReportStatus(str, Enum):
    """Lifecycle of report generation and DMS upload.

    PENDING     — row created, generation not yet started.
    HTML_READY  — narrative + Jinja2 HTML rendered, stored in `html_content`.
    PDF_READY   — HTML converted to PDF in memory; not yet uploaded.
    UPLOADED    — PDF written to DMS; `pdf_dms_document_id` populated.
    FAILED      — any step failed; `error_detail` is set with the reason.
    """

    PENDING = "PENDING"
    HTML_READY = "HTML_READY"
    PDF_READY = "PDF_READY"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"


class InboundEventStatus(str, Enum):
    """Lifecycle of an `inbound_application_event` row."""

    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobType(str, Enum):
    """`processing_job.job_type` values. One enum entry per retryable stage."""

    DOCUMENT_FETCH = "DOCUMENT_FETCH"
    VERIFY_AND_EXTRACT = "VERIFY_AND_EXTRACT"
    REPORT_GENERATION = "REPORT_GENERATION"
    REPORT_UPLOAD = "REPORT_UPLOAD"
    EDW_WRITE = "EDW_WRITE"


class JobStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
