import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Activity, AlertTriangle, Gauge, Route, Truck, Wrench } from "lucide-react";
import { api } from "../api/client";
import type { Alert, DeviceForecast, FleetSummary, Geofence, LiveDevice, Paginated } from "../api/types";
import AlertDrawer from "../components/AlertDrawer";
import { HBar, SeverityTimeline } from "../components/charts";
import FleetMap, { GeofenceLayer, VehicleLayer } from "../components/FleetMap";
import { Card, Empty, FaultBadge, Health, PageHead, SeverityBadge, Stat } from "../components/ui";
import { hoursLabel, humanize, num, timeAgo } from "../lib/format";

export default function Overview() {
  const [openAlert, setOpenAlert] = useState<string | null>(null);
  const summary = useQuery({ queryKey: ["fleet-summary"], queryFn: () => api.get<FleetSummary>("/analytics/fleet"), refetchInterval: 30_000 });
  const live = useQuery({ queryKey: ["live"], queryFn: () => api.get<LiveDevice[]>("/devices/live"), refetchInterval: 30_000 });
  const timeline = useQuery({ queryKey: ["alerts", "timeline"], queryFn: () => api.get<any[]>("/analytics/timeline", { days: 7 }) });
  const forecast = useQuery({ queryKey: ["forecast"], queryFn: () => api.get<DeviceForecast[]>("/analytics/forecast"), refetchInterval: 120_000 });
  const recent = useQuery({ queryKey: ["alerts", "recent"], queryFn: () => api.get<Paginated<Alert>>("/alerts", { page_size: 8, acknowledged: false }) });
  const fences = useQuery({ queryKey: ["geofences"], queryFn: () => api.get<Geofence[]>("/geofences") });

  const s = summary.data;
  const atRisk = (forecast.data ?? []).filter((f) => f.overall_risk !== "NONE" && f.overall_risk !== "LOW").slice(0, 5);
  const faults = Object.entries(s?.alerts_by_fault_type ?? {}).map(([k, v]) => ({ name: humanize(k), count: v })).sort((a, b) => b.count - a.count);
  const devices = live.data ?? [];
  const positions = devices.filter((d) => d.latest).map((d) => [d.latest!.gps_lat, d.latest!.gps_lon] as [number, number]);

  return (
    <>
      <PageHead title="Fleet overview" sub="Health, risk and activity across your fleet, live." />
      <div className="grid grid-6" style={{ marginBottom: 16 }}>
        <Stat label="Vehicles online" icon={<Truck size={14} />} value={s ? `${s.online_devices}/${s.total_devices}` : "—"} hint={`${s?.active_devices ?? 0} active`} />
        <Stat label="Open alerts" icon={<AlertTriangle size={14} />} value={s?.unacknowledged_alerts ?? "—"} color={s?.unacknowledged_alerts ? "var(--red)" : undefined} hint={`${s?.alerts_last_24h ?? 0} in last 24 h`} />
        <Stat label="Critical (all time)" icon={<Activity size={14} />} value={s?.alerts_by_severity.CRITICAL ?? 0} hint={`${s?.total_alerts ?? 0} alerts total`} />
        <Stat label="Open work orders" icon={<Wrench size={14} />} value={s?.open_work_orders ?? "—"} />
        <Stat label="Distance (24 h)" icon={<Route size={14} />} value={s ? `${num(s.distance_km_24h, 0)} km` : "—"} hint={`${s?.trips_24h ?? 0} trips`} />
        <Stat label="Avg engine temp (24 h)" icon={<Gauge size={14} />} value={s?.avg_engine_temp ? `${num(s.avg_engine_temp)} °C` : "—"} hint={s?.avg_fuel_level ? `Avg fuel ${num(s.avg_fuel_level, 0)}%` : undefined} />
      </div>

      <div className="grid grid-3" style={{ marginBottom: 16 }}>
        <Card className="span-2" title="Live fleet" sub="Colour shows health score" actions={<Link to="/map" className="btn sm">Full map</Link>} flush>
          <FleetMap fit={positions}>
            <GeofenceLayer fences={fences.data ?? []} />
            <VehicleLayer devices={devices} />
          </FleetMap>
        </Card>
        <Card title="Failure forecast" sub="Vehicles trending toward a fault" actions={<Link to="/vehicles" className="btn sm">All vehicles</Link>} flush>
          {!atRisk.length && <Empty>No vehicle is trending toward a failure threshold.</Empty>}
          {atRisk.map((f) => {
            const top = f.signals[0];
            return (
              <Link key={f.device_id} to={`/vehicles/${f.device_id}?tab=forecast`} style={{ display: "block", padding: "12px 16px", borderBottom: "1px solid var(--border)", color: "inherit", textDecoration: "none" }}>
                <div className="row between">
                  <strong>{f.device_name}</strong>
                  <SeverityBadge value={f.overall_risk} />
                </div>
                <div className="small muted">
                  {top.label} {top.direction === "rising" ? "↑" : "↓"} {num(top.current, 2)} {top.unit} → limit {top.threshold} {top.unit}
                </div>
                <div className="small">
                  {top.hours_to_threshold === 0
                    ? <>Limit crossed — likely <strong>{humanize(top.predicted_fault)}</strong></>
                    : <>Predicted {humanize(top.predicted_fault)} in <strong>{hoursLabel(top.hours_to_threshold)}</strong></>}
                </div>
              </Link>
            );
          })}
        </Card>
      </div>

      <div className="grid grid-3" style={{ marginBottom: 16 }}>
        <Card className="span-2" title="Alerts, last 7 days">
          {timeline.data ? <SeverityTimeline data={timeline.data} /> : null}
        </Card>
        <Card title="Faults detected">
          {faults.length ? <HBar data={faults} dataKey="count" nameKey="name" height={220} /> : <Empty>No faults detected yet.</Empty>}
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Unacknowledged alerts" actions={<Link to="/alerts" className="btn sm">View all</Link>} flush>
          {!recent.data?.items.length && <Empty>No open alerts. Nice.</Empty>}
          {!!recent.data?.items.length && (
            <div className="table-wrap">
              <table>
                <tbody>
                  {recent.data.items.map((a) => (
                    <tr key={a.id} className="clickable" onClick={() => setOpenAlert(a.id)}>
                      <td><SeverityBadge value={a.severity} /></td>
                      <td><strong>{a.device_name}</strong></td>
                      <td><FaultBadge value={a.fault_type} /></td>
                      <td className="small muted nowrap right">{timeAgo(a.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card title="Vehicle health" actions={<Link to="/vehicles" className="btn sm">Manage</Link>} flush>
          {!devices.length && <Empty>No vehicles yet — add one on the Vehicles page or run the simulator.</Empty>}
          {!!devices.length && (
            <div className="table-wrap">
              <table>
                <tbody>
                  {[...devices].sort((a, b) => a.health_score - b.health_score).map((d) => (
                    <tr key={d.id}>
                      <td><span className={`dot ${d.online ? "green" : ""}`} /> <Link to={`/vehicles/${d.id}`}><strong>{d.name}</strong></Link></td>
                      <td><Health score={d.health_score} /></td>
                      <td className="small">{d.latest ? `${num(d.latest.speed, 0)} km/h` : "—"}</td>
                      <td className="small muted right nowrap">{timeAgo(d.last_seen_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
      {openAlert && <AlertDrawer alertId={openAlert} onClose={() => setOpenAlert(null)} />}
    </>
  );
}
