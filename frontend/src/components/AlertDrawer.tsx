import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Check, ThumbsDown, ThumbsUp, Wrench } from "lucide-react";
import { api } from "../api/client";
import type { Alert, WorkOrder } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { canOperate, FEATURE_LABELS, fmtDate, num } from "../lib/format";
import { Badge, Drawer, ErrorBox, FaultBadge, Loading, SeverityBadge } from "./ui";
import { useToast } from "./Toasts";

const SENSORS: [keyof NonNullable<Alert["telemetry"]>, string, string][] = [
  ["engine_temp", "Engine temp", "°C"], ["rpm", "RPM", ""], ["battery_voltage", "Battery", "V"],
  ["vibration", "Vibration", "g"], ["speed", "Speed", "km/h"], ["fuel_level", "Fuel", "%"],
  ["oil_pressure", "Oil pressure", "psi"], ["coolant_level", "Coolant", "%"], ["tire_pressure", "Tyre", "psi"],
];

export default function AlertDrawer({ alertId, onClose }: { alertId: string; onClose: () => void }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const [notes, setNotes] = useState("");
  const { data: a, isLoading, error } = useQuery({
    queryKey: ["alerts", "detail", alertId],
    queryFn: () => api.get<Alert>(`/alerts/${alertId}`),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["alerts"] });
    qc.invalidateQueries({ queryKey: ["fleet-summary"] });
  };
  const ack = useMutation({
    mutationFn: (acknowledged: boolean) => api.patch(`/alerts/${alertId}/acknowledge`, { acknowledged }),
    onSuccess: refresh,
  });
  const feedback = useMutation({
    mutationFn: (fb: string) => api.post(`/alerts/${alertId}/feedback`, { feedback: fb, notes: notes || null }),
    onSuccess: () => { refresh(); toast({ title: "Feedback recorded", kind: "success" }); },
  });
  const createWo = useMutation({
    mutationFn: () => api.post<WorkOrder>("/maintenance/work-orders", {
      alert_id: alertId, priority: a?.severity === "CRITICAL" ? "URGENT" : "HIGH",
    }),
    onSuccess: (wo) => {
      refresh();
      qc.invalidateQueries({ queryKey: ["work-orders"] });
      toast({ title: `Work order #${wo.number} created`, kind: "success" });
      nav(`/maintenance?wo=${wo.id}`);
    },
  });

  const canAct = canOperate(user?.role);
  const contributors = a?.affected_metrics.top_contributors ?? [];
  const ens = a?.affected_metrics.ensemble;
  const maxZ = Math.max(1, ...contributors.map((c) => Math.abs(c.z_score)));

  return (
    <Drawer title="Alert detail" onClose={onClose}>
      {isLoading && <Loading />}
      <ErrorBox error={error} />
      {a && (
        <div className="stack" style={{ gap: 18 }}>
          <div>
            <div className="row wrap" style={{ marginBottom: 6 }}>
              <SeverityBadge value={a.severity} />
              <FaultBadge value={a.fault_type} />
              {a.fault_confidence && <span className="badge">{a.fault_confidence} confidence</span>}
              {a.feedback && <Badge value={a.feedback} />}
              {a.escalated_at && <span className="badge red">Escalated</span>}
            </div>
            <h2><Link to={`/vehicles/${a.device_id}`}>{a.device_name}</Link></h2>
            <div className="small muted">{fmtDate(a.created_at)} · anomaly score {a.anomaly_score.toFixed(3)}</div>
          </div>

          {a.llm_summary && (
            <div>
              <h3 style={{ marginBottom: 6 }}>Diagnosis</h3>
              <div className="summary-box">{a.llm_summary}</div>
            </div>
          )}

          {a.telemetry && (
            <div>
              <h3 style={{ marginBottom: 6 }}>Sensor snapshot</h3>
              <div className="sensor-grid">
                {SENSORS.filter(([k]) => a.telemetry![k] != null).map(([k, label, unit]) => (
                  <div className="sensor" key={k}>
                    <div className="s-label">{label}</div>
                    <div className="s-value">{num(a.telemetry![k] as number, 2)} <span className="small muted">{unit}</span></div>
                  </div>
                ))}
              </div>
              {!!a.telemetry.dtc_codes?.length && (
                <div className="row wrap" style={{ marginTop: 8 }}>
                  <span className="small muted">OBD-II codes:</span>
                  {a.telemetry.dtc_codes.map((c) => <code key={c} className="badge orange">{c}</code>)}
                </div>
              )}
            </div>
          )}

          {contributors.length > 0 && (
            <div>
              <h3 style={{ marginBottom: 6 }}>Why it was flagged</h3>
              <div className="stack" style={{ gap: 8 }}>
                {contributors.map((c) => (
                  <div key={c.feature}>
                    <div className="row between small">
                      <span>{FEATURE_LABELS[c.feature] ?? c.feature}: <strong>{num(c.value, 2)}</strong> <span className="muted">(normal ≈ {num(c.train_mean, 2)})</span></span>
                      <span className="mono">{c.z_score > 0 ? "+" : ""}{c.z_score}σ</span>
                    </div>
                    <div className="bar-inline"><span style={{ width: `${(Math.abs(c.z_score) / maxZ) * 100}%`, background: Math.abs(c.z_score) > 3 ? "var(--red)" : "var(--orange)" }} /></div>
                  </div>
                ))}
              </div>
              {ens && (
                <div className="small muted" style={{ marginTop: 8 }}>
                  Isolation Forest {ens.isolation_forest_score.toFixed(3)} · LOF {ens.lof_confirmed ? "confirmed" : "not confirmed"} ·
                  {" "}{ens.n_train_samples} training samples{ens.model_version ? ` · model v${ens.model_version} (${ens.model_scope?.toLowerCase()})` : ""}
                </div>
              )}
            </div>
          )}

          <div>
            <h3 style={{ marginBottom: 6 }}>Status</h3>
            <dl className="kv">
              <dt>Acknowledged</dt><dd>{a.acknowledged ? `Yes · ${fmtDate(a.acknowledged_at)}` : "No"}</dd>
              <dt>Work order</dt><dd>{a.work_order_id ? <Link to={`/maintenance?wo=${a.work_order_id}`}>Open work order →</Link> : "None"}</dd>
              {a.feedback_notes && (<><dt>Feedback notes</dt><dd>{a.feedback_notes}</dd></>)}
            </dl>
          </div>

          {canAct && (
            <div className="stack">
              <div className="row wrap">
                {!a.acknowledged ? (
                  <button className="btn primary" onClick={() => ack.mutate(true)} disabled={ack.isPending}><Check size={16} /> Acknowledge</button>
                ) : (
                  <button className="btn" onClick={() => ack.mutate(false)} disabled={ack.isPending}>Reopen</button>
                )}
                {!a.work_order_id && (
                  <button className="btn" onClick={() => createWo.mutate()} disabled={createWo.isPending}><Wrench size={16} /> Create work order</button>
                )}
              </div>
              <div className="card" style={{ padding: 12 }}>
                <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>Was this a real fault? Your answer measures detector precision.</div>
                <input placeholder="Optional note (e.g. 'confirmed hose split')" value={notes} onChange={(e) => setNotes(e.target.value)} />
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn sm" onClick={() => feedback.mutate("TRUE_POSITIVE")}><ThumbsUp size={14} /> Real fault</button>
                  <button className="btn sm" onClick={() => feedback.mutate("FALSE_POSITIVE")}><ThumbsDown size={14} /> False alarm</button>
                </div>
              </div>
              <ErrorBox error={ack.error || feedback.error || createWo.error} />
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}
