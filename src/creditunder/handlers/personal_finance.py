from datetime import date

from creditunder.domain.enums import DocumentType, ProductType, ValidationOutcome
from creditunder.domain.models import (
    DocumentResult,
    EmployerSnapshot,
    ExtractedField,
    RequiredDocumentSet,
    ValidationResult,
)
from creditunder.handlers.base import BaseProductHandler


class PersonalFinanceHandler(BaseProductHandler):
    @property
    def product_type(self) -> ProductType:
        return ProductType.PERSONAL_FINANCE

    # BR-13 Personal Finance employer-class document matrix:
    #   Class A     → Salary Certificate (+ ID)
    #   Class B     → Salary Certificate + Salary Transfer Letter (+ ID)
    #   Class C/D   → Subscription & Wage Certificate (+ ID)
    #
    # Only ID_DOCUMENT and SALARY_CERTIFICATE are modelled in the document
    # registry today, so every class collapses to the same MVP set. Add a
    # branch here once SALARY_TRANSFER_LETTER / SUBSCRIPTION_WAGE_CERTIFICATE
    # are registered.
    def required_documents(self, applicant_data: dict) -> RequiredDocumentSet:
        return RequiredDocumentSet(
            required={DocumentType.ID_DOCUMENT, DocumentType.SALARY_CERTIFICATE}
        )

    # ----------------------------------------------------------------- #
    #  Validation                                                        #
    # ----------------------------------------------------------------- #

    def validate(self, application_id, applicant_data, document_results):
        employer = EmployerSnapshot.from_applicant_data(applicant_data)
        results: list[ValidationResult] = []

        id_doc = self._find_doc(document_results, DocumentType.ID_DOCUMENT)
        salary_doc = self._find_doc(document_results, DocumentType.SALARY_CERTIFICATE)

        # Verification rules — always emit one row each regardless of outcome.
        id_verified = bool(id_doc and id_doc.verification_passed)
        results.append(self._stamp_versions(
            ValidationResult(
                rule_code="ID_VERIFICATION_FAILED",
                outcome=ValidationOutcome.PASS if id_verified else ValidationOutcome.HARD_BREACH,
                description=(
                    "ID document verified as a valid national ID."
                    if id_verified
                    else "ID document could not be verified as a valid national ID."
                ),
            )
        ))

        salary_verified = bool(salary_doc and salary_doc.verification_passed)
        results.append(self._stamp_versions(
            ValidationResult(
                rule_code="SALARY_CERT_VERIFICATION_FAILED",
                outcome=ValidationOutcome.PASS if salary_verified else ValidationOutcome.HARD_BREACH,
                description=(
                    "Salary certificate verified successfully."
                    if salary_verified
                    else "Salary certificate could not be verified."
                ),
            )
        ))

        if id_doc:
            results += self._low_confidence_check(id_doc.extracted_data, DocumentType.ID_DOCUMENT)
            results += self._validate_id(applicant_data, id_doc.extracted_data)

        if salary_doc:
            results += self._low_confidence_check(
                salary_doc.extracted_data, DocumentType.SALARY_CERTIFICATE
            )
            results += self._validate_salary(applicant_data, salary_doc.extracted_data, employer)

        recommendation, rationale = self._derive_recommendation(results)
        return results, recommendation, rationale

    # ----------------------------------------------------------------- #
    #  Rule implementations                                              #
    # ----------------------------------------------------------------- #

    def _validate_id(
        self, applicant_data: dict, fields: dict[str, ExtractedField]
    ) -> list[ValidationResult]:
        results = []

        # Rule: ID number must match CRM record — always emits a row.
        extracted_id = str(fields["id_number"].value).strip() if "id_number" in fields else None
        declared_id = str(applicant_data.get("id_number", "")).strip()
        if extracted_id is not None:
            mismatch = extracted_id != declared_id
            results.append(self._stamp_versions(
                ValidationResult(
                    rule_code="ID_NUMBER_MISMATCH",
                    outcome=ValidationOutcome.HARD_BREACH if mismatch else ValidationOutcome.PASS,
                    description=(
                        "ID number on document does not match CRM record."
                        if mismatch
                        else "ID number matches CRM record."
                    ),
                    field_name="id_number",
                    extracted_value=extracted_id,
                    expected_value=declared_id,
                )
            ))

        # Rule: Name similarity check — always emits a row.
        extracted_name = str(fields["full_name"].value).strip().upper() if "full_name" in fields else None
        declared_name = str(applicant_data.get("name", "")).strip().upper()
        if extracted_name is not None:
            name_mismatch = extracted_name != declared_name
            results.append(self._stamp_versions(
                ValidationResult(
                    rule_code="ID_NAME_CHECK",
                    outcome=ValidationOutcome.SOFT_MISMATCH if name_mismatch else ValidationOutcome.PASS,
                    description=(
                        "Name on ID does not exactly match CRM record (may be transliteration)."
                        if name_mismatch
                        else "Name on ID matches CRM record."
                    ),
                    field_name="full_name",
                    extracted_value=extracted_name,
                    expected_value=declared_name,
                )
            ))

        # Rule: ID must not be expired — always emits a row.
        if "expiry_date" in fields:
            try:
                expiry = date.fromisoformat(str(fields["expiry_date"].value))
                expired = expiry < date.today()
                results.append(self._stamp_versions(
                    ValidationResult(
                        rule_code="ID_EXPIRED",
                        outcome=ValidationOutcome.HARD_BREACH if expired else ValidationOutcome.PASS,
                        description=(
                            f"ID document expired on {expiry}."
                            if expired
                            else f"ID document valid until {expiry}."
                        ),
                        field_name="expiry_date",
                        extracted_value=str(expiry),
                    )
                ))
            except ValueError:
                results.append(self._stamp_versions(
                    ValidationResult(
                        rule_code="ID_EXPIRED",
                        outcome=ValidationOutcome.MANUAL_REVIEW_REQUIRED,
                        description="Could not parse ID expiry date. Manual review required.",
                        field_name="expiry_date",
                        manual_review_required=True,
                    )
                ))

        return results

    def _validate_salary(
        self,
        applicant_data: dict,
        fields: dict[str, ExtractedField],
        employer: EmployerSnapshot | None,
    ) -> list[ValidationResult]:
        cfg = self._config()
        results = []

        # Rule: Employer must match CRM record — always emits a row.
        # When an employer snapshot is present, prefer the normalized name
        # from the rules source as the canonical comparison anchor (BR-07).
        if "employer_name" in fields:
            extracted_employer = str(fields["employer_name"].value).strip().upper()
            if employer is not None:
                expected_employer = employer.employer_name_normalized.strip().upper()
            else:
                expected_employer = str(applicant_data.get("employer", "")).strip().upper()
            employer_mismatch = extracted_employer != expected_employer
            results.append(self._stamp_versions(
                ValidationResult(
                    rule_code="SALARY_EMPLOYER_MATCH",
                    outcome=ValidationOutcome.SOFT_MISMATCH if employer_mismatch else ValidationOutcome.PASS,
                    description=(
                        "Employer on salary certificate does not match CRM record."
                        if employer_mismatch
                        else "Employer on salary certificate matches CRM record."
                    ),
                    field_name="employer_name",
                    extracted_value=extracted_employer,
                    expected_value=expected_employer,
                )
            ))

        # Rule: Salary within configured tolerance of declared salary — always emits a row.
        tolerance = cfg.personal_finance.salary_deviation_tolerance
        if "total_salary" in fields:
            try:
                extracted_salary = float(fields["total_salary"].value)
                declared_salary = float(applicant_data.get("declared_salary", 0))
                if declared_salary > 0:
                    deviation = abs(extracted_salary - declared_salary) / declared_salary
                    breached = deviation > tolerance
                    results.append(self._stamp_versions(
                        ValidationResult(
                            rule_code="SALARY_DEVIATION",
                            outcome=ValidationOutcome.SOFT_MISMATCH if breached else ValidationOutcome.PASS,
                            description=(
                                f"Salary deviation {deviation:.1%} exceeds "
                                f"{tolerance:.0%} tolerance. "
                                f"Extracted: {extracted_salary:,.0f} SAR, "
                                f"Declared: {declared_salary:,.0f} SAR."
                                if breached
                                else
                                f"Salary within {tolerance:.0%} tolerance "
                                f"(deviation {deviation:.1%}). "
                                f"Extracted: {extracted_salary:,.0f} SAR, "
                                f"Declared: {declared_salary:,.0f} SAR."
                            ),
                            field_name="total_salary",
                            extracted_value=extracted_salary,
                            expected_value=declared_salary,
                        )
                    ))
            except (ValueError, TypeError):
                results.append(self._stamp_versions(
                    ValidationResult(
                        rule_code="SALARY_DEVIATION",
                        outcome=ValidationOutcome.MANUAL_REVIEW_REQUIRED,
                        description="Could not parse salary amount. Manual review required.",
                        field_name="total_salary",
                        manual_review_required=True,
                    )
                ))

        return results

    @staticmethod
    def _find_doc(
        document_results: list[DocumentResult], doc_type: DocumentType
    ) -> DocumentResult | None:
        return next((d for d in document_results if d.document_type == doc_type), None)
