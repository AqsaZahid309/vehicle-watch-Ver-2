export type Role = "ADMIN" | "MANAGER" | "TECHNICIAN" | "OPERATOR" | "VIEWER";
export type Severity = "LOW" | "MEDIUM" | "CRITICAL";

export interface Organization {
  id: string;
  name: string;
  currency: string;
  fuel_price_per_liter: number;
  escalation_minutes: number;
  created_at: string;
}

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: Role;
  is_active: boolean;
  organization_id: string;
  created_at: string;
}

export interface Me extends User {
  organization: Organization;
}

export interface Device {
  id: string;
  name: string;
  device_type: string;
  owner_id: string | null;
  is_active: boolean;
  registered_at: string;
  vin: string | null;
  license_plate: string | null;
  make: string | null;
  model: string | null;
  year: number | null;
  fuel_tank_liters: number;
  odometer_km: number;
  assigned_driver_id: string | null;
  api_key_prefix: string | null;
  last_seen_at: string | null;
}

export interface Reading {
  id?: string;
  device_id?: string;
  recorded_at: string;
  gps_lat: number;
  gps_lon: number;
  engine_temp: number;
  rpm: number;
  fuel_level: number;
  battery_voltage: number;
  speed: number;
  vibration: number;
  oil_pressure?: number | null;
  coolant_level?: number | null;
  tire_pressure?: number | null;
  ambient_temp?: number | null;
  dtc_codes?: string[] | null;
}

export interface LiveDevice {
  id: string;
  name: string;
  device_type: string;
  license_plate: string | null;
  is_active: boolean;
  online: boolean;
  last_seen_at: string | null;
  latest: Reading | null;
  open_alerts: number;
  health_score: number;
  driver_name: string | null;
}

export interface Contributor {
  feature: string;
  z_score: number;
  value: number;
  train_mean: number;
  train_std: number;
  direction: "above" | "below";
}

export interface Alert {
  id: string;
  device_id: string;
  device_name: string | null;
  telemetry_id: string | null;
  severity: Severity;
  anomaly_score: number;
  affected_metrics: {
    top_contributors?: Contributor[];
    ensemble?: {
      isolation_forest_score: number;
      lof_confirmed: boolean;
      confidence: string;
      n_features: number;
      n_train_samples: number;
      model_version?: number | null;
      model_scope?: string | null;
    };
    dtc_codes?: string[];
  };
  fault_type: string | null;
  fault_confidence: "HIGH" | "MEDIUM" | null;
  llm_summary: string | null;
  acknowledged: boolean;
  acknowledged_at: string | null;
  acknowledged_by_id: string | null;
  feedback: "TRUE_POSITIVE" | "FALSE_POSITIVE" | null;
  feedback_notes: string | null;
  escalated_at: string | null;
  work_order_id: string | null;
  created_at: string;
  telemetry?: Reading | null;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export type WOStatus = "OPEN" | "IN_PROGRESS" | "ON_HOLD" | "RESOLVED" | "CANCELLED";
export type WOPriority = "LOW" | "MEDIUM" | "HIGH" | "URGENT";

export interface WorkOrder {
  id: string;
  number: number;
  device_id: string;
  device_name: string | null;
  alert_id: string | null;
  schedule_id: string | null;
  title: string;
  description: string | null;
  status: WOStatus;
  priority: WOPriority;
  assigned_to_id: string | null;
  assigned_to_email: string | null;
  created_by_id: string | null;
  root_cause: string | null;
  resolution_notes: string | null;
  parts_cost: number;
  labor_cost: number;
  downtime_hours: number;
  total_cost: number;
  due_date: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  resolved_at: string | null;
}

export interface Schedule {
  id: string;
  device_id: string;
  device_name: string | null;
  name: string;
  interval_km: number | null;
  interval_days: number | null;
  last_service_at: string;
  last_service_km: number;
  is_active: boolean;
  km_remaining: number | null;
  days_remaining: number | null;
  due: boolean;
}

export interface Driver {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  license_number: string | null;
  is_active: boolean;
  created_at: string;
}

export interface DriverScore {
  driver_id: string | null;
  driver_name: string;
  trips: number;
  distance_km: number;
  driving_hours: number;
  idle_pct: number;
  harsh_accel_per_100km: number;
  harsh_brake_per_100km: number;
  overspeed_pct: number;
  over_rev_events: number;
  fuel_l_per_100km: number | null;
  score: number;
}

export interface Trip {
  id: string;
  device_id: string;
  device_name: string | null;
  driver_id: string | null;
  driver_name: string | null;
  started_at: string;
  ended_at: string;
  is_open: boolean;
  start_lat: number;
  start_lon: number;
  end_lat: number;
  end_lon: number;
  distance_km: number;
  duration_seconds: number;
  moving_seconds: number;
  idle_seconds: number;
  max_speed: number;
  avg_speed: number;
  harsh_accel_count: number;
  harsh_brake_count: number;
  overspeed_seconds: number;
  over_rev_count: number;
  fuel_used_liters: number;
  score: number;
}

export interface TripPoint {
  t: string;
  lat: number;
  lon: number;
  speed: number;
}

export type GeofenceKind = "DEPOT" | "CUSTOMER" | "SERVICE" | "RESTRICTED";

export interface Geofence {
  id: string;
  name: string;
  kind: GeofenceKind;
  shape: "CIRCLE" | "POLYGON";
  center_lat: number | null;
  center_lon: number | null;
  radius_m: number | null;
  polygon: [number, number][] | null;
  alert_on_enter: boolean;
  alert_on_exit: boolean;
  speed_limit_kmh: number | null;
  created_at: string;
  vehicles_inside: number;
}

export interface GeofenceEvent {
  id: string;
  geofence_id: string;
  geofence_name: string;
  geofence_kind: string;
  device_id: string;
  device_name: string;
  event: "ENTER" | "EXIT" | "SPEEDING";
  occurred_at: string;
  lat: number;
  lon: number;
  speed: number;
}

export interface FuelStat {
  device_id: string;
  device_name: string;
  distance_km: number;
  fuel_used_liters: number;
  l_per_100km: number | null;
  fuel_cost: number;
  cost_per_km: number | null;
  refuels: number;
  theft_suspected: number;
  current_level: number | null;
}

export interface FuelEvent {
  id: string;
  device_id: string;
  device_name: string;
  event: "REFUEL" | "THEFT_SUSPECTED";
  occurred_at: string;
  level_before: number;
  level_after: number;
  liters: number;
  lat: number;
  lon: number;
  reviewed: boolean;
}

export interface ForecastSignal {
  signal: string;
  label: string;
  unit: string;
  current: number;
  threshold: number;
  direction: "rising" | "falling";
  slope_per_hour: number;
  r2: number;
  hours_to_threshold: number | null;
  risk: "NONE" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  predicted_fault: string;
  history: { h: number; v: number }[];
}

export interface DeviceForecast {
  device_id: string;
  device_name: string;
  sample_count: number;
  window_hours: number;
  overall_risk: ForecastSignal["risk"];
  min_hours_to_failure: number | null;
  signals: ForecastSignal[];
}

export interface ModelVersion {
  id: string;
  scope: "DEVICE" | "CLASS";
  device_id: string | null;
  device_type: string;
  version: number;
  reason: string;
  n_train: number;
  window_start: string | null;
  window_end: string | null;
  feature_stats: Record<string, { mean: number; std: number }>;
  drift_psi: number | null;
  is_active: boolean;
  pinned: boolean;
  trained_at: string;
  has_artifact: boolean;
}

export interface DetectorMetrics {
  total_alerts: number;
  labelled_alerts: number;
  precision: number | null;
  by_fault_type: {
    fault_type: string;
    alerts: number;
    labelled: number;
    true_positive: number;
    false_positive: number;
    precision: number | null;
  }[];
  by_device: {
    device_id: string;
    device_name: string;
    alerts: number;
    true_positive: number;
    false_positive: number;
    precision: number | null;
  }[];
}

export interface Notification {
  id: string;
  event_type: string;
  severity: string | null;
  title: string;
  body: string;
  link: string | null;
  device_id: string | null;
  alert_id: string | null;
  read: boolean;
  created_at: string;
}

export type ChannelType = "EMAIL" | "SLACK" | "TEAMS" | "WEBHOOK" | "SMS";

export interface Channel {
  id: string;
  name: string;
  channel_type: ChannelType;
  target: string;
  min_severity: Severity;
  event_types: string[];
  enabled: boolean;
  created_at: string;
}

export interface FleetSummary {
  total_devices: number;
  active_devices: number;
  online_devices: number;
  total_alerts: number;
  unacknowledged_alerts: number;
  alerts_by_severity: Partial<Record<Severity, number>>;
  alerts_by_fault_type: Record<string, number>;
  alerts_last_24h: number;
  avg_engine_temp: number | null;
  avg_fuel_level: number | null;
  open_work_orders: number;
  distance_km_24h: number;
  trips_24h: number;
}

export interface AuditEntry {
  id: string;
  created_at: string;
  user_email: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  details: Record<string, unknown>;
}
