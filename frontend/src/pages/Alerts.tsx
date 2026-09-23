import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { CheckCheck, Download } from "lucide-react";
import { api } from "../api/client";
import type { Alert, Device, Paginated } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import AlertDrawer from "../components/AlertDrawer";
import { Badge, Card, Empty, FaultBadge, Loading, PageHead, Pager, SeverityBadge } from "../components/ui";
import { canOperate, fmtDate, humanize } from "../lib/format";

const FAULTS = ["COOLANT_LEAK", "BATTERY_FAILURE", "TRANSMISSION_STRESS", "ENGINE_STRESS", "WHEEL_BEARING",
  "BRAKE_WEAR", "LOW_OIL_PRESSURE", "TIRE_PRESSURE", "UNKNOWN_ANOMALY"];

export default function Alerts({ deviceId }: { deviceId?: string }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ severity: "", acknowledged: deviceId ? "" : "false", fault_type: "", device_id: deviceId ?? "", feedback: "" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const openId = params.get("id");

  useEffect(() => setPage(1), [filters]);

  const devices = useQuery({ queryKey: ["devices"], queryFn: () => api.get<Device[]>("/devices"), enabled: !deviceId });
  const alerts = useQuery({
    queryKey: ["alerts", "list", filters, page],
    queryFn: () => api.get<Paginated<Alert>>("/alerts", { ...filters, page, page_size: 50 }),
    refetchInterval: 30_000,
  });
  const bulk = useMutation({
    mutationFn: () => api.post("/alerts/acknowledge", { alert_ids: [...selected] }),
    onSuccess: () => {
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["fleet-summary"] });
    },
  });

  const set = (k: keyof typeof filters) => (e: React.ChangeEvent<HTMLSelectElement>) => setFilters({ ...filters, [k]: e.target.value });
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };
  const items = alerts.data?.items ?? [];
  const allChecked = items.length > 0 && items.every((a) => selected.has(a.id));
  const canAct = canOperate(user?.role);

  const body = (
    <Card
      flush
      title={
        <div className="filters">
          <select value={filters.severity} onChange={set("severity")} aria-label="Severity">
            <option value="">All severities</option><option>CRITICAL</option><option>MEDIUM</option><option>LOW</option>
          </select>
          <select value={filters.acknowledged} onChange={set("acknowledged")} aria-label="Status">
            <option value="">Any status</option><option value="false">Unacknowledged</option><option value="true">Acknowledged</option>
          </select>
          <select value={filters.fault_type} onChange={set("fault_type")} aria-label="Fault">
            <option value="">All faults</option>
            {FAULTS.map((f) => <option key={f} value={f}>{humanize(f)}</option>)}
          </select>
          {!deviceId && (
            <select value={filters.device_id} onChange={set("device_id")} aria-label="Vehicle">
              <option value="">All vehicles</option>
              {devices.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          )}
          <select value={filters.feedback} onChange={set("feedback")} aria-label="Feedback">
            <option value="">Any feedback</option><option value="TRUE_POSITIVE">Real fault</option><option value="FALSE_POSITIVE">False alarm</option>
          </select>
        </div>
      }
      actions={
        <>
          {canAct && selected.size > 0 && (
            <button className="btn sm primary" onClick={() => bulk.mutate()} disabled={bulk.isPending}>
              <CheckCheck size={14} /> Acknowledge {selected.size}
            </button>
          )}
          <span className="small muted">{alerts.data?.total ?? 0} alerts</span>
        </>
      }
    >
      {alerts.isLoading && <Loading />}
      {alerts.data && !items.length && <Empty>No alerts match these filters.</Empty>}
      {!!items.length && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {canAct && <th style={{ width: 32 }}><input type="checkbox" aria-label="Select all" checked={allChecked} onChange={() => setSelected(allChecked ? new Set() : new Set(items.map((a) => a.id)))} /></th>}
                <th>Severity</th><th>Vehicle</th><th>Fault</th><th>Diagnosis</th><th className="num">Score</th><th>Status</th><th>When</th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id} className="clickable" onClick={() => { const next = new URLSearchParams(params); next.set("id", a.id); setParams(next); }}>
                  {canAct && <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label="Select" checked={selected.has(a.id)} onChange={() => toggle(a.id)} /></td>}
                  <td><SeverityBadge value={a.severity} /></td>
                  <td className="nowrap"><strong>{a.device_name}</strong></td>
                  <td><FaultBadge value={a.fault_type} /></td>
                  <td className="small muted" style={{ maxWidth: 380 }}><div className="ellipsis">{a.llm_summary ?? "Summary pending…"}</div></td>
                  <td className="num mono">{a.anomaly_score.toFixed(3)}</td>
                  <td className="nowrap">
                    {a.acknowledged ? <span className="badge green">Acknowledged</span> : <span className="badge red">Open</span>}
                    {a.feedback && <> <Badge value={a.feedback} /></>}
                    {a.work_order_id && <span className="badge blue" style={{ marginLeft: 4 }}>WO</span>}
                  </td>
                  <td className="small muted nowrap">{fmtDate(a.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pager page={page} pages={alerts.data?.pages ?? 1} onPage={setPage} />
    </Card>
  );

  const drawer = openId && <AlertDrawer alertId={openId} onClose={() => { params.delete("id"); setParams(params); }} />;

  if (deviceId) return <>{body}{drawer}</>;
  return (
    <>
      <PageHead
        title="Alerts"
        sub="Anomalies found by the ML ensemble, classified and explained."
        actions={<button className="btn" onClick={() => api.download("/reports/export/alerts", {}, "alerts.csv")}><Download size={16} /> Export CSV</button>}
      />
      {body}
      {drawer}
    </>
  );
}
