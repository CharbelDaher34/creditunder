from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CaseListItem(BaseModel):
    id: UUID
    application_id: str
    applicant_name: str | None
    product_type: str
    branch_name: str
    validator_id: str
    status: str
    recommendation: str | None
    manual_review_required: bool
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ExtractedFieldOut(BaseModel):
    label: str
    value: Any
    confidence: float | None
    page_reference: int | None


class DocumentDetail(BaseModel):
    id: UUID
    dms_document_id: str
    document_name: str | None
    document_type: str | None
    status: str
    verification_passed: bool | None
    verification_confidence: float | None
    fetched_at: datetime | None
    verified_at: datetime | None
    error_detail: str | None
    extracted_fields: list[ExtractedFieldOut]


class ValidationDetail(BaseModel):
    id: UUID
    rule_code: str
    outcome: str
    description: str
    field_name: str | None
    extracted_value: str | None
    expected_value: str | None
    confidence: float | None
    manual_review_required: bool
    rule_version: str | None
    employer_rule_version: str | None
    config_version: str | None
    evaluated_at: datetime


class ValidationGroups(BaseModel):
    """Validation rows grouped by outcome — order matters for the UI."""

    hard_breach: list[ValidationDetail] = Field(default_factory=list)
    soft_mismatch: list[ValidationDetail] = Field(default_factory=list)
    low_confidence: list[ValidationDetail] = Field(default_factory=list)
    manual_review: list[ValidationDetail] = Field(default_factory=list)


class ReportSummary(BaseModel):
    status: str | None
    pdf_available: bool
    pdf_uploaded_at: datetime | None
    error_detail: str | None


class AuditEntryOut(BaseModel):
    id: UUID
    event_type: str
    actor: str | None
    detail: dict | None
    occurred_at: datetime


class ManualCheckItem(BaseModel):
    """Item in the Manual Checks panel (BR-30 / BR-33)."""

    rule_code: str
    description: str
    field_name: str | None
    reference: str | None = None
    kind: str = "RULE"  # RULE | STAMP_AND_SIGNATURE


class TechnicalException(BaseModel):
    """Pipeline / integration failure. Visually distinct from business outcomes (BR-37)."""

    kind: str
    description: str
    reference: str | None


class CaseDetail(BaseModel):
    case: dict
    employer_snapshot: dict | None
    documents: list[DocumentDetail]
    validations: ValidationGroups
    manual_checks: list[ManualCheckItem]
    technical_exceptions: list[TechnicalException]
    report: ReportSummary
    audit_timeline: list[AuditEntryOut]


class CaseStatusCounts(BaseModel):
    """Dashboard header KPIs."""

    total: int
    in_progress: int
    completed: int
    failed: int
    manual_intervention_required: int
    approve: int
    hold: int
    decline: int
