from typing import Optional

from pydantic import Field

from creditunder.domain.enums import DocumentType
from creditunder.domain.models import ExtractedField
from creditunder.documents.base import BaseExtractionSchema


class SalaryCertificateExtraction(BaseExtractionSchema):
    employee_name: ExtractedField[str]
    employer_name: ExtractedField[str]
    basic_salary: ExtractedField[float] = Field(description="Basic monthly salary in SAR")
    housing_allowance: Optional[ExtractedField[float]] = Field(
        default=None, description="Monthly housing allowance in SAR if stated"
    )
    transport_allowance: Optional[ExtractedField[float]] = Field(
        default=None, description="Monthly transport allowance in SAR if stated"
    )
    total_salary: ExtractedField[float] = Field(description="Total monthly compensation in SAR")
    issue_date: ExtractedField[str] = Field(description="Date the certificate was issued in YYYY-MM-DD format")
    job_title: Optional[ExtractedField[str]] = None

    @classmethod
    def document_type(cls) -> DocumentType:
        return DocumentType.SALARY_CERTIFICATE

    @classmethod
    def verification_prompt(cls) -> str:
        return (
            "A valid SALARY_CERTIFICATE is an official letter issued by an employer certifying "
            "an employee's salary. It must state the employer name, employee name, and a salary amount. "
            "National ID cards, bank statements, or any other document type do not qualify."
        )

    @classmethod
    def extraction_prompt(cls) -> str:
        return (
            "Extract the following fields from this salary certificate. "
            "All monetary amounts must be in SAR (Saudi Riyals). "
            "Return all dates in YYYY-MM-DD format. "
            "If the total salary is not explicitly stated, compute it as the sum of all stated components."
        )
