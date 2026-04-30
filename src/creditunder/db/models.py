from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class InboundApplicationEvent(Base):
    __tablename__ = "inbound_application_event"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, unique=True)
    application_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("event_id", name="uq_inbound_event_id"),)


class ApplicationCase(Base):
    __tablename__ = "application_case"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    application_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    product_type: Mapped[str] = mapped_column(String(50), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(200), nullable=False)
    validator_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    supervisor_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    applicant_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="RECEIVED")
    recommendation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    recommendation_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    documents: Mapped[list["CaseDocument"]] = relationship(back_populates="case")
    report: Mapped["CaseReport | None"] = relationship(back_populates="case", uselist=False)
    edw_staging: Mapped["EDWStaging | None"] = relationship(back_populates="case", uselist=False)
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="case")

    __table_args__ = (UniqueConstraint("application_id", name="uq_case_application_id"),)


class CaseDocument(Base):
    __tablename__ = "case_document"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("application_case.id"), nullable=False, index=True)
    dms_document_id: Mapped[str] = mapped_column(String(200), nullable=False)
    document_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")
    verification_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    case: Mapped["ApplicationCase"] = relationship(back_populates="documents")
    extraction_versions: Mapped[list["StageOutputVersion"]] = relationship(back_populates="document")


class StageOutputVersion(Base):
    """Append-only: extraction attempts per document."""

    __tablename__ = "stage_output_version"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("case_document.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    raw_extraction: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped["CaseDocument"] = relationship(back_populates="extraction_versions")

    __table_args__ = (
        Index("ix_stage_output_version_doc_version", "case_document_id", "version"),
    )


class ValidationResultRow(Base):
    """Append-only: one row per rule execution."""

    __tablename__ = "validation_result"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("application_case.id"), nullable=False, index=True)
    rule_code: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extracted_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CaseReport(Base):
    __tablename__ = "case_report"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("application_case.id"), nullable=False, unique=True)
    html_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_dms_document_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    case: Mapped["ApplicationCase"] = relationship(back_populates="report")


class EDWStaging(Base):
    __tablename__ = "edw_staging"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("application_case.id"), nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="STAGED")
    edw_confirmation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    case: Mapped["ApplicationCase"] = relationship(back_populates="edw_staging")


class DeadLetterEvent(Base):
    __tablename__ = "dead_letter_event"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    application_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID | None] = mapped_column(ForeignKey("application_case.id"), nullable=True, index=True)
    application_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    case: Mapped["ApplicationCase | None"] = relationship(back_populates="audit_events")

    __table_args__ = (Index("ix_audit_event_case_time", "case_id", "occurred_at"),)
