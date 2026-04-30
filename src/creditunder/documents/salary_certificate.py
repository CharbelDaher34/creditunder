from typing import Optional

from pydantic import Field

from creditunder.domain.enums import DocumentType
from creditunder.documents.base import BaseExtractionSchema


class SalaryCertificateExtraction(BaseExtractionSchema):
    employee_name: str = Field(description="Full name of the employee")
    employer_name: str = Field(description="Name of the employing organisation")
    basic_salary: float = Field(description="Basic monthly salary in SAR")
    housing_allowance: Optional[float] = Field(
        default=None, description="Monthly housing allowance in SAR if stated"
    )
    transport_allowance: Optional[float] = Field(
        default=None, description="Monthly transport allowance in SAR if stated"
    )
    total_salary: float = Field(description="Total monthly compensation in SAR")
    issue_date: str = Field(description="Date the certificate was issued in YYYY-MM-DD format")
    job_title: Optional[str] = Field(default=None, description="Employee's job title if stated")

    @classmethod
    def document_type(cls) -> DocumentType:
        return DocumentType.SALARY_CERTIFICATE

    @classmethod
    def extraction_prompt(cls) -> str:
        return (
            "Extract the following fields from this salary certificate. "
            "All monetary amounts must be in SAR (Saudi Riyals). "
            "Return all dates in YYYY-MM-DD format. "
            "If the total salary is not explicitly stated, compute it as the sum of all stated components."
        )
