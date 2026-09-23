import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Printer } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, Empty, ErrorBox, Loading, PageHead, Stat } from "../components/ui";
import { humanize, money, num, pct } from "../lib/format";

function isoDay(d: Date) {
  return d.toISOString().slice(0, 10);
}

const EXPORTS = [
  ["alerts", "Alerts"], ["trips", "Trips"], ["work_orders", "Work orders"], ["fuel_events", "Fuel events"],
] as const;

export default function Reports() {
  const { user } = useAuth();
  const [range, setRange] = useState({ start: isoDay(new Date(Date.now() - 30 * 864e5)), end: isoDay(new Date()) });
  const params = { start: `${range.start}T00:00:00Z`, end: `${range.end}T23:59:59Z` };
  const report = useQuery({ queryKey: ["report", range], queryFn: () => api.get<any>("/reports/summary", params) });
  const r = report.data;
  const cur = r?.currency ?? user?.organization.currency ?? "USD";

  return (
    <>
      <PageHead title="Reports" sub={r ? `${r.organization} · ${range.start} to ${range.end}` : "Fleet performance for a period."}
        actions={<>
          <input type="date" value={range.start} max={range.end} onChange={(e) => setRange({ ...range, start: e.target.value })} style={{ width: "auto" }} aria-label="Start" />
          <input type="date" value={range.end} min={range.start} onChange={(e) => setRange({ ...range, end: e.target.value })} style={{ width: "auto" }} aria-label="End" />
          <button className="btn" onClick={() => window.print()}><Printer size={16} /> Print / PDF</button>
        </>} />
      {report.isLoading && <Loading />}
      <ErrorBox error={report.error} />
      {r && (
        <div className="stack" style={{ gap: 16 }}>
          <div className="grid grid-4">
            <Stat label="Estimated cost avoided" value={money(r.cost_avoided.total, cur)} color="var(--green)" hint="Faults fixed early vs. run to failure" />
            <Stat label="Maintenance spend" value={money(r.maintenance.total_cost, cur)} hint={`${r.maintenance.work_orders_resolved} work orders resolved`} />
            <Stat label="Fuel cost" value={money(r.fleet.fuel_cost, cur)} hint={`${num(r.fleet.fuel_liters, 0)} L · ${r.fleet.l_per_100km ?? "—"} L/100 km`} />
            <Stat label="Distance" value={`${num(r.fleet.distance_km, 0)} km`} hint={`${r.fleet.trips} trips · ${r.fleet.driving_hours} h`} />
          </div>
          <div className="grid grid-3">
            <Card title="Alerts">
              <dl className="kv">
                <dt>Total</dt><dd>{r.alerts.total}</dd>
                <dt>Critical / medium / low</dt><dd>{r.alerts.by_severity.CRITICAL ?? 0} / {r.alerts.by_severity.MEDIUM ?? 0} / {r.alerts.by_severity.LOW ?? 0}</dd>
                <dt>Acknowledged</dt><dd>{r.alerts.acknowledged_pct ?? "—"}%</dd>
                <dt>Mean time to acknowledge</dt><dd>{r.alerts.mean_minutes_to_acknowledge != null ? `${r.alerts.mean_minutes_to_acknowledge} min` : "—"}</dd>
                <dt>Detector precision</dt><dd>{r.alerts.precision != null ? pct(r.alerts.precision) : "—"} <span className="muted small">({r.alerts.true_positive} real / {r.alerts.false_positive} false)</span></dd>
              </dl>
            </Card>
            <Card title="Maintenance">
              <dl className="kv">
                <dt>Work orders opened</dt><dd>{r.maintenance.work_orders_created}</dd>
                <dt>Resolved</dt><dd>{r.maintenance.work_orders_resolved}</dd>
                <dt>Mean time to resolve</dt><dd>{r.maintenance.mean_hours_to_resolve != null ? `${r.maintenance.mean_hours_to_resolve} h` : "—"}</dd>
                <dt>Downtime</dt><dd>{r.maintenance.downtime_hours} h</dd>
                <dt>Cost</dt><dd>{money(r.maintenance.total_cost, cur)}</dd>
              </dl>
            </Card>
            <Card title="Operations">
              <dl className="kv">
                <dt>Vehicles</dt><dd>{r.fleet.vehicles}</dd>
                <dt>Average trip score</dt><dd>{r.fleet.avg_trip_score ?? "—"}</dd>
                <dt>Suspected fuel thefts</dt><dd>{r.fleet.fuel_theft_suspected}</dd>
                <dt>Top faults</dt><dd>{Object.entries(r.alerts.by_fault_type as Record<string, number>).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => `${humanize(k)} (${v})`).join(", ") || "—"}</dd>
              </dl>
            </Card>
          </div>
          <Card title="Cost avoided by early detection" sub="Per resolved work order with a confirmed fault: typical run-to-failure cost minus actual repair cost." flush>
            {!r.cost_avoided.by_fault_type.length && <Empty>No confirmed faults resolved in this period. Resolve work orders with a root cause to populate this.</Empty>}
            {!!r.cost_avoided.by_fault_type.length && (
              <table>
                <thead><tr><th>Fault</th><th className="num">Repairs</th><th className="num">Run-to-failure cost (typical)</th><th className="num">Saved</th></tr></thead>
                <tbody>
                  {r.cost_avoided.by_fault_type.map((row: any) => (
                    <tr key={row.fault_type}>
                      <td>{humanize(row.fault_type)}</td><td className="num">{row.count}</td>
                      <td className="num">{money(r.cost_avoided.assumptions[row.fault_type]?.run_to_failure, cur)}</td>
                      <td className="num"><strong>{money(row.saved, cur)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          <Card title="By vehicle" flush>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Vehicle</th><th className="num">Distance</th><th className="num">Trips</th><th className="num">Fuel</th><th className="num">Alerts</th><th className="num">Critical</th><th className="num">Work orders</th><th className="num">Maint. cost</th><th className="num">Downtime</th><th className="num">Trip score</th></tr></thead>
                <tbody>
                  {r.vehicles.map((v: any) => (
                    <tr key={v.device_id}>
                      <td><strong>{v.device_name}</strong></td>
                      <td className="num">{num(v.distance_km, 0)} km</td><td className="num">{v.trips}</td>
                      <td className="num">{num(v.fuel_liters, 0)} L</td><td className="num">{v.alerts}</td>
                      <td className="num">{v.critical_alerts}</td><td className="num">{v.work_orders}</td>
                      <td className="num">{money(v.maintenance_cost, cur)}</td><td className="num">{v.downtime_hours} h</td>
                      <td className="num">{v.avg_trip_score ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card title="Export data (CSV)" className="no-print">
            <div className="row wrap">
              {EXPORTS.map(([kind, label]) => (
                <button key={kind} className="btn" onClick={() => api.download(`/reports/export/${kind}`, params, `${kind}-${range.start}-${range.end}.csv`)}>
                  <Download size={14} /> {label}
                </button>
              ))}
              {user?.role === "ADMIN" && (
                <button className="btn" onClick={() => api.download("/reports/export/audit", params, `audit-${range.start}-${range.end}.csv`)}><Download size={14} /> Audit log</button>
              )}
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
