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
    product_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="RECEIVED")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("event_id", name="uq_inbound_event_id"),)


class ApplicationCase(Base):
    __tablename__ = "application_case"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    application_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    # FK to the deduplication key on inbound_application_event (UNIQUE column).
    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inbound_application_event.event_id", name="fk_application_case_event_id"),
        nullable=False,
    )
    product_type: Mapped[str] = mapped_column(String(50), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(200), nullable=False)
    validator_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    supervisor_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    applicant_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="CREATED")
    recommendation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    recommendation_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Captures top-level pipeline failures that are not attributable to a
    # specific stage row (handler exceptions, missing required documents,
    # unsupported product type after the case row was created, etc.).
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    documents: Mapped[list["CaseDocument"]] = relationship(back_populates="case")
    report: Mapped["CaseReport | None"] = relationship(back_populates="case", uselist=False)
    edw_staging: Mapped["EDWStaging | None"] = relationship(back_populates="case", uselist=False)
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="case")
    case_result: Mapped["CaseResultRow | None"] = relationship(back_populates="case", uselist=False)
    dms_artifacts: Mapped[list["DMSArtifact"]] = relationship(back_populates="case")
    processing_jobs: Mapped[list["ProcessingJob"]] = relationship(back_populates="case")

    __table_args__ = (
        UniqueConstraint("application_id", name="uq_case_application_id"),
        Index("ix_application_case_status_created", "status", "created_at"),
    )


class CaseResultRow(Base):
    """Aggregated handler output — one row per completed case. completed_at is handler completion time."""

    __tablename__ = "case_result"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_case.id"), nullable=False, unique=True
    )
    recommendation: Mapped[str] = mapped_column(String(20), nullable=False)
    manual_review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    case: Mapped["ApplicationCase"] = relationship(back_populates="case_result")


class CaseDocument(Base):
    __tablename__ = "case_document"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_case.id"), nullable=False, index=True
    )
    dms_document_id: Mapped[str] = mapped_column(String(200), nullable=False)
    document_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")
    verification_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    case: Mapped["ApplicationCase"] = relationship(back_populates="documents")
    extraction_versions: Mapped[list["StageOutputVersion"]] = relationship(back_populates="document")


class StageOutputVersion(Base):
    """Append-only: one row per extraction attempt per document."""

    __tablename__ = "stage_output_version"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("case_document.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    raw_extraction: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_ai_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped["CaseDocument"] = relationship(back_populates="extraction_versions")

    __table_args__ = (
        Index("ix_stage_output_version_doc_version", "case_document_id", "version"),
    )


class ValidationResultRow(Base):
    """Append-only: one row per business rule executed per case."""

    __tablename__ = "validation_result"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_case.id"), nullable=False, index=True
    )
    rule_code: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extracted_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_validation_result_case_outcome", "case_id", "outcome"),
    )


class CaseReport(Base):
    __tablename__ = "case_report"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_case.id"), nullable=False, unique=True
    )
    html_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_dms_document_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pdf_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pdf_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    case: Mapped["ApplicationCase"] = relationship(back_populates="report")


class EDWStaging(Base):
    __tablename__ = "edw_staging"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_case.id"), nullable=False, unique=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="STAGED")
    edw_confirmation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    staged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    export_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    case: Mapped["ApplicationCase"] = relationship(back_populates="edw_staging")


class DMSArtifact(Base):
    """Tracks every DMS interaction — both fetched source documents and uploaded PDFs."""

    __tablename__ = "dms_artifact"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_case.id"), nullable=False, index=True
    )
    dms_document_id: Mapped[str] = mapped_column(String(200), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)   # SOURCE_DOCUMENT, PDF_REPORT
    direction: Mapped[str] = mapped_column(String(20), nullable=False)        # INBOUND, OUTBOUND
    status: Mapped[str] = mapped_column(String(20), nullable=False)           # SUCCESS, FAILED
    error_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    interacted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    case: Mapped["ApplicationCase"] = relationship(back_populates="dms_artifacts")


class ProcessingJob(Base):
    """Tracks retryable job execution — one row per stage, updated across retry attempts."""

    __tablename__ = "processing_job"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_case.id"), nullable=False, index=True
    )
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    case: Mapped["ApplicationCase"] = relationship(back_populates="processing_jobs")

    __table_args__ = (
        Index("ix_processing_job_case_type_status", "case_id", "job_type", "status"),
    )


class DeadLetterEvent(Base):
    __tablename__ = "dead_letter_event"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    # FK to inbound_application_event.event_id (UNIQUE column).
    # Nullable: events that fail schema validation never produce an
    # inbound_application_event row, so there is nothing to reference.
    event_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inbound_application_event.event_id", name="fk_dead_letter_event_id"),
        nullable=True,
        index=True,
    )
    case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("application_case.id"), nullable=True, index=True
    )
    application_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    case: Mapped["ApplicationCase | None"] = relationship()


class AuditEvent(Base):
    """Immutable audit log. Range-partitioned by `occurred_at` (monthly).

    The table is declared partitioned in the migration. The PK is a composite
    (id, occurred_at) so the partition key can participate.
    """

    __tablename__ = "audit_event"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        server_default=func.now(),
        index=True,
    )
    case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("application_case.id"), nullable=True, index=True
    )
    application_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    case: Mapped["ApplicationCase | None"] = relationship(back_populates="audit_events")

    __table_args__ = (
        Index("ix_audit_event_case_time", "case_id", "occurred_at"),
        # Partitioning is declared in the alembic migration.
        # SQLAlchemy is told here only about the composite PK.
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )
