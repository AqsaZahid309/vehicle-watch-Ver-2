import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Send, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { AuditEntry, Channel, ChannelType, Role, User } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Card, Empty, ErrorBox, Field, Modal, PageHead, Tabs } from "../components/ui";
import { useToast } from "../components/Toasts";
import { fmtDate, humanize } from "../lib/format";

type Tab = "profile" | "organization" | "team" | "channels" | "audit";
const ROLES: Role[] = ["ADMIN", "MANAGER", "TECHNICIAN", "OPERATOR", "VIEWER"];
const EVENTS = ["ALERT", "ESCALATION", "FORECAST", "GEOFENCE", "FUEL", "WORK_ORDER", "MAINTENANCE_DUE", "REPORT"];
const TARGET_HINT: Record<ChannelType, string> = {
  SLACK: "Incoming webhook URL — https://hooks.slack.com/services/…",
  TEAMS: "Teams incoming webhook / workflow URL",
  WEBHOOK: "Any https endpoint — receives a JSON POST",
  EMAIL: "Email address (requires SMTP_* settings on the server)",
  SMS: "E.164 phone number, e.g. +447700900123 (requires TWILIO_* settings)",
};

function Profile() {
  const { user } = useAuth();
  const toast = useToast();
  const [f, setF] = useState({ current_password: "", new_password: "" });
  const change = useMutation({
    mutationFn: () => api.post("/auth/change-password", f),
    onSuccess: () => { setF({ current_password: "", new_password: "" }); toast({ title: "Password changed", kind: "success" }); },
  });
  return (
    <div className="grid grid-2">
      <Card title="Your account">
        <dl className="kv">
          <dt>Email</dt><dd>{user?.email}</dd>
          <dt>Name</dt><dd>{user?.full_name ?? "—"}</dd>
          <dt>Role</dt><dd>{user?.role}</dd>
          <dt>Organization</dt><dd>{user?.organization.name}</dd>
        </dl>
      </Card>
      <Card title="Change password">
        <form className="stack" onSubmit={(e) => { e.preventDefault(); change.mutate(); }}>
          <Field label="Current password"><input type="password" required value={f.current_password} onChange={(e) => setF({ ...f, current_password: e.target.value })} autoComplete="current-password" /></Field>
          <Field label="New password" help="At least 8 characters."><input type="password" minLength={8} required value={f.new_password} onChange={(e) => setF({ ...f, new_password: e.target.value })} autoComplete="new-password" /></Field>
          <ErrorBox error={change.error} />
          <div><button className="btn primary" disabled={change.isPending}>Update password</button></div>
        </form>
      </Card>
    </div>
  );
}

function OrganizationTab() {
  const { user, reload } = useAuth();
  const toast = useToast();
  const org = user!.organization;
  const [f, setF] = useState({ name: org.name, currency: org.currency, fuel_price_per_liter: String(org.fuel_price_per_liter), escalation_minutes: String(org.escalation_minutes) });
  const save = useMutation({
    mutationFn: () => api.patch("/organization", { ...f, fuel_price_per_liter: Number(f.fuel_price_per_liter), escalation_minutes: Number(f.escalation_minutes) }),
    onSuccess: async () => { await reload(); toast({ title: "Organization saved", kind: "success" }); },
  });
  const isAdmin = user?.role === "ADMIN";
  return (
    <Card title="Organization settings">
      <form className="stack" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }} style={{ maxWidth: 560 }}>
        <div className="form-grid">
          <Field label="Name" full><input disabled={!isAdmin} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Currency" help="ISO code, e.g. GBP, USD, EUR"><input disabled={!isAdmin} value={f.currency} maxLength={8} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></Field>
          <Field label="Fuel price per litre"><input disabled={!isAdmin} type="number" step="0.01" min={0} value={f.fuel_price_per_liter} onChange={(e) => setF({ ...f, fuel_price_per_liter: e.target.value })} /></Field>
          <Field label="Escalate unacknowledged CRITICAL alerts after (minutes)" full>
            <input disabled={!isAdmin} type="number" min={1} max={1440} value={f.escalation_minutes} onChange={(e) => setF({ ...f, escalation_minutes: e.target.value })} />
          </Field>
        </div>
        <ErrorBox error={save.error} />
        {isAdmin && <div><button className="btn primary" disabled={save.isPending}>Save</button></div>}
      </form>
    </Card>
  );
}

function Team() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ email: "", full_name: "", password: "", role: "OPERATOR" as Role });
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });
  const create = useMutation({ mutationFn: () => api.post("/users", { ...f, full_name: f.full_name || null }), onSuccess: () => { refresh(); setAdding(false); } });
  const update = useMutation({ mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api.patch(`/users/${id}`, body), onSuccess: refresh });
  const del = useMutation({ mutationFn: (id: string) => api.del(`/users/${id}`), onSuccess: refresh });
  const isAdmin = user?.role === "ADMIN";
  return (
    <Card title="Team" sub="Admins manage users. Roles: Admin › Manager › Technician › Operator › Viewer."
      actions={isAdmin && <button className="btn sm" onClick={() => setAdding(true)}><Plus size={14} /> Add user</button>} flush>
      <ErrorBox error={update.error || del.error} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Joined</th><th /></tr></thead>
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.id}>
                <td><strong>{u.full_name || u.email}</strong><div className="small muted">{u.email}</div></td>
                <td>
                  <select disabled={!isAdmin || u.id === user?.id} value={u.role} onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value } })} style={{ width: "auto" }}>
                    {ROLES.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}
                  </select>
                </td>
                <td>
                  <label className="checkbox small">
                    <input type="checkbox" disabled={!isAdmin || u.id === user?.id} checked={u.is_active} onChange={(e) => update.mutate({ id: u.id, body: { is_active: e.target.checked } })} /> Active
                  </label>
                </td>
                <td className="small muted">{fmtDate(u.created_at)}</td>
                <td className="right">{isAdmin && u.id !== user?.id && <button className="btn sm ghost icon" aria-label="Remove user" onClick={() => confirm(`Remove ${u.email}?`) && del.mutate(u.id)}><Trash2 size={14} /></button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {adding && (
        <Modal title="Add user" onClose={() => setAdding(false)}>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <div className="form-grid">
              <Field label="Email" full><input type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
              <Field label="Name"><input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
              <Field label="Role">
                <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })}>{ROLES.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}</select>
              </Field>
              <Field label="Temporary password" full help="Share it securely; they can change it under Settings › Profile.">
                <input type="text" minLength={8} required value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} autoComplete="off" />
              </Field>
            </div>
            <ErrorBox error={create.error} />
            <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn primary" disabled={create.isPending}>Add user</button></div>
          </form>
        </Modal>
      )}
    </Card>
  );
}

function Channels() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState<Partial<Channel> | null>(null);
  const channels = useQuery({ queryKey: ["channels"], queryFn: () => api.get<Channel[]>("/notifications/channels") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["channels"] });
  const save = useMutation({
    mutationFn: (c: Partial<Channel>) => c.id
      ? api.patch(`/notifications/channels/${c.id}`, { name: c.name, target: c.target, min_severity: c.min_severity, event_types: c.event_types, enabled: c.enabled })
      : api.post("/notifications/channels", c),
    onSuccess: () => { refresh(); setEditing(null); },
  });
  const toggle = useMutation({ mutationFn: (c: Channel) => api.patch(`/notifications/channels/${c.id}`, { enabled: !c.enabled }), onSuccess: refresh });
  const del = useMutation({ mutationFn: (id: string) => api.del(`/notifications/channels/${id}`), onSuccess: refresh });
  const test = useMutation({
    mutationFn: (id: string) => api.post(`/notifications/channels/${id}/test`),
    onSuccess: () => toast({ title: "Test message sent", kind: "success" }),
    onError: (e) => toast({ title: "Test failed", body: (e as Error).message, kind: "error" }),
  });
  const isAdmin = user?.role === "ADMIN";

  useEffect(() => save.reset(), [editing]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Card title="Notification channels" sub="Where alerts, escalations, forecasts and other events are delivered, in addition to the in-app feed."
      actions={isAdmin && <button className="btn sm" onClick={() => setEditing({ channel_type: "SLACK", min_severity: "MEDIUM", event_types: ["ALERT", "ESCALATION", "FORECAST"], enabled: true, name: "", target: "" })}><Plus size={14} /> Add channel</button>} flush>
      {!channels.data?.length && <Empty>No channels. Notifications appear in the in-app bell only.</Empty>}
      {!!channels.data?.length && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Type</th><th>Target</th><th>Min severity</th><th>Events</th><th>Enabled</th><th /></tr></thead>
            <tbody>
              {channels.data.map((c) => (
                <tr key={c.id}>
                  <td><strong>{c.name}</strong></td>
                  <td><span className="badge blue">{c.channel_type}</span></td>
                  <td className="small mono ellipsis" style={{ maxWidth: 240 }}>{c.target}</td>
                  <td>{c.min_severity}</td>
                  <td className="small">{c.event_types.map(humanize).join(", ")}</td>
                  <td><input type="checkbox" disabled={!isAdmin} checked={c.enabled} onChange={() => toggle.mutate(c)} aria-label="Enabled" /></td>
                  <td className="right nowrap">
                    {isAdmin && <>
                      <button className="btn sm" onClick={() => test.mutate(c.id)} disabled={test.isPending}><Send size={13} /> Test</button>{" "}
                      <button className="btn sm" onClick={() => setEditing(c)}>Edit</button>{" "}
                      <button className="btn sm ghost icon" aria-label="Delete channel" onClick={() => confirm(`Delete ${c.name}?`) && del.mutate(c.id)}><Trash2 size={14} /></button>
                    </>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <Modal title={editing.id ? "Edit channel" : "Add channel"} onClose={() => setEditing(null)}>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(editing); }}>
            <div className="form-grid">
              <Field label="Name"><input required value={editing.name ?? ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} placeholder="Ops on-call" /></Field>
              <Field label="Type">
                <select disabled={!!editing.id} value={editing.channel_type} onChange={(e) => setEditing({ ...editing, channel_type: e.target.value as ChannelType })}>
                  {(["SLACK", "TEAMS", "WEBHOOK", "EMAIL", "SMS"] as ChannelType[]).map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </Field>
              <Field label="Target" full help={TARGET_HINT[editing.channel_type as ChannelType]}>
                <input required value={editing.target ?? ""} onChange={(e) => setEditing({ ...editing, target: e.target.value })} />
              </Field>
              <Field label="Minimum severity">
                <select value={editing.min_severity} onChange={(e) => setEditing({ ...editing, min_severity: e.target.value as Channel["min_severity"] })}>
                  <option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="CRITICAL">Critical</option>
                </select>
              </Field>
              <Field label="Events" full>
                <div className="row wrap">
                  {EVENTS.map((ev) => (
                    <label key={ev} className="checkbox small">
                      <input type="checkbox" checked={editing.event_types?.includes(ev) ?? false}
                        onChange={(e) => setEditing({ ...editing, event_types: e.target.checked ? [...(editing.event_types ?? []), ev] : (editing.event_types ?? []).filter((x) => x !== ev) })} />
                      {humanize(ev)}
                    </label>
                  ))}
                </div>
              </Field>
            </div>
            <ErrorBox error={save.error} />
            <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn primary" disabled={save.isPending}>Save</button></div>
          </form>
        </Modal>
      )}
    </Card>
  );
}

function Audit() {
  const logs = useQuery({ queryKey: ["audit"], queryFn: () => api.get<AuditEntry[]>("/audit-logs", { limit: 200 }) });
  return (
    <Card title="Audit log" sub="Security- and maintenance-relevant actions, newest first." flush>
      {!logs.data?.length && <Empty>No audit entries.</Empty>}
      {!!logs.data?.length && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>When</th><th>User</th><th>Action</th><th>Details</th></tr></thead>
            <tbody>
              {logs.data.map((a) => (
                <tr key={a.id}>
                  <td className="small nowrap">{fmtDate(a.created_at)}</td>
                  <td className="small">{a.user_email}</td>
                  <td><code>{a.action}</code></td>
                  <td className="small mono muted ellipsis" style={{ maxWidth: 420 }}>{Object.keys(a.details).length ? JSON.stringify(a.details) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function Settings() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("profile");
  const tabs: { id: Tab; label: string }[] = [
    { id: "profile", label: "Profile" }, { id: "organization", label: "Organization" }, { id: "team", label: "Team" },
    { id: "channels", label: "Notifications" },
    ...(user?.role === "ADMIN" ? [{ id: "audit" as Tab, label: "Audit log" }] : []),
  ];
  return (
    <>
      <PageHead title="Settings" />
      <Tabs value={tab} onChange={setTab} tabs={tabs} />
      {tab === "profile" && <Profile />}
      {tab === "organization" && <OrganizationTab />}
      {tab === "team" && <Team />}
      {tab === "channels" && <Channels />}
      {tab === "audit" && <Audit />}
    </>
  );
}
