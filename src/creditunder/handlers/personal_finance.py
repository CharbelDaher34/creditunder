from datetime import date

from creditunder.domain.enums import DocumentType, ProductType, ValidationOutcome
from creditunder.domain.models import DocumentResult, ExtractedField, ValidationResult
from creditunder.handlers.base import BaseProductHandler


class PersonalFinanceHandler(BaseProductHandler):
    @property
    def product_type(self) -> ProductType:
        return ProductType.PERSONAL_FINANCE

    @property
    def required_documents(self) -> list[DocumentType]:
        return [DocumentType.ID_DOCUMENT, DocumentType.SALARY_CERTIFICATE]

    def validate(self, application_id, applicant_data, document_results):
        results: list[ValidationResult] = []

        id_doc = self._find_doc(document_results, DocumentType.ID_DOCUMENT)
        salary_doc = self._find_doc(document_results, DocumentType.SALARY_CERTIFICATE)

        if id_doc:
            results += self._low_confidence_check(id_doc.extracted_data)
            results += self._validate_id(applicant_data, id_doc.extracted_data)

        if salary_doc:
            results += self._low_confidence_check(salary_doc.extracted_data)
            results += self._validate_salary(applicant_data, salary_doc.extracted_data)

        if not id_doc or not id_doc.verification_passed:
            results.append(
                ValidationResult(
                    rule_code="ID_VERIFICATION_FAILED",
                    outcome=ValidationOutcome.HARD_BREACH,
                    description="ID document could not be verified as a valid national ID.",
                )
            )

        if not salary_doc or not salary_doc.verification_passed:
            results.append(
                ValidationResult(
                    rule_code="SALARY_CERT_VERIFICATION_FAILED",
                    outcome=ValidationOutcome.HARD_BREACH,
                    description="Salary certificate could not be verified.",
                )
            )

        recommendation, rationale = self._derive_recommendation(results)
        return results, recommendation, rationale

    # ---- rule implementations ----

    def _validate_id(
        self, applicant_data: dict, fields: dict[str, ExtractedField]
    ) -> list[ValidationResult]:
        results = []

        # Rule: ID number must match CRM record
        if "id_number" in fields:
            extracted_id = str(fields["id_number"].value).strip()
            declared_id = str(applicant_data.get("id_number", "")).strip()
            if extracted_id != declared_id:
                results.append(
                    ValidationResult(
                        rule_code="ID_NUMBER_MISMATCH",
                        outcome=ValidationOutcome.HARD_BREACH,
                        description="ID number on document does not match CRM record.",
                        field_name="id_number",
                        extracted_value=extracted_id,
                        expected_value=declared_id,
                    )
                )

        # Rule: Name similarity check (case-insensitive, trimmed)
        if "full_name" in fields:
            extracted_name = str(fields["full_name"].value).strip().upper()
            declared_name = str(applicant_data.get("name", "")).strip().upper()
            if extracted_name != declared_name:
                results.append(
                    ValidationResult(
                        rule_code="ID_NAME_CHECK",
                        outcome=ValidationOutcome.SOFT_MISMATCH,
                        description="Name on ID does not exactly match CRM record (may be transliteration).",
                        field_name="full_name",
                        extracted_value=extracted_name,
                        expected_value=declared_name,
                    )
                )

        # Rule: ID must not be expired
        if "expiry_date" in fields:
            try:
                expiry = date.fromisoformat(str(fields["expiry_date"].value))
                if expiry < date.today():
                    results.append(
                        ValidationResult(
                            rule_code="ID_EXPIRED",
                            outcome=ValidationOutcome.HARD_BREACH,
                            description=f"ID document expired on {expiry}.",
                            field_name="expiry_date",
                            extracted_value=str(expiry),
                        )
                    )
            except ValueError:
                results.append(
                    ValidationResult(
                        rule_code="ID_EXPIRY_PARSE_ERROR",
                        outcome=ValidationOutcome.MANUAL_REVIEW_REQUIRED,
                        description="Could not parse ID expiry date. Manual review required.",
                        field_name="expiry_date",
                        manual_review_required=True,
                    )
                )

        return results

    def _validate_salary(
        self, applicant_data: dict, fields: dict[str, ExtractedField]
    ) -> list[ValidationResult]:
        results = []

        # Rule: Employer must match CRM record
        if "employer_name" in fields:
            extracted_employer = str(fields["employer_name"].value).strip().upper()
            declared_employer = str(applicant_data.get("employer", "")).strip().upper()
            if extracted_employer != declared_employer:
                results.append(
                    ValidationResult(
                        rule_code="SALARY_EMPLOYER_MATCH",
                        outcome=ValidationOutcome.SOFT_MISMATCH,
                        description="Employer on salary certificate does not match CRM record.",
                        field_name="employer_name",
                        extracted_value=extracted_employer,
                        expected_value=declared_employer,
                    )
                )

        # Rule: Salary within 10% of declared salary
        if "total_salary" in fields:
            try:
                extracted_salary = float(fields["total_salary"].value)
                declared_salary = float(applicant_data.get("declared_salary", 0))
                if declared_salary > 0:
                    deviation = abs(extracted_salary - declared_salary) / declared_salary
                    if deviation > 0.10:
                        results.append(
                            ValidationResult(
                                rule_code="SALARY_DEVIATION",
                                outcome=ValidationOutcome.SOFT_MISMATCH,
                                description=(
                                    f"Salary deviation {deviation:.1%} exceeds 10% tolerance. "
                                    f"Extracted: {extracted_salary:,.0f} SAR, "
                                    f"Declared: {declared_salary:,.0f} SAR."
                                ),
                                field_name="total_salary",
                                extracted_value=extracted_salary,
                                expected_value=declared_salary,
                            )
                        )
            except (ValueError, TypeError):
                results.append(
                    ValidationResult(
                        rule_code="SALARY_PARSE_ERROR",
                        outcome=ValidationOutcome.MANUAL_REVIEW_REQUIRED,
                        description="Could not parse salary amount. Manual review required.",
                        field_name="total_salary",
                        manual_review_required=True,
                    )
                )

        return results

    @staticmethod
    def _find_doc(
        document_results: list[DocumentResult], doc_type: DocumentType
    ) -> DocumentResult | None:
        return next((d for d in document_results if d.document_type == doc_type), None)
