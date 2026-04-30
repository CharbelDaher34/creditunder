from creditunder.domain.enums import DocumentType
from creditunder.documents.base import BaseExtractionSchema
from creditunder.documents.id_document import IDDocumentExtraction
from creditunder.documents.salary_certificate import SalaryCertificateExtraction

EXTRACTION_SCHEMA_REGISTRY: dict[DocumentType, type[BaseExtractionSchema]] = {
    DocumentType.ID_DOCUMENT: IDDocumentExtraction,
    DocumentType.SALARY_CERTIFICATE: SalaryCertificateExtraction,
}
