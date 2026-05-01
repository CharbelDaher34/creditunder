from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel

from creditunder.domain.enums import (
    DocumentType,
    ProductType,
    Recommendation,
    ValidationOutcome,
)

T = TypeVar("T")


@dataclass
class ExtractedField(Generic[T]):
    value: T
    confidence: float
    source_document_name: str
    page_reference: int
    normalized_label: str


@dataclass
class ValidationResult:
    rule_code: str
    outcome: ValidationOutcome
    description: str
    field_name: str | None = None
    extracted_value: Any | None = None
    expected_value: Any | None = None
    confidence: float | None = None
    manual_review_required: bool = False


@dataclass
class DocumentResult:
    document_id: str
    document_type: DocumentType
    document_name: str
    verification_passed: bool
    verification_confidence: float
    extracted_data: dict[str, ExtractedField]


@dataclass
class CaseResult:
    application_id: str
    product_type: ProductType
    document_results: list[DocumentResult]
    validation_results: list[ValidationResult]
    recommendation: Recommendation
    recommendation_rationale: str
    manual_review_required: bool = False
    completed_at: datetime | None = None


# Kafka inbound event model
class ApplicationEvent(BaseModel):
    event_id: UUID
    application_id: str
    product_type: ProductType
    branch_name: str
    validator_id: str
    supervisor_id: str
    document_ids: list[str]
    applicant_data: dict[str, Any]

    class Config:
        use_enum_values = True
