"""remove employer_rule_version from validation_result

Revision ID: a3c7e1d2f0b9
Revises: f2a1e3d4b5c6
Create Date: 2026-05-03 00:00:00.000000

"""
from alembic import op

revision = "a3c7e1d2f0b9"
down_revision = "e5b2f9a1c3d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("validation_result", "employer_rule_version")


def downgrade() -> None:
    import sqlalchemy as sa
    op.add_column(
        "validation_result",
        sa.Column("employer_rule_version", sa.String(50), nullable=True),
    )
