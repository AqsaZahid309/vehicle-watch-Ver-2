"""Platform expansion: multi-tenancy, maintenance, notifications, fleet ops, model registry

Revision ID: 003
Revises: 002
Create Date: 2026-09-23 00:00:00.000000

Existing data is preserved: if users already exist, a "Default Organization" is
created and every existing user and device is moved into it. Duplicate
(device_id, recorded_at) telemetry rows — which the new idempotency constraint
forbids — are removed first (keeping one of each).

Requires PostgreSQL 12+ (ALTER TYPE ... ADD VALUE inside a transaction).
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def _ts(name: str, nullable: bool = True, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=sa.text("now()") if default_now else None,
    )


def _id() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True)


def _fk(name: str, target: str, ondelete: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, UUID, sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    # ── Enum values ──────────────────────────────────────────────────────────
    for value in ("MANAGER", "TECHNICIAN", "VIEWER"):
        op.execute(f"ALTER TYPE userrole ADD VALUE IF NOT EXISTS '{value}'")
    for value in ("WHEEL_BEARING", "LOW_OIL_PRESSURE", "TIRE_PRESSURE"):
        op.execute(f"ALTER TYPE faulttype ADD VALUE IF NOT EXISTS '{value}'")

    # ── Organizations + backfill ─────────────────────────────────────────────
    op.create_table(
        "organizations",
        _id(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("fuel_price_per_liter", sa.Float, nullable=False, server_default="1.5"),
        sa.Column("escalation_minutes", sa.Integer, nullable=False, server_default="15"),
        _ts("created_at", nullable=False, default_now=True),
    )

    conn = op.get_bind()
    has_users = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM users)")).scalar()
    default_org = str(uuid.uuid4())
    if has_users:
        conn.execute(
            sa.text("INSERT INTO organizations (id, name) VALUES (CAST(:id AS uuid), 'Default Organization')"),
            {"id": default_org},
        )

    # ── Users ────────────────────────────────────────────────────────────────
    op.add_column("users", _fk("organization_id", "organizations.id", "CASCADE"))
    op.add_column("users", sa.Column("full_name", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"))
    if has_users:
        conn.execute(sa.text("UPDATE users SET organization_id = CAST(:id AS uuid)"), {"id": default_org})
    op.alter_column("users", "organization_id", nullable=False)
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    # ── Drivers (referenced by devices) ──────────────────────────────────────
    op.create_table(
        "drivers",
        _id(),
        _fk("organization_id", "organizations.id", "CASCADE", nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(64)),
        sa.Column("license_number", sa.String(64)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_drivers_organization_id", "drivers", ["organization_id"])

    # ── Devices ──────────────────────────────────────────────────────────────
    op.add_column("devices", _fk("organization_id", "organizations.id", "CASCADE"))
    if has_users:
        conn.execute(sa.text("UPDATE devices SET organization_id = CAST(:id AS uuid)"), {"id": default_org})
    op.alter_column("devices", "organization_id", nullable=False)
    op.create_index("ix_devices_organization_id", "devices", ["organization_id"])

    # owner is now optional and survives user deletion
    op.drop_constraint("devices_owner_id_fkey", "devices", type_="foreignkey")
    op.alter_column("devices", "owner_id", nullable=True)
    op.create_foreign_key("devices_owner_id_fkey", "devices", "users", ["owner_id"], ["id"], ondelete="SET NULL")

    for col in (
        sa.Column("vin", sa.String(32)),
        sa.Column("license_plate", sa.String(32)),
        sa.Column("make", sa.String(64)),
        sa.Column("model", sa.String(64)),
        sa.Column("year", sa.Integer),
        sa.Column("fuel_tank_liters", sa.Float, nullable=False, server_default="300"),
        sa.Column("odometer_km", sa.Float, nullable=False, server_default="0"),
        _fk("assigned_driver_id", "drivers.id", "SET NULL"),
        sa.Column("api_key_prefix", sa.String(16)),
        sa.Column("api_key_hash", sa.String(64)),
        _ts("last_seen_at"),
        _ts("anomaly_watermark"),
        _ts("trip_watermark"),
    ):
        op.add_column("devices", col)
    op.create_unique_constraint("uq_devices_api_key_prefix", "devices", ["api_key_prefix"])

    # ── Telemetry ────────────────────────────────────────────────────────────
    for col in (
        sa.Column("oil_pressure", sa.Float),
        sa.Column("coolant_level", sa.Float),
        sa.Column("tire_pressure", sa.Float),
        sa.Column("ambient_temp", sa.Float),
        sa.Column("dtc_codes", JSONB),
    ):
        op.add_column("telemetry", col)
    op.execute(
        """
        DELETE FROM telemetry a
        USING telemetry b
        WHERE a.device_id = b.device_id
          AND a.recorded_at = b.recorded_at
          AND a.id > b.id
        """
    )
    op.create_unique_constraint("uq_telemetry_device_recorded", "telemetry", ["device_id", "recorded_at"])

    op.create_table(
        "telemetry_hourly",
        sa.Column("device_id", UUID, sa.ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("bucket", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("sample_count", sa.Integer, nullable=False),
        *[sa.Column(c, sa.Float, nullable=False) for c in (
            "avg_engine_temp", "max_engine_temp", "avg_rpm", "max_rpm", "avg_speed", "max_speed",
            "avg_fuel_level", "avg_battery_voltage", "min_battery_voltage", "avg_vibration", "max_vibration",
        )],
    )

    # ── Alerts ───────────────────────────────────────────────────────────────
    op.add_column("alerts", _ts("acknowledged_at"))
    op.add_column("alerts", _fk("acknowledged_by_id", "users.id", "SET NULL"))
    op.add_column("alerts", sa.Column("feedback", sa.String(20)))
    op.add_column("alerts", sa.Column("feedback_notes", sa.Text))
    op.add_column("alerts", _fk("feedback_by_id", "users.id", "SET NULL"))
    op.add_column("alerts", _ts("escalated_at"))
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])
    op.create_index("ix_alerts_feedback", "alerts", ["feedback"])

    # ── Trips, geofences, fuel ───────────────────────────────────────────────
    op.create_table(
        "trips",
        _id(),
        _fk("device_id", "devices.id", "CASCADE", nullable=False),
        _fk("driver_id", "drivers.id", "SET NULL"),
        _ts("started_at", nullable=False),
        _ts("ended_at", nullable=False),
        sa.Column("is_open", sa.Boolean, nullable=False, server_default="true"),
        *[sa.Column(c, sa.Float, nullable=False) for c in ("start_lat", "start_lon", "end_lat", "end_lon")],
        *[sa.Column(c, sa.Float, nullable=False, server_default="0") for c in (
            "distance_km", "moving_seconds", "idle_seconds", "max_speed", "speed_sum",
            "overspeed_seconds", "fuel_used_liters", "last_speed", "last_fuel_level",
        )],
        *[sa.Column(c, sa.Integer, nullable=False, server_default="0") for c in (
            "point_count", "harsh_accel_count", "harsh_brake_count", "over_rev_count",
        )],
        sa.Column("score", sa.Float, nullable=False, server_default="100"),
    )
    op.create_index("ix_trips_device_id", "trips", ["device_id"])
    op.create_index("ix_trips_driver_id", "trips", ["driver_id"])
    op.create_index("ix_trips_started_at", "trips", ["started_at"])

    op.create_table(
        "geofences",
        _id(),
        _fk("organization_id", "organizations.id", "CASCADE", nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="CUSTOMER"),
        sa.Column("shape", sa.String(10), nullable=False, server_default="CIRCLE"),
        sa.Column("center_lat", sa.Float),
        sa.Column("center_lon", sa.Float),
        sa.Column("radius_m", sa.Float),
        sa.Column("polygon", JSONB),
        sa.Column("alert_on_enter", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("alert_on_exit", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("speed_limit_kmh", sa.Float),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_geofences_organization_id", "geofences", ["organization_id"])

    op.create_table(
        "geofence_events",
        _id(),
        _fk("geofence_id", "geofences.id", "CASCADE", nullable=False),
        _fk("device_id", "devices.id", "CASCADE", nullable=False),
        sa.Column("event", sa.String(20), nullable=False),
        _ts("occurred_at", nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("speed", sa.Float, nullable=False, server_default="0"),
    )
    op.create_index("ix_geofence_events_geofence_id", "geofence_events", ["geofence_id"])
    op.create_index("ix_geofence_events_device_id", "geofence_events", ["device_id"])
    op.create_index("ix_geofence_events_occurred_at", "geofence_events", ["occurred_at"])

    op.create_table(
        "device_geofence_state",
        sa.Column("device_id", UUID, sa.ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("geofence_id", UUID, sa.ForeignKey("geofences.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("inside", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("speeding", sa.Boolean, nullable=False, server_default="false"),
        _ts("since", nullable=False, default_now=True),
    )

    op.create_table(
        "fuel_events",
        _id(),
        _fk("device_id", "devices.id", "CASCADE", nullable=False),
        sa.Column("event", sa.String(30), nullable=False),
        _ts("occurred_at", nullable=False),
        *[sa.Column(c, sa.Float, nullable=False) for c in ("level_before", "level_after", "liters", "lat", "lon")],
        sa.Column("reviewed", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_fuel_events_device_id", "fuel_events", ["device_id"])
    op.create_index("ix_fuel_events_occurred_at", "fuel_events", ["occurred_at"])

    # ── Maintenance ──────────────────────────────────────────────────────────
    op.create_table(
        "service_schedules",
        _id(),
        _fk("device_id", "devices.id", "CASCADE", nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("interval_km", sa.Float),
        sa.Column("interval_days", sa.Integer),
        _ts("last_service_at", nullable=False, default_now=True),
        sa.Column("last_service_km", sa.Float, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_service_schedules_device_id", "service_schedules", ["device_id"])

    op.create_table(
        "work_orders",
        _id(),
        _fk("organization_id", "organizations.id", "CASCADE", nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        _fk("device_id", "devices.id", "CASCADE", nullable=False),
        _fk("alert_id", "alerts.id", "SET NULL"),
        _fk("schedule_id", "service_schedules.id", "SET NULL"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="MEDIUM"),
        _fk("assigned_to_id", "users.id", "SET NULL"),
        _fk("created_by_id", "users.id", "SET NULL"),
        sa.Column("root_cause", sa.String(40)),
        sa.Column("resolution_notes", sa.Text),
        sa.Column("parts_cost", sa.Float, nullable=False, server_default="0"),
        sa.Column("labor_cost", sa.Float, nullable=False, server_default="0"),
        sa.Column("downtime_hours", sa.Float, nullable=False, server_default="0"),
        _ts("due_date"),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        _ts("started_at"),
        _ts("resolved_at"),
    )
    for col in ("organization_id", "device_id", "alert_id", "status", "created_at"):
        op.create_index(f"ix_work_orders_{col}", "work_orders", [col])

    # ── Notifications ────────────────────────────────────────────────────────
    op.create_table(
        "notification_channels",
        _id(),
        _fk("organization_id", "organizations.id", "CASCADE", nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.String(20), nullable=False),
        sa.Column("target", sa.String(1024), nullable=False),
        sa.Column("min_severity", sa.String(20), nullable=False, server_default="MEDIUM"),
        sa.Column("event_types", JSONB, nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_notification_channels_organization_id", "notification_channels", ["organization_id"])

    op.create_table(
        "notifications",
        _id(),
        _fk("organization_id", "organizations.id", "CASCADE", nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(20)),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("link", sa.String(512)),
        _fk("device_id", "devices.id", "CASCADE"),
        _fk("alert_id", "alerts.id", "SET NULL"),
        sa.Column("read", sa.Boolean, nullable=False, server_default="false"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_notifications_organization_id", "notifications", ["organization_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    op.create_table(
        "notification_deliveries",
        _id(),
        _fk("notification_id", "notifications.id", "CASCADE", nullable=False),
        _fk("channel_id", "notification_channels.id", "SET NULL"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_notification_deliveries_notification_id", "notification_deliveries", ["notification_id"])

    # ── Model registry, audit ────────────────────────────────────────────────
    op.create_table(
        "model_versions",
        _id(),
        _fk("organization_id", "organizations.id", "CASCADE", nullable=False),
        sa.Column("scope", sa.String(10), nullable=False, server_default="DEVICE"),
        _fk("device_id", "devices.id", "CASCADE"),
        sa.Column("device_type", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("n_train", sa.Integer, nullable=False),
        _ts("window_start"),
        _ts("window_end"),
        sa.Column("feature_stats", JSONB, nullable=False, server_default="{}"),
        sa.Column("drift_psi", sa.Float),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("pinned", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("artifact", sa.LargeBinary),
        _ts("trained_at", nullable=False, default_now=True),
    )
    op.create_index("ix_model_versions_organization_id", "model_versions", ["organization_id"])
    op.create_index("ix_model_versions_device_id", "model_versions", ["device_id"])

    op.create_table(
        "audit_logs",
        _id(),
        _fk("organization_id", "organizations.id", "CASCADE", nullable=False),
        _fk("user_id", "users.id", "SET NULL"),
        sa.Column("user_email", sa.String(255)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(64)),
        sa.Column("details", JSONB, nullable=False, server_default="{}"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_audit_logs_organization_id", "audit_logs", ["organization_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    for table in (
        "audit_logs", "model_versions", "notification_deliveries", "notifications",
        "notification_channels", "work_orders", "service_schedules", "fuel_events",
        "device_geofence_state", "geofence_events", "geofences", "trips", "telemetry_hourly",
    ):
        op.drop_table(table)

    op.drop_index("ix_alerts_feedback", "alerts")
    op.drop_index("ix_alerts_created_at", "alerts")
    for col in ("escalated_at", "feedback_by_id", "feedback_notes", "feedback", "acknowledged_by_id", "acknowledged_at"):
        op.drop_column("alerts", col)

    op.drop_constraint("uq_telemetry_device_recorded", "telemetry", type_="unique")
    for col in ("dtc_codes", "ambient_temp", "tire_pressure", "coolant_level", "oil_pressure"):
        op.drop_column("telemetry", col)

    op.drop_constraint("uq_devices_api_key_prefix", "devices", type_="unique")
    for col in ("trip_watermark", "anomaly_watermark", "last_seen_at", "api_key_hash", "api_key_prefix",
                "assigned_driver_id", "odometer_km", "fuel_tank_liters", "year", "model", "make",
                "license_plate", "vin"):
        op.drop_column("devices", col)
    op.drop_index("ix_devices_organization_id", "devices")
    op.drop_column("devices", "organization_id")
    # Devices without an owner cannot be restored to NOT NULL; remove them.
    op.execute("DELETE FROM devices WHERE owner_id IS NULL")
    op.drop_constraint("devices_owner_id_fkey", "devices", type_="foreignkey")
    op.alter_column("devices", "owner_id", nullable=False)
    op.create_foreign_key("devices_owner_id_fkey", "devices", "users", ["owner_id"], ["id"], ondelete="CASCADE")

    op.drop_table("drivers")
    op.drop_index("ix_users_organization_id", "users")
    for col in ("is_active", "full_name", "organization_id"):
        op.drop_column("users", col)
    op.drop_table("organizations")
    # Enum values added to userrole/faulttype cannot be removed in PostgreSQL; they are left in place.
