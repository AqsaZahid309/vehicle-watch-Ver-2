"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum("ADMIN", "OPERATOR", name="userrole"),
            nullable=False,
            server_default="OPERATOR",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # devices
    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("device_type", sa.String(100), nullable=False),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_devices_owner_id", "devices", ["owner_id"])

    # telemetry
    op.create_table(
        "telemetry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gps_lat", sa.Float, nullable=False),
        sa.Column("gps_lon", sa.Float, nullable=False),
        sa.Column("engine_temp", sa.Float, nullable=False),
        sa.Column("rpm", sa.Float, nullable=False),
        sa.Column("fuel_level", sa.Float, nullable=False),
        sa.Column("battery_voltage", sa.Float, nullable=False),
        sa.Column("speed", sa.Float, nullable=False),
        sa.Column("vibration", sa.Float, nullable=False),
    )
    op.create_index("ix_telemetry_device_id", "telemetry", ["device_id"])
    op.create_index("ix_telemetry_recorded_at", "telemetry", ["recorded_at"])
    op.create_index(
        "ix_telemetry_device_recorded", "telemetry", ["device_id", "recorded_at"]
    )

    # alerts
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "telemetry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("telemetry.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "severity",
            sa.Enum("LOW", "MEDIUM", "CRITICAL", name="alertseverity"),
            nullable=False,
        ),
        sa.Column("anomaly_score", sa.Float, nullable=False),
        sa.Column("affected_metrics", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("llm_summary", sa.Text, nullable=True),
        sa.Column("acknowledged", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_alerts_device_id", "alerts", ["device_id"])


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_index("ix_telemetry_device_recorded", "telemetry")
    op.drop_index("ix_telemetry_recorded_at", "telemetry")
    op.drop_index("ix_telemetry_device_id", "telemetry")
    op.drop_table("telemetry")
    op.drop_index("ix_devices_owner_id", "devices")
    op.drop_table("devices")
    op.drop_index("ix_users_email", "users")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS alertseverity")
    op.execute("DROP TYPE IF EXISTS userrole")
