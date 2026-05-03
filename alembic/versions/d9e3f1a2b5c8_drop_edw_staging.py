"""drop_edw_staging

Revision ID: d9e3f1a2b5c8
Revises: c1a3d8e2f4b7
Create Date: 2026-05-02 14:00:00.000000

EDW has been removed from the platform. Case results are consumed directly
from Postgres by the bank's reporting layer. This drops the edw_staging table
that tracked HTTP export calls to the former EDW mockup.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

revision: str = "d9e3f1a2b5c8"
down_revision: Union[str, None] = "c1a3d8e2f4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("edw_staging")


def downgrade() -> None:
    op.create_table(
        "edw_staging",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="STAGED"),
        sa.Column("edw_confirmation_id", sa.String(200), nullable=True),
        sa.Column("staged_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("export_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["case_id"], ["application_case.id"]),
        sa.UniqueConstraint("case_id"),
    )
