import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { CalendarClock, Plus, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { Device, Paginated, Schedule, User, WorkOrder, WOStatus } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, Card, Drawer, Empty, ErrorBox, Field, Loading, Modal, PageHead, Tabs } from "../components/ui";
import { useToast } from "../components/Toasts";
import { atLeast, canOperate, fmtDate, humanize, money, num, timeAgo } from "../lib/format";

const COLUMNS: WOStatus[] = ["OPEN", "IN_PROGRESS", "ON_HOLD", "RESOLVED"];
const ROOT_CAUSES = ["COOLANT_LEAK", "BATTERY_FAILURE", "TRANSMISSION_STRESS", "BRAKE_WEAR", "ENGINE_STRESS",
  "WHEEL_BEARING", "LOW_OIL_PRESSURE", "TIRE_PRESSURE", "OTHER_FAULT", "PREVENTIVE_SERVICE", "NO_FAULT_FOUND"];

function useUsers() {
  return useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
}

export function WorkOrderDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const users = useUsers();
  const wo = useQuery({ queryKey: ["work-orders", id], queryFn: () => api.get<WorkOrder>(`/maintenance/work-orders/${id}`) });
  const [draft, setDraft] = useState<Partial<WorkOrder>>({});
  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patch<WorkOrder>(`/maintenance/work-orders/${id}`, body),
    onSuccess: () => {
      setDraft({});
      qc.invalidateQueries({ queryKey: ["work-orders"] });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      toast({ title: "Work order updated", kind: "success" });
    },
  });
  const del = useMutation({
    mutationFn: () => api.del(`/maintenance/work-orders/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["work-orders"] }); onClose(); },
  });

  const w = wo.data;
  const canEdit = atLeast(user?.role, "TECHNICIAN");
  const v = <K extends keyof WorkOrder>(k: K) => (k in draft ? draft[k] : w?.[k]) as WorkOrder[K];
  const set = (k: keyof WorkOrder, val: unknown) => setDraft({ ...draft, [k]: val });

  const save = (extra: Record<string, unknown> = {}) => {
    const body: Record<string, unknown> = { ...draft, ...extra };
    if ("assigned_to_id" in body && !body.assigned_to_id) {
      delete body.assigned_to_id;
      body.unassign = true;
    }
    update.mutate(body);
  };

  return (
    <Drawer title={w ? `Work order #${w.number}` : "Work order"} onClose={onClose}>
      {wo.isLoading && <Loading />}
      {w && (
        <div className="stack" style={{ gap: 16 }}>
          <div>
            <div className="row wrap" style={{ marginBottom: 6 }}><Badge value={w.status} /><Badge value={w.priority} />{w.schedule_id && <span className="badge purple">Preventive</span>}</div>
            <h2>{w.title}</h2>
            <div className="small muted">
              <Link to={`/vehicles/${w.device_id}`}>{w.device_name}</Link> · opened {fmtDate(w.created_at)}
              {w.alert_id && <> · <Link to={`/alerts?id=${w.alert_id}`}>source alert</Link></>}
            </div>
          </div>
          {w.description && <div className="summary-box">{w.description}</div>}

          <div className="form-grid">
            <Field label="Status">
              <select disabled={!canEdit} value={v("status")} onChange={(e) => set("status", e.target.value)}>
                {[...COLUMNS, "CANCELLED"].map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
              </select>
            </Field>
            <Field label="Priority">
              <select disabled={!canEdit} value={v("priority")} onChange={(e) => set("priority", e.target.value)}>
                {["LOW", "MEDIUM", "HIGH", "URGENT"].map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
              </select>
            </Field>
            <Field label="Assigned to">
              <select disabled={!canEdit} value={v("assigned_to_id") ?? ""} onChange={(e) => set("assigned_to_id", e.target.value || null)}>
                <option value="">Unassigned</option>
                {users.data?.filter((u) => u.is_active).map((u) => <option key={u.id} value={u.id}>{u.full_name || u.email} ({u.role.toLowerCase()})</option>)}
              </select>
            </Field>
            <Field label="Root cause" help="Required to resolve. Labels the source alert as a real fault or a false alarm.">
              <select disabled={!canEdit} value={v("root_cause") ?? ""} onChange={(e) => set("root_cause", e.target.value || null)}>
                <option value="">Not determined</option>
                {ROOT_CAUSES.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}
              </select>
            </Field>
            <Field label="Parts cost"><input disabled={!canEdit} type="number" min={0} value={v("parts_cost")} onChange={(e) => set("parts_cost", Number(e.target.value))} /></Field>
            <Field label="Labour cost"><input disabled={!canEdit} type="number" min={0} value={v("labor_cost")} onChange={(e) => set("labor_cost", Number(e.target.value))} /></Field>
            <Field label="Downtime (hours)"><input disabled={!canEdit} type="number" min={0} step={0.5} value={v("downtime_hours")} onChange={(e) => set("downtime_hours", Number(e.target.value))} /></Field>
            <Field label="Due"><input disabled={!canEdit} type="date" value={(v("due_date") ?? "").slice(0, 10)} onChange={(e) => set("due_date", e.target.value ? new Date(e.target.value).toISOString() : null)} /></Field>
            <Field label="Resolution notes" full>
              <textarea disabled={!canEdit} value={v("resolution_notes") ?? ""} onChange={(e) => set("resolution_notes", e.target.value)} />
            </Field>
          </div>
          <ErrorBox error={update.error || del.error} />
          {canEdit && (
            <div className="row wrap">
              <button className="btn primary" disabled={!Object.keys(draft).length || update.isPending} onClick={() => save()}>Save changes</button>
              {w.status !== "RESOLVED" && (
                <button className="btn" disabled={update.isPending} onClick={() => save({ status: "RESOLVED" })}>Resolve</button>
              )}
              <div style={{ flex: 1 }} />
              {atLeast(user?.role, "MANAGER") && (
                <button className="btn danger" onClick={() => confirm("Delete this work order?") && del.mutate()}><Trash2 size={14} /> Delete</button>
              )}
            </div>
          )}
          <dl className="kv small">
            <dt>Total cost</dt><dd>{money(w.total_cost, user?.organization.currency)}</dd>
            <dt>Started</dt><dd>{fmtDate(w.started_at)}</dd>
            <dt>Resolved</dt><dd>{fmtDate(w.resolved_at)}</dd>
            <dt>Last updated</dt><dd>{timeAgo(w.updated_at)}</dd>
          </dl>
        </div>
      )}
    </Drawer>
  );
}

export function NewWorkOrder({ deviceId, onClose }: { deviceId?: string; onClose: () => void }) {
  const qc = useQueryClient();
  const devices = useQuery({ queryKey: ["devices"], queryFn: () => api.get<Device[]>("/devices") });
  const users = useUsers();
  const [f, setF] = useState({ device_id: deviceId ?? "", title: "", description: "", priority: "MEDIUM", assigned_to_id: "", due_date: "" });
  const create = useMutation({
    mutationFn: () => api.post("/maintenance/work-orders", {
      ...f, assigned_to_id: f.assigned_to_id || null, due_date: f.due_date ? new Date(f.due_date).toISOString() : null,
      description: f.description || null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["work-orders"] }); onClose(); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate(); };
  return (
    <Modal title="New work order" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <div className="form-grid">
          <Field label="Vehicle" full>
            <select required value={f.device_id} onChange={(e) => setF({ ...f, device_id: e.target.value })} disabled={!!deviceId}>
              <option value="">Select…</option>
              {devices.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
          <Field label="Title" full><input required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Replace front brake pads" /></Field>
          <Field label="Priority">
            <select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}>
              {["LOW", "MEDIUM", "HIGH", "URGENT"].map((p) => <option key={p} value={p}>{humanize(p)}</option>)}
            </select>
          </Field>
          <Field label="Due date"><input type="date" value={f.due_date} onChange={(e) => setF({ ...f, due_date: e.target.value })} /></Field>
          <Field label="Assign to" full>
            <select value={f.assigned_to_id} onChange={(e) => setF({ ...f, assigned_to_id: e.target.value })}>
              <option value="">Unassigned</option>
              {users.data?.map((u) => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
            </select>
          </Field>
          <Field label="Description" full><textarea value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        </div>
        <ErrorBox error={create.error} />
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn primary" disabled={create.isPending}>Create</button></div>
      </form>
    </Modal>
  );
}

export function WorkOrderBoard({ deviceId }: { deviceId?: string }) {
  const [params, setParams] = useSearchParams();
  const [mine, setMine] = useState(false);
  const wos = useQuery({
    queryKey: ["work-orders", "board", deviceId, mine],
    queryFn: () => api.get<Paginated<WorkOrder>>("/maintenance/work-orders", { device_id: deviceId, assigned_to_me: mine || undefined, page_size: 200 }),
    refetchInterval: 60_000,
  });
  const { user } = useAuth();
  const open = params.get("wo");
  return (
    <>
      <div className="row between" style={{ marginBottom: 12 }}>
        <label className="checkbox small"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Assigned to me</label>
        <span className="small muted">{wos.data?.total ?? 0} work orders</span>
      </div>
      {wos.isLoading && <Loading />}
      <div className="kanban">
        {COLUMNS.map((col) => {
          const cards = (wos.data?.items ?? []).filter((w) => w.status === col);
          return (
            <div className="kanban-col" key={col}>
              <h3>{humanize(col)} <span className="badge">{cards.length}</span></h3>
              {cards.map((w) => (
                <div key={w.id} className="wo-card" onClick={() => { const next = new URLSearchParams(params); next.set("wo", w.id); setParams(next); }}>
                  <div className="row between small"><span className="muted">#{w.number}</span><Badge value={w.priority} /></div>
                  <div style={{ fontWeight: 600, margin: "4px 0" }}>{w.title}</div>
                  <div className="small muted">{w.device_name}{w.assigned_to_email ? ` · ${w.assigned_to_email}` : ""}</div>
                  <div className="small muted">
                    {col === "RESOLVED" ? `${humanize(w.root_cause)} · ${money(w.total_cost, user?.organization.currency)}` : `opened ${timeAgo(w.created_at)}`}
                  </div>
                </div>
              ))}
            </div>
          );
        })}
      </div>
      {open && <WorkOrderDrawer id={open} onClose={() => { params.delete("wo"); setParams(params); }} />}
    </>
  );
}

export function Schedules({ deviceId }: { deviceId?: string }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const schedules = useQuery({ queryKey: ["schedules", deviceId], queryFn: () => api.get<Schedule[]>("/maintenance/schedules", { device_id: deviceId }) });
  const devices = useQuery({ queryKey: ["devices"], queryFn: () => api.get<Device[]>("/devices") });
  const [f, setF] = useState({ device_id: deviceId ?? "", name: "", interval_km: "", interval_days: "" });
  const create = useMutation({
    mutationFn: () => api.post("/maintenance/schedules", {
      device_id: f.device_id, name: f.name,
      interval_km: f.interval_km ? Number(f.interval_km) : null, interval_days: f.interval_days ? Number(f.interval_days) : null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["schedules"] }); setAdding(false); },
  });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/maintenance/schedules/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });
  const manage = atLeast(user?.role, "MANAGER");
  return (
    <Card
      title="Preventive maintenance schedules"
      sub="A work order opens automatically when a schedule falls due."
      actions={manage && <button className="btn sm" onClick={() => setAdding(true)}><Plus size={14} /> Add schedule</button>}
      flush
    >
      {!schedules.data?.length && <Empty icon={<CalendarClock size={28} />}>No schedules yet.</Empty>}
      {!!schedules.data?.length && (
        <div className="table-wrap">
          <table>
            <thead><tr>{!deviceId && <th>Vehicle</th>}<th>Service</th><th>Interval</th><th>Last done</th><th>Next due</th><th /></tr></thead>
            <tbody>
              {schedules.data.map((s) => (
                <tr key={s.id}>
                  {!deviceId && <td><Link to={`/vehicles/${s.device_id}`}>{s.device_name}</Link></td>}
                  <td><strong>{s.name}</strong></td>
                  <td className="small">{[s.interval_km && `${num(s.interval_km, 0)} km`, s.interval_days && `${s.interval_days} days`].filter(Boolean).join(" or ")}</td>
                  <td className="small">{fmtDate(s.last_service_at)} · {num(s.last_service_km, 0)} km</td>
                  <td>
                    {s.due ? <span className="badge red">Due now</span> : (
                      <span className="small">{[s.km_remaining != null && `${num(s.km_remaining, 0)} km`, s.days_remaining != null && `${num(s.days_remaining, 0)} days`].filter(Boolean).join(" / ")}</span>
                    )}
                  </td>
                  <td className="right">{manage && <button className="btn sm ghost icon" aria-label="Delete" onClick={() => confirm(`Delete schedule "${s.name}"?`) && del.mutate(s.id)}><Trash2 size={14} /></button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && (
        <Modal title="New maintenance schedule" onClose={() => setAdding(false)}>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <div className="form-grid">
              <Field label="Vehicle" full>
                <select required value={f.device_id} disabled={!!deviceId} onChange={(e) => setF({ ...f, device_id: e.target.value })}>
                  <option value="">Select…</option>
                  {devices.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </Field>
              <Field label="Service name" full><input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Engine oil & filter" /></Field>
              <Field label="Every (km)"><input type="number" min={1} value={f.interval_km} onChange={(e) => setF({ ...f, interval_km: e.target.value })} /></Field>
              <Field label="Every (days)"><input type="number" min={1} value={f.interval_days} onChange={(e) => setF({ ...f, interval_days: e.target.value })} /></Field>
            </div>
            <ErrorBox error={create.error} />
            <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn primary" disabled={create.isPending}>Create</button></div>
          </form>
        </Modal>
      )}
    </Card>
  );
}

export default function Maintenance() {
  const { user } = useAuth();
  const [tab, setTab] = useState<"board" | "schedules">("board");
  const [creating, setCreating] = useState(false);
  return (
    <>
      <PageHead
        title="Maintenance"
        sub="Work orders from alerts and schedules. Resolving one records the root cause, cost and downtime."
        actions={canOperate(user?.role) && <button className="btn primary" onClick={() => setCreating(true)}><Plus size={16} /> New work order</button>}
      />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "board", label: "Work orders" }, { id: "schedules", label: "Schedules" }]} />
      {tab === "board" ? <WorkOrderBoard /> : <Schedules />}
      {creating && <NewWorkOrder onClose={() => setCreating(false)} />}
    </>
  );
}
