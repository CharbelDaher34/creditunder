from abc import ABC, abstractmethod
from dataclasses import dataclass

from creditunder.domain.enums import DocumentType, ProductType, Recommendation, ValidationOutcome
from creditunder.domain.models import (
    CaseResult,
    DocumentResult,
    ExtractedField,
    ValidationResult,
)


class BaseProductHandler(ABC):
    @property
    @abstractmethod
    def product_type(self) -> ProductType: ...

    @property
    @abstractmethod
    def required_documents(self) -> list[DocumentType]: ...

    @abstractmethod
    def validate(
        self,
        application_id: str,
        applicant_data: dict,
        document_results: list[DocumentResult],
    ) -> tuple[list[ValidationResult], Recommendation, str]:
        """
        Apply product validation rules.
        Returns (validation_results, recommendation, rationale).
        """
        ...

    def _low_confidence_check(
        self,
        fields: dict[str, ExtractedField],
        threshold: float = 0.7,
    ) -> list[ValidationResult]:
        results = []
        for label, ef in fields.items():
            if ef.confidence < threshold:
                results.append(
                    ValidationResult(
                        rule_code="LOW_CONFIDENCE",
                        outcome=ValidationOutcome.LOW_CONFIDENCE,
                        description=(
                            f"Field '{ef.normalized_label}' has low AI confidence "
                            f"({ef.confidence:.0%}). Manual review required."
                        ),
                        field_name=label,
                        extracted_value=ef.value,
                        confidence=ef.confidence,
                        manual_review_required=True,
                    )
                )
        return results

    def _derive_recommendation(
        self, validation_results: list[ValidationResult]
    ) -> tuple[Recommendation, str]:
        outcomes = {r.outcome for r in validation_results}
        if ValidationOutcome.HARD_BREACH in outcomes:
            breaches = [r for r in validation_results if r.outcome == ValidationOutcome.HARD_BREACH]
            codes = ", ".join(r.rule_code for r in breaches)
            return Recommendation.DECLINE, f"Hard breach(es) detected: {codes}"
        if ValidationOutcome.MANUAL_REVIEW_REQUIRED in outcomes:
            return Recommendation.HOLD, "Manual review required on one or more fields."
        if ValidationOutcome.LOW_CONFIDENCE in outcomes or ValidationOutcome.SOFT_MISMATCH in outcomes:
            return Recommendation.HOLD, "Soft mismatches or low-confidence fields require validator attention."
        return Recommendation.APPROVE, "All validation rules passed with acceptable confidence."
