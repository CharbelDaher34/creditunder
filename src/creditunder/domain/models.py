from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel

from creditunder.domain.enums import (
    DocumentType,
    ProductType,
    Recommendation,
    ValidationOutcome,
)

T = TypeVar("T")


class ExtractedField(BaseModel, Generic[T]):
    value: T
    confidence: float
    source_document_name: str
    page_reference: int
    normalized_label: str


# --------------------------------------------------------------------- #
#  Employer snapshot (carried inside applicant_data)                     #
# --------------------------------------------------------------------- #


EmployerClass = Literal["A", "B", "C", "D", "GOV"]


class EmployerSnapshot(BaseModel):
    """Employer information snapshotted by CRM at event-publish time.

    Carried inside `applicant_data["employer_snapshot"]`. CRM resolves the
    employer record against the governed employer-rules source and embeds
    the snapshot here so the handler never needs to fetch it.
    """

    employer_id: str
    employer_name_normalized: str
    employer_class: EmployerClass

    @classmethod
    def from_applicant_data(cls, applicant_data: dict | None) -> "EmployerSnapshot | None":
        if not applicant_data:
            return None
        raw = applicant_data.get("employer_snapshot")
        if not raw:
            return None
        return cls.model_validate(raw)


# --------------------------------------------------------------------- #
#  Required-document set (returned by handlers)                          #
# --------------------------------------------------------------------- #


@dataclass
class RequiredDocumentSet:
    """What a product handler expects, given the applicant context.

    `required` — must all be present, otherwise the case is moved to
        `MANUAL_INTERVENTION_REQUIRED`.
    `optional` — accepted if present, ignored if absent.
    `unsupported` — must NOT be present; flagged if uploaded.
    """

    required: set[DocumentType]
    optional: set[DocumentType] = field(default_factory=set)
    unsupported: set[DocumentType] = field(default_factory=set)


# --------------------------------------------------------------------- #
#  Validation results                                                    #
# --------------------------------------------------------------------- #


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
    rule_version: str | None = None
    config_version: str | None = None            # set automatically from validation_config.yaml


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


# --------------------------------------------------------------------- #
#  Inbound Kafka event                                                   #
# --------------------------------------------------------------------- #


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
