from abc import ABC, abstractmethod

from creditunder.domain.enums import (
    DocumentType,
    ProductType,
    Recommendation,
    ValidationOutcome,
)
from creditunder.domain.models import (
    DocumentResult,
    ExtractedField,
    RequiredDocumentSet,
    ValidationResult,
)
from creditunder.validation_config import ValidationConfig, get_validation_config


class BaseProductHandler(ABC):
    @property
    @abstractmethod
    def product_type(self) -> ProductType: ...

    @abstractmethod
    def required_documents(self, applicant_data: dict) -> RequiredDocumentSet:
        """The document set this handler expects for *this* applicant.

        The set may depend on applicant context — most importantly
        `applicant_data["employer_snapshot"].employer_class`. Returns
        `RequiredDocumentSet(required, optional, unsupported)` so the
        Application Processor can flag missing items, accept optional
        ones, and reject explicitly unsupported uploads.
        """
        ...

    @abstractmethod
    def validate(
        self,
        application_id: str,
        applicant_data: dict,
        document_results: list[DocumentResult],
    ) -> tuple[list[ValidationResult], Recommendation, str]:
        """Apply product validation rules.

        Returns `(validation_results, recommendation, rationale)`.
        Each `ValidationResult` is stamped with `config_version` so the
        audit trail is reproducible across config / rules updates.
        """
        ...

    # ----------------------------------------------------------------- #
    #  Shared helpers — every handler stamps versions, threshold-checks  #
    #  confidence, and derives a recommendation the same way.            #
    # ----------------------------------------------------------------- #

    def _config(self) -> ValidationConfig:
        return get_validation_config()

    def _stamp_versions(self, result: ValidationResult) -> ValidationResult:
        result.config_version = self._config().version
        return result

    def _low_confidence_check(
        self,
        fields: dict[str, ExtractedField],
        document_type: DocumentType | str,
    ) -> list[ValidationResult]:
        cfg = self._config()
        doc_key = document_type.value if hasattr(document_type, "value") else str(document_type)
        threshold = cfg.confidence_threshold(doc_key)
        results = []
        for label, ef in fields.items():
            if ef.confidence < threshold:
                results.append(self._stamp_versions(
                    ValidationResult(
                        rule_code="LOW_CONFIDENCE",
                        outcome=ValidationOutcome.LOW_CONFIDENCE,
                        description=(
                            f"Field '{ef.normalized_label}' has low AI confidence "
                            f"({ef.confidence:.0%} < {threshold:.0%}). Manual review required."
                        ),
                        field_name=label,
                        extracted_value=ef.value,
                        confidence=ef.confidence,
                        manual_review_required=True,
                    )
                ))
        return results

    def _derive_recommendation(
        self, validation_results: list[ValidationResult]
    ) -> tuple[Recommendation, str]:
        mapping = self._config().recommendation_mapping
        outcomes = {r.outcome for r in validation_results}
        if ValidationOutcome.HARD_BREACH in outcomes:
            breaches = [r for r in validation_results if r.outcome == ValidationOutcome.HARD_BREACH]
            codes = ", ".join(r.rule_code for r in breaches)
            return mapping.on_hard_breach, f"Hard breach(es) detected: {codes}"
        if ValidationOutcome.MANUAL_REVIEW_REQUIRED in outcomes:
            return mapping.on_manual_review, "Manual review required on one or more fields."
        if ValidationOutcome.SOFT_MISMATCH in outcomes:
            return mapping.on_soft_mismatch, "Soft mismatches require validator attention."
        if ValidationOutcome.LOW_CONFIDENCE in outcomes:
            return mapping.on_low_confidence, "Low-confidence fields require validator attention."
        return mapping.default, "All validation rules passed with acceptable confidence."
