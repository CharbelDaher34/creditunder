"""readd_edw_staging

Revision ID: f2a1e3d4b5c6
Revises: d9e3f1a2b5c8
Create Date: 2026-05-02 16:00:00.000000

Re-adds edw_staging as the Reviewer Workbench's single source of truth.
The table stores a denormalised snapshot of every completed case so the
workbench never needs to query the pipeline tables directly.

Promoted columns (application_id, validator_id, supervisor_id, status,
recommendation, product_type, manual_review_required, pdf_dms_document_id,
branch_name, applicant_name) support efficient list-view filtering and
role-based row scoping. The payload JSONB carries the full CaseDetail
shape for the detail view.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

revision: str = "f2a1e3d4b5c6"
down_revision: Union[str, None] = "d9e3f1a2b5c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "edw_staging",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("application_case.id"),
            nullable=False,
            unique=True,
        ),
        # Promoted queryable columns
        sa.Column("application_id", sa.String(100), nullable=False),
        sa.Column("validator_id", sa.String(100), nullable=False),
        sa.Column("supervisor_id", sa.String(100), nullable=False),
        sa.Column("product_type", sa.String(50), nullable=False),
        sa.Column("branch_name", sa.String(200), nullable=False),
        sa.Column("applicant_name", sa.String(500), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("recommendation", sa.String(20), nullable=True),
        sa.Column("manual_review_required", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("pdf_dms_document_id", sa.String(200), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # Full denormalised payload
        sa.Column("payload", JSONB, nullable=False),
        # EDW export tracking
        sa.Column("export_status", sa.String(50), nullable=False, server_default="STAGED"),
        sa.Column("edw_confirmation_id", sa.String(200), nullable=True),
        sa.Column("export_error", sa.Text, nullable=True),
        sa.Column(
            "staged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_unique_constraint("uq_edw_staging_case_id", "edw_staging", ["case_id"])
    op.create_index("ix_edw_staging_application_id", "edw_staging", ["application_id"])
    op.create_index("ix_edw_staging_validator_id", "edw_staging", ["validator_id"])
    op.create_index("ix_edw_staging_supervisor_id", "edw_staging", ["supervisor_id"])
    op.create_index("ix_edw_staging_status", "edw_staging", ["status"])


def downgrade() -> None:
    op.drop_index("ix_edw_staging_status", table_name="edw_staging")
    op.drop_index("ix_edw_staging_supervisor_id", table_name="edw_staging")
    op.drop_index("ix_edw_staging_validator_id", table_name="edw_staging")
    op.drop_index("ix_edw_staging_application_id", table_name="edw_staging")
    op.drop_table("edw_staging")
