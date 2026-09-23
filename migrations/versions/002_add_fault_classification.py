"""Add fault_type and fault_confidence to alerts

Revision ID: 002
Revises: 001
Create Date: 2026-04-22 00:00:00.000000

Adds two nullable columns to the `alerts` table to store the result of the
rule-based fault classifier introduced in AnomalyService.fault_classifier():

  fault_type        — named fault pattern (e.g. COOLANT_LEAK, BATTERY_FAILURE)
  fault_confidence  — confidence level from the rule match (HIGH or MEDIUM)

Both columns are nullable so that alerts created before this migration
(or created by code that pre-dates fault classification) remain valid.
New alerts will always have both fields populated by fault_classifier().
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the PostgreSQL enum types before adding columns that reference them.
    # Using op.execute() keeps the DDL explicit and avoids SQLAlchemy attempting
    # to auto-create the type during column addition (which would fail if the
    # type already exists from a partial migration run).
    op.execute(
        """
        CREATE TYPE faulttype AS ENUM (
            'COOLANT_LEAK',
            'BATTERY_FAILURE',
            'TRANSMISSION_STRESS',
            'BRAKE_WEAR',
            'ENGINE_STRESS',
            'UNKNOWN_ANOMALY'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE faultconfidence AS ENUM ('HIGH', 'MEDIUM')
        """
    )

    op.add_column(
        "alerts",
        sa.Column(
            "fault_type",
            sa.Enum(
                "COOLANT_LEAK",
                "BATTERY_FAILURE",
                "TRANSMISSION_STRESS",
                "BRAKE_WEAR",
                "ENGINE_STRESS",
                "UNKNOWN_ANOMALY",
                name="faulttype",
                create_type=False,   # type already created above
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "alerts",
        sa.Column(
            "fault_confidence",
            sa.Enum(
                "HIGH",
                "MEDIUM",
                name="faultconfidence",
                create_type=False,   # type already created above
            ),
            nullable=True,
        ),
    )

    # Index fault_type to support efficient dashboard queries like
    # "show all COOLANT_LEAK alerts" or "count by fault type".
    op.create_index("ix_alerts_fault_type", "alerts", ["fault_type"])


def downgrade() -> None:
    op.drop_index("ix_alerts_fault_type", "alerts")
    op.drop_column("alerts", "fault_confidence")
    op.drop_column("alerts", "fault_type")
    op.execute("DROP TYPE IF EXISTS faulttype")
    op.execute("DROP TYPE IF EXISTS faultconfidence")
