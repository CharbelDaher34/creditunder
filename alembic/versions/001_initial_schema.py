"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-30

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbound_application_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.String(100), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_inbound_event_id"),
    )
    op.create_index("ix_iae_application_id", "inbound_application_event", ["application_id"])

    op.create_table(
        "application_case",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.String(100), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_type", sa.String(50), nullable=False),
        sa.Column("branch_name", sa.String(200), nullable=False),
        sa.Column("validator_id", sa.String(100), nullable=False),
        sa.Column("supervisor_id", sa.String(100), nullable=False),
        sa.Column("applicant_data", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="RECEIVED"),
        sa.Column("recommendation", sa.String(20), nullable=True),
        sa.Column("recommendation_rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", name="uq_case_application_id"),
    )
    op.create_index("ix_ac_validator_id", "application_case", ["validator_id"])
    op.create_index("ix_ac_supervisor_id", "application_case", ["supervisor_id"])

    op.create_table(
        "case_document",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dms_document_id", sa.String(200), nullable=False),
        sa.Column("document_name", sa.String(500), nullable=True),
        sa.Column("document_type", sa.String(50), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("verification_passed", sa.Boolean(), nullable=True),
        sa.Column("verification_confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id"], ["application_case.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cd_case_id", "case_document", ["case_id"])

    op.create_table(
        "stage_output_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("raw_extraction", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_document_id"], ["case_document.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stage_output_version_doc_version",
        "stage_output_version",
        ["case_document_id", "version"],
    )

    op.create_table(
        "validation_result",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_code", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=True),
        sa.Column("extracted_value", sa.Text(), nullable=True),
        sa.Column("expected_value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id"], ["application_case.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vr_case_id", "validation_result", ["case_id"])
    op.create_index("ix_vr_outcome", "validation_result", ["outcome"])

    op.create_table(
        "case_report",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("html_content", sa.Text(), nullable=True),
        sa.Column("pdf_dms_document_id", sa.String(200), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id"], ["application_case.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
    )

    op.create_table(
        "edw_staging",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="STAGED"),
        sa.Column("edw_confirmation_id", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id"], ["application_case.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
    )

    op.create_table(
        "dead_letter_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("application_id", sa.String(100), nullable=True),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dle_application_id", "dead_letter_event", ["application_id"])

    op.create_table(
        "audit_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("application_id", sa.String(100), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id"], ["application_case.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_event_case_time", "audit_event", ["case_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("audit_event")
    op.drop_table("dead_letter_event")
    op.drop_table("edw_staging")
    op.drop_table("case_report")
    op.drop_table("validation_result")
    op.drop_table("stage_output_version")
    op.drop_table("case_document")
    op.drop_table("application_case")
    op.drop_table("inbound_application_event")
