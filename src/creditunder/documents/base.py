from typing import Optional

from pydantic import BaseModel

from creditunder.domain.enums import DocumentType
from creditunder.domain.models import ExtractedField


class BaseExtractionSchema(BaseModel):
    """Base class for all document extraction schemas.

    Fields are typed ExtractedField[T] directly — the schema is the AI output model.
    Verification fields are declared here so every document carries them.
    """

    is_correct_type: ExtractedField[bool]
    verification_confidence: ExtractedField[float]
    detected_type: ExtractedField[str]
    verification_notes: Optional[ExtractedField[str]] = None

    @classmethod
    def document_type(cls) -> DocumentType:
        raise NotImplementedError

    @classmethod
    def extraction_prompt(cls) -> str:
        raise NotImplementedError

    @classmethod
    def verification_prompt(cls) -> str:
        return (
            f"Confirm this document is a {cls.document_type().value}. "
            "It should contain all expected fields for that document type."
        )

    @classmethod
    def verification_field_names(cls) -> frozenset[str]:
        return frozenset({"is_correct_type", "verification_confidence", "detected_type", "verification_notes"})
