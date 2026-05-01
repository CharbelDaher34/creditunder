from typing import Optional

from pydantic import Field

from creditunder.domain.enums import DocumentType
from creditunder.domain.models import ExtractedField
from creditunder.documents.base import BaseExtractionSchema


class IDDocumentExtraction(BaseExtractionSchema):
    full_name: ExtractedField[str]
    id_number: ExtractedField[str]
    date_of_birth: ExtractedField[str] = Field(description="Date of birth in YYYY-MM-DD format")
    expiry_date: ExtractedField[str] = Field(description="ID expiry date in YYYY-MM-DD format")
    nationality: ExtractedField[str]
    gender: Optional[ExtractedField[str]] = None

    @classmethod
    def document_type(cls) -> DocumentType:
        return DocumentType.ID_DOCUMENT

    @classmethod
    def verification_prompt(cls) -> str:
        return (
            "A valid ID_DOCUMENT is a Saudi National ID card or Iqama. "
            "It must show a full name, a national ID number, an expiry date, and a nationality. "
            "Salary certificates, bank statements, or any other document type do not qualify."
        )

    @classmethod
    def extraction_prompt(cls) -> str:
        return (
            "Extract the following fields from this Saudi National ID (Iqama or national ID card). "
            "Return all dates in YYYY-MM-DD format. "
            "If the document uses Hijri dates, convert them to Gregorian. "
            "If a field is not visible or legible, omit it from the response."
        )
