"""drop_correlation_id

Revision ID: e5b2f9a1c3d7
Revises: c1a3d8e2f4b7
Create Date: 2026-05-03 00:00:00.000000

Removes correlation_id from inbound_application_event and application_case.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e5b2f9a1c3d7"
down_revision: Union[str, None] = "f2a1e3d4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_inbound_application_event_correlation_id",
        table_name="inbound_application_event",
    )
    op.drop_column("inbound_application_event", "correlation_id")

    op.drop_index(
        "ix_application_case_correlation_id",
        table_name="application_case",
    )
    op.drop_column("application_case", "correlation_id")


def downgrade() -> None:
    op.add_column(
        "application_case",
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_application_case_correlation_id",
        "application_case",
        ["correlation_id"],
    )

    op.add_column(
        "inbound_application_event",
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_inbound_application_event_correlation_id",
        "inbound_application_event",
        ["correlation_id"],
    )
