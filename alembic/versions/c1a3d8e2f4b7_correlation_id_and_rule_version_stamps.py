"""correlation_id_and_rule_version_stamps

Revision ID: c1a3d8e2f4b7
Revises: 9fa2b5c9c2c8
Create Date: 2026-05-02 12:00:00.000000

Adds:
  - correlation_id on inbound_application_event (indexed, nullable for
    backfilled rows from before the contract was tightened)
  - correlation_id on application_case (indexed, denormalised)
  - rule_version, employer_rule_version, config_version on
    validation_result so every rule outcome is reproducible across
    config / employer-rules updates (BR-03 / BR-15 / BR-30)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c1a3d8e2f4b7"
down_revision: Union[str, None] = "9fa2b5c9c2c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- inbound_application_event ----------------------------------------
    op.add_column(
        "inbound_application_event",
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_inbound_application_event_correlation_id",
        "inbound_application_event",
        ["correlation_id"],
    )

    # --- application_case --------------------------------------------------
    op.add_column(
        "application_case",
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_application_case_correlation_id",
        "application_case",
        ["correlation_id"],
    )

    # --- validation_result version stamps ----------------------------------
    op.add_column(
        "validation_result",
        sa.Column("rule_version", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "validation_result",
        sa.Column("employer_rule_version", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "validation_result",
        sa.Column("config_version", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("validation_result", "config_version")
    op.drop_column("validation_result", "employer_rule_version")
    op.drop_column("validation_result", "rule_version")
    op.drop_index(
        "ix_application_case_correlation_id", table_name="application_case"
    )
    op.drop_column("application_case", "correlation_id")
    op.drop_index(
        "ix_inbound_application_event_correlation_id",
        table_name="inbound_application_event",
    )
    op.drop_column("inbound_application_event", "correlation_id")
