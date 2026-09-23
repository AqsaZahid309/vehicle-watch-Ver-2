import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Copy, KeyRound, Pin, PinOff, RefreshCw, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { Device, DeviceForecast, Driver, LiveDevice, ModelVersion, Reading } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { C, ForecastChart, MetricLine } from "../components/charts";
import FleetMap, { VehicleLayer } from "../components/FleetMap";
import { Card, Empty, ErrorBox, Field, Health, Loading, PageHead, SeverityBadge, Tabs } from "../components/ui";
import { useToast } from "../components/Toasts";
import { atLeast, fmtDate, hoursLabel, humanize, num, timeAgo } from "../lib/format";
import Alerts from "./Alerts";
import { Schedules, WorkOrderBoard } from "./Maintenance";
import { TripsTable } from "./Trips";
import { VehicleForm } from "./Vehicles";

type Tab = "overview" | "forecast" | "alerts" | "trips" | "maintenance" | "model" | "settings";

const SENSORS: { key: keyof Reading; label: string; unit: string; warn?: (v: number) => boolean; bad?: (v: number) => boolean; d?: number }[] = [
  { key: "speed", label: "Speed", unit: "km/h", d: 0 },
  { key: "engine_temp", label: "Engine temp", unit: "°C", warn: (v) => v > 105, bad: (v) => v > 115 },
  { key: "rpm", label: "RPM", unit: "", d: 0, warn: (v) => v > 3200, bad: (v) => v > 4000 },
  { key: "battery_voltage", label: "Battery", unit: "V", d: 2, warn: (v) => v < 12.2, bad: (v) => v < 11.8 },
  { key: "vibration", label: "Vibration", unit: "g", d: 2, warn: (v) => v > 5, bad: (v) => v > 7 },
  { key: "fuel_level", label: "Fuel", unit: "%", d: 0, warn: (v) => v < 20, bad: (v) => v < 10 },
  { key: "oil_pressure", label: "Oil pressure", unit: "psi", warn: (v) => v < 25, bad: (v) => v < 15 },
  { key: "coolant_level", label: "Coolant", unit: "%", d: 0, warn: (v) => v < 60, bad: (v) => v < 40 },
  { key: "tire_pressure", label: "Lowest tyre", unit: "psi", d: 0, warn: (v) => v < 90, bad: (v) => v < 80 },
  { key: "ambient_temp", label: "Ambient", unit: "°C" },
];

function LiveSensors({ latest }: { latest: Reading | null | undefined }) {
  if (!latest) return <Empty>No telemetry received yet.</Empty>;
  return (
    <>
      <div className="sensor-grid">
        {SENSORS.filter((s) => latest[s.key] != null).map((s) => {
          const v = latest[s.key] as number;
          const cls = s.bad?.(v) ? "bad" : s.warn?.(v) ? "warn" : "";
          return (
            <div key={s.key} className={`sensor ${cls}`}>
              <div className="s-label">{s.label}</div>
              <div className="s-value">{num(v, s.d ?? 1)} <span className="small muted">{s.unit}</span></div>
            </div>
          );
        })}
      </div>
      {!!latest.dtc_codes?.length && (
        <div className="row wrap" style={{ marginTop: 10 }}>
          <span className="small muted">Active OBD-II codes:</span>
          {latest.dtc_codes.map((c) => <code key={c} className="badge orange">{c}</code>)}
        </div>
      )}
      <div className="small muted" style={{ marginTop: 8 }}>Reading from {timeAgo(latest.recorded_at)}</div>
    </>
  );
}

function OverviewTab({ device, live }: { device: Device; live?: LiveDevice }) {
  const trends = useQuery({
    queryKey: ["trends", device.id],
    queryFn: () => api.get<{ series: Record<string, any>[] }>(`/analytics/devices/${device.id}`, { last_n: 300 }),
    refetchInterval: 20_000,
  });
  const series = trends.data?.series ?? [];
  const pos = live?.latest ? [[live.latest.gps_lat, live.latest.gps_lon] as [number, number]] : [];
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid grid-3">
        <Card className="span-2" title="Live sensors"><LiveSensors latest={live?.latest} /></Card>
        <Card title="Position" flush>
          {live?.latest ? <FleetMap fit={pos} fitOnce={false} className="map"><VehicleLayer devices={[live]} /></FleetMap> : <Empty>No position yet.</Empty>}
        </Card>
      </div>
      <div className="grid grid-2">
        <Card title="Engine temperature" sub="°C · last 300 readings"><MetricLine data={series} dataKey="engine_temp" color={C.orange} unit="°C" threshold={110} /></Card>
        <Card title="Battery voltage" sub="V"><MetricLine data={series} dataKey="battery_voltage" color={C.green} unit="V" threshold={11.8} /></Card>
        <Card title="Vibration" sub="g"><MetricLine data={series} dataKey="vibration" color={C.purple} unit="g" threshold={6} /></Card>
        <Card title="Speed & RPM" sub="km/h"><MetricLine data={series} dataKey="speed" color={C.blue} unit="km/h" /></Card>
      </div>
    </div>
  );
}

function ForecastTab({ deviceId }: { deviceId: string }) {
  const fc = useQuery({ queryKey: ["forecast", deviceId], queryFn: () => api.get<DeviceForecast>(`/analytics/devices/${deviceId}/forecast`), refetchInterval: 60_000 });
  if (fc.isLoading) return <Loading />;
  const f = fc.data;
  if (!f || !f.signals.length) return <Card><Empty>Not enough recent data to forecast (needs ~30 readings in the last 6 hours).</Empty></Card>;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="info-box">
        Trend lines are fitted to bucketed medians of the last {num(f.window_hours)} hours ({f.sample_count} readings).
        A time-to-limit is only shown when the trend points toward the limit and fits well (R² ≥ 0.5).
      </div>
      <div className="grid grid-2">
        {f.signals.map((s) => (
          <Card key={s.signal} title={s.label}
            sub={`Now ${num(s.current, 2)} ${s.unit} · limit ${s.threshold} ${s.unit} · ${s.slope_per_hour > 0 ? "+" : ""}${s.slope_per_hour} ${s.unit}/h · R² ${s.r2}`}
            actions={<SeverityBadge value={s.risk === "NONE" ? null : s.risk} />}>
            <ForecastChart history={s.history} threshold={s.threshold} slope={s.slope_per_hour} current={s.current} hoursTo={s.hours_to_threshold} unit={s.unit} />
            <div className="small" style={{ marginTop: 6 }}>
              {s.hours_to_threshold === null ? <span className="muted">Not trending toward the limit.</span> : (
                <>Predicted <strong>{humanize(s.predicted_fault)}</strong> {s.hours_to_threshold === 0 ? "— limit already crossed" : <>in <strong>{hoursLabel(s.hours_to_threshold)}</strong></>}</>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

function ModelTab({ deviceId }: { deviceId: string }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const versions = useQuery({ queryKey: ["models", deviceId], queryFn: () => api.get<ModelVersion[]>("/ml/models", { device_id: deviceId }) });
  const pin = useMutation({
    mutationFn: ({ id, pinned }: { id: string; pinned: boolean }) => api.post(`/ml/models/${id}/${pinned ? "pin" : "unpin"}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }),
  });
  const retrain = useMutation({
    mutationFn: () => api.post(`/ml/devices/${deviceId}/retrain`),
    onSuccess: () => toast({ title: "Retrain queued", body: "It runs on the next worker cycle (≤ 1 min).", kind: "success" }),
  });
  const manage = atLeast(user?.role, "MANAGER");
  return (
    <Card flush title="Anomaly model versions"
      sub="Pin a known-good version so automatic retraining can't learn a slow degradation as 'normal'."
      actions={manage && <button className="btn sm" onClick={() => retrain.mutate()}><RefreshCw size={14} /> Retrain now</button>}>
      <ErrorBox error={pin.error || retrain.error} />
      {!versions.data?.length && <Empty>No model trained yet — one is trained once the vehicle has ~10 readings.</Empty>}
      {!!versions.data?.length && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Version</th><th>Scope</th><th>Trained</th><th>Reason</th><th className="num">Samples</th><th>Training window</th><th className="num">Drift (PSI)</th><th>State</th><th /></tr></thead>
            <tbody>
              {versions.data.map((v) => (
                <tr key={v.id}>
                  <td><strong>v{v.version}</strong></td>
                  <td>{v.scope === "CLASS" ? <span className="badge purple">Class · {v.device_type}</span> : <span className="badge">Vehicle</span>}</td>
                  <td className="small nowrap">{fmtDate(v.trained_at)}</td>
                  <td><span className="badge">{humanize(v.reason)}</span></td>
                  <td className="num">{v.n_train}</td>
                  <td className="small">{v.window_start ? `${fmtDate(v.window_start)} → ${fmtDate(v.window_end)}` : "—"}</td>
                  <td className="num">{v.drift_psi == null ? "—" : <span style={{ color: v.drift_psi > 0.25 ? "var(--red)" : undefined }}>{v.drift_psi.toFixed(3)}</span>}</td>
                  <td>{v.is_active && <span className="badge green">Active</span>} {v.pinned && <span className="badge blue">Pinned</span>}</td>
                  <td className="right">
                    {manage && v.scope === "DEVICE" && v.has_artifact && (
                      <button className="btn sm ghost" onClick={() => pin.mutate({ id: v.id, pinned: !v.pinned })}>
                        {v.pinned ? <><PinOff size={14} /> Unpin</> : <><Pin size={14} /> Pin</>}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function SettingsTab({ device }: { device: Device }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const [key, setKey] = useState<string | null>(null);
  const drivers = useQuery({ queryKey: ["drivers"], queryFn: () => api.get<Driver[]>("/drivers") });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["devices"] }); qc.invalidateQueries({ queryKey: ["live"] }); };
  const update = useMutation({ mutationFn: (body: Record<string, unknown>) => api.patch(`/devices/${device.id}`, body), onSuccess: () => { refresh(); toast({ title: "Vehicle saved", kind: "success" }); } });
  const rotate = useMutation({ mutationFn: () => api.post<{ api_key: string }>(`/devices/${device.id}/api-key`), onSuccess: (r) => { setKey(r.api_key); refresh(); } });
  const revoke = useMutation({ mutationFn: () => api.del(`/devices/${device.id}/api-key`), onSuccess: refresh });
  const del = useMutation({ mutationFn: () => api.del(`/devices/${device.id}`), onSuccess: () => { refresh(); nav("/vehicles"); } });
  const manage = atLeast(user?.role, "MANAGER");
  if (!manage) return <Card><Empty>Only managers and admins can change vehicle settings.</Empty></Card>;

  return (
    <div className="grid grid-2">
      <Card title="Vehicle details">
        <VehicleForm initial={device} onSubmit={(b) => update.mutate(b)} busy={update.isPending} error={update.error} submitLabel="Save" />
      </Card>
      <div className="stack" style={{ gap: 16 }}>
        <Card title="Driver & status">
          <div className="stack">
            <Field label="Assigned driver">
              <select value={device.assigned_driver_id ?? ""} onChange={(e) => update.mutate(e.target.value ? { assigned_driver_id: e.target.value } : { unassign_driver: true })}>
                <option value="">Unassigned</option>
                {drivers.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </Field>
            <label className="checkbox">
              <input type="checkbox" checked={device.is_active} onChange={(e) => update.mutate({ is_active: e.target.checked })} />
              Active (inactive vehicles are skipped by the analytics worker)
            </label>
          </div>
        </Card>
        <Card title="Device API key" sub="The telematics unit sends X-Device-Key with each reading. No user login needed.">
          <div className="stack">
            <div className="small">Current key: {device.api_key_prefix ? <code>vw_{device.api_key_prefix}_••••••••</code> : <span className="muted">none issued</span>}</div>
            {key && (
              <div className="info-box">
                <div style={{ fontWeight: 650, marginBottom: 4 }}>Copy this key now. It won't be shown again.</div>
                <div className="row"><code style={{ wordBreak: "break-all" }}>{key}</code>
                  <button className="btn sm icon" aria-label="Copy key" onClick={() => navigator.clipboard?.writeText(key)}><Copy size={14} /></button></div>
                <div className="small" style={{ marginTop: 6 }}>POST readings to <code>/api/v1/ingest/telemetry</code> or batches to <code>/api/v1/ingest/telemetry/batch</code>.</div>
              </div>
            )}
            <div className="row">
              <button className="btn" onClick={() => (!device.api_key_prefix || confirm("Rotating invalidates the current key immediately. Continue?")) && rotate.mutate()}>
                <KeyRound size={14} /> {device.api_key_prefix ? "Rotate key" : "Issue key"}
              </button>
              {device.api_key_prefix && <button className="btn danger" onClick={() => confirm("Revoke the key? The device will stop reporting.") && revoke.mutate()}>Revoke</button>}
            </div>
            <ErrorBox error={rotate.error || revoke.error} />
          </div>
        </Card>
        {user?.role === "ADMIN" && (
          <Card title="Danger zone">
            <button className="btn danger" onClick={() => confirm(`Delete ${device.name} and all its telemetry, alerts and trips? This cannot be undone.`) && del.mutate()}>
              <Trash2 size={14} /> Delete vehicle
            </button>
            <ErrorBox error={del.error} />
          </Card>
        )}
      </div>
    </div>
  );
}

export default function VehicleDetail() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "overview";
  const device = useQuery({ queryKey: ["devices", id], queryFn: () => api.get<Device>(`/devices/${id}`) });
  const live = useQuery({ queryKey: ["live"], queryFn: () => api.get<LiveDevice[]>("/devices/live"), refetchInterval: 30_000 });
  const l = live.data?.find((d) => d.id === id);

  if (device.isLoading) return <Loading />;
  if (device.error || !device.data) return <ErrorBox error={device.error ?? "Vehicle not found"} />;
  const d = device.data;

  return (
    <>
      <Link to="/vehicles" className="small row" style={{ marginBottom: 8 }}><ArrowLeft size={14} /> All vehicles</Link>
      <PageHead
        title={d.name}
        sub={
          <span className="row wrap">
            {[d.make, d.model, d.year].filter(Boolean).join(" ") || d.device_type}
            {d.license_plate && <span className="badge">{d.license_plate}</span>}
            {l?.online ? <span className="badge green">Online</span> : <span className="badge">Offline · {timeAgo(d.last_seen_at)}</span>}
            {l && <Health score={l.health_score} />}
            {l?.driver_name && <span className="small muted">Driver: {l.driver_name}</span>}
            <span className="small muted">{num(d.odometer_km, 0)} km</span>
          </span>
        }
      />
      <Tabs<Tab>
        value={tab}
        onChange={(t) => setParams({ tab: t })}
        tabs={[
          { id: "overview", label: "Overview" }, { id: "forecast", label: "Failure forecast" }, { id: "alerts", label: "Alerts" },
          { id: "trips", label: "Trips" }, { id: "maintenance", label: "Maintenance" }, { id: "model", label: "ML model" }, { id: "settings", label: "Settings" },
        ]}
      />
      {tab === "overview" && <OverviewTab device={d} live={l} />}
      {tab === "forecast" && <ForecastTab deviceId={id} />}
      {tab === "alerts" && <Alerts deviceId={id} />}
      {tab === "trips" && <TripsTable deviceId={id} />}
      {tab === "maintenance" && <div className="stack" style={{ gap: 16 }}><WorkOrderBoard deviceId={id} /><Schedules deviceId={id} /></div>}
      {tab === "model" && <ModelTab deviceId={id} />}
      {tab === "settings" && <SettingsTab device={d} />}
    </>
  );
}
