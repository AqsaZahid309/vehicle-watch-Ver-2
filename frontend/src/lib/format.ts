import type { Role } from "../api/types";

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function parseTs(iso: string): number {
  return new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z").getTime();
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = Math.round((Date.now() - parseTs(iso)) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function duration(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
}

export function hoursLabel(h: number | null | undefined): string {
  if (h === null || h === undefined) return "—";
  if (h === 0) return "now";
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} days`;
}

export function num(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function money(v: number | null | undefined, currency = "USD"): string {
  if (v === null || v === undefined) return "—";
  try {
    return v.toLocaleString(undefined, { style: "currency", currency, maximumFractionDigits: 0 });
  } catch {
    return `${currency} ${num(v, 0)}`;
  }
}

export function humanize(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

const ROLE_RANK: Record<Role, number> = { VIEWER: 0, OPERATOR: 1, TECHNICIAN: 2, MANAGER: 3, ADMIN: 4 };
export function atLeast(role: Role | undefined, min: Role): boolean {
  return !!role && ROLE_RANK[role] >= ROLE_RANK[min];
}
export function canOperate(role: Role | undefined) {
  return atLeast(role, "OPERATOR");
}

export function healthColor(score: number): string {
  if (score >= 85) return "var(--green)";
  if (score >= 60) return "var(--yellow)";
  if (score >= 35) return "var(--orange)";
  return "var(--red)";
}

export const FEATURE_LABELS: Record<string, string> = {
  engine_temp: "Engine temp",
  rpm: "RPM",
  fuel_level: "Fuel level",
  battery_voltage: "Battery",
  speed: "Speed",
  vibration: "Vibration",
  temp_per_rpm: "Temp per RPM",
  vib_per_speed: "Vibration per speed",
  engine_stress: "Engine stress",
  electrical_load: "Electrical load",
};
