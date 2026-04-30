from pydantic import BaseModel

from creditunder.domain.enums import DocumentType


class BaseExtractionSchema(BaseModel):
    """Base class for all document extraction schemas."""

    @classmethod
    def document_type(cls) -> DocumentType:
        raise NotImplementedError

    @classmethod
    def extraction_prompt(cls) -> str:
        raise NotImplementedError
