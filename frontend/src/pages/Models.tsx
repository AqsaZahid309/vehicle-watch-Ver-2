import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { DetectorMetrics, ModelVersion } from "../api/types";
import { Card, Empty, PageHead, Stat } from "../components/ui";
import { fmtDate, humanize, pct } from "../lib/format";

interface ActiveRow { device_id: string; device_name: string; device_type: string; model: ModelVersion | null }

function Precision({ value }: { value: number | null }) {
  if (value == null) return <span className="muted">—</span>;
  const color = value >= 0.8 ? "var(--green)" : value >= 0.5 ? "var(--yellow)" : "var(--red)";
  return <strong style={{ color }}>{pct(value)}</strong>;
}

export default function Models() {
  const metrics = useQuery({ queryKey: ["ml-metrics"], queryFn: () => api.get<DetectorMetrics>("/ml/metrics") });
  const active = useQuery({ queryKey: ["ml-active"], queryFn: () => api.get<ActiveRow[]>("/ml/active"), refetchInterval: 60_000 });
  const m = metrics.data;
  const labelledShare = m && m.total_alerts ? m.labelled_alerts / m.total_alerts : null;

  return (
    <>
      <PageHead title="ML models" sub="How well the detector performs, and which model is scoring each vehicle." />
      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat label="Detector precision" value={<Precision value={m?.precision ?? null} />} hint="Real faults ÷ labelled alerts" />
        <Stat label="Alerts" value={m?.total_alerts ?? "—"} />
        <Stat label="Labelled" value={m?.labelled_alerts ?? "—"} hint={labelledShare != null ? `${pct(labelledShare)} of alerts` : undefined} />
        <Stat label="Vehicles modelled" value={active.data ? active.data.filter((a) => a.model).length : "—"} hint={`of ${active.data?.length ?? 0}`} />
      </div>
      <div className="info-box" style={{ marginBottom: 16 }}>
        Labels come from operators marking alerts "real fault" or "false alarm", and from work orders resolved with a root cause.
        More labels give a more trustworthy precision figure, and show where the detector or its thresholds need tuning.
      </div>
      <div className="grid grid-2" style={{ marginBottom: 16 }}>
        <Card title="Precision by fault type" flush>
          {!m?.by_fault_type.length && <Empty>No alerts yet.</Empty>}
          {!!m?.by_fault_type.length && (
            <table>
              <thead><tr><th>Fault</th><th className="num">Alerts</th><th className="num">Real</th><th className="num">False</th><th className="num">Precision</th></tr></thead>
              <tbody>
                {m.by_fault_type.map((f) => (
                  <tr key={f.fault_type}><td>{humanize(f.fault_type)}</td><td className="num">{f.alerts}</td><td className="num">{f.true_positive}</td><td className="num">{f.false_positive}</td><td className="num"><Precision value={f.precision} /></td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Precision by vehicle" flush>
          {!m?.by_device.length && <Empty>No alerts yet.</Empty>}
          {!!m?.by_device.length && (
            <table>
              <thead><tr><th>Vehicle</th><th className="num">Alerts</th><th className="num">Real</th><th className="num">False</th><th className="num">Precision</th></tr></thead>
              <tbody>
                {m.by_device.map((d) => (
                  <tr key={d.device_id}><td><Link to={`/vehicles/${d.device_id}?tab=model`}>{d.device_name}</Link></td><td className="num">{d.alerts}</td><td className="num">{d.true_positive}</td><td className="num">{d.false_positive}</td><td className="num"><Precision value={d.precision} /></td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
      <Card title="Active model per vehicle" sub="Class models score new vehicles until they have enough history of their own." flush>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Vehicle</th><th>Model</th><th>Trained</th><th>Reason</th><th className="num">Samples</th><th className="num">Drift (PSI)</th><th>State</th></tr></thead>
            <tbody>
              {active.data?.map((a) => (
                <tr key={a.device_id}>
                  <td><Link to={`/vehicles/${a.device_id}?tab=model`}><strong>{a.device_name}</strong></Link></td>
                  {a.model ? (
                    <>
                      <td>v{a.model.version} {a.model.scope === "CLASS" ? <span className="badge purple">class · {a.model.device_type}</span> : <span className="badge">vehicle</span>}</td>
                      <td className="small">{fmtDate(a.model.trained_at)}</td>
                      <td>{humanize(a.model.reason)}</td>
                      <td className="num">{a.model.n_train}</td>
                      <td className="num">{a.model.drift_psi?.toFixed(3) ?? "—"}</td>
                      <td>{a.model.pinned ? <span className="badge blue">Pinned</span> : <span className="badge green">Auto</span>}</td>
                    </>
                  ) : <td colSpan={6} className="muted small">Waiting for data — trains after ~10 readings.</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
