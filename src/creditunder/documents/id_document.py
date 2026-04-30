from datetime import date
from typing import Optional

from pydantic import Field

from creditunder.domain.enums import DocumentType
from creditunder.documents.base import BaseExtractionSchema


class IDDocumentExtraction(BaseExtractionSchema):
    full_name: str = Field(description="Full name as printed on the ID")
    id_number: str = Field(description="National ID number")
    date_of_birth: str = Field(description="Date of birth in YYYY-MM-DD format")
    expiry_date: str = Field(description="ID expiry date in YYYY-MM-DD format")
    nationality: str = Field(description="Nationality as printed")
    gender: Optional[str] = Field(default=None, description="Gender if printed")

    @classmethod
    def document_type(cls) -> DocumentType:
        return DocumentType.ID_DOCUMENT

    @classmethod
    def extraction_prompt(cls) -> str:
        return (
            "Extract the following fields from this Saudi National ID (Iqama or national ID card). "
            "Return all dates in YYYY-MM-DD format. "
            "If the document uses Hijri dates, convert them to Gregorian. "
            "If a field is not visible or legible, omit it from the response."
        )
