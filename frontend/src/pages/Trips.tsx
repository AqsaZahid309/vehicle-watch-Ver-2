import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Trash2, Trophy } from "lucide-react";
import { api } from "../api/client";
import type { Driver, DriverScore, Paginated, Trip, TripPoint } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { MetricLine } from "../components/charts";
import FleetMap, { RouteLayer } from "../components/FleetMap";
import { Card, Drawer, Empty, ErrorBox, Field, Loading, Modal, PageHead, Pager, Tabs } from "../components/ui";
import { atLeast, duration, fmtDate, healthColor, num } from "../lib/format";

function Score({ value }: { value: number }) {
  return <strong style={{ color: healthColor(value) }}>{num(value, 0)}</strong>;
}

export function TripDrawer({ trip, onClose }: { trip: Trip; onClose: () => void }) {
  const route = useQuery({ queryKey: ["trip-route", trip.id], queryFn: () => api.get<TripPoint[]>(`/trips/${trip.id}/route`) });
  const pts = route.data ?? [];
  return (
    <Drawer title={`Trip · ${trip.device_name}`} onClose={onClose}>
      <div className="stack">
        <div className="small muted">{fmtDate(trip.started_at)} → {fmtDate(trip.ended_at)}{trip.is_open && " (in progress)"}</div>
        <div className="card" style={{ overflow: "hidden" }}>
          {route.isLoading ? <Loading /> : (
            <FleetMap className="map" fit={pts.map((p) => [p.lat, p.lon] as [number, number])}><RouteLayer points={pts} /></FleetMap>
          )}
        </div>
        <div className="grid grid-3">
          <div className="sensor"><div className="s-label">Distance</div><div className="s-value">{num(trip.distance_km)} km</div></div>
          <div className="sensor"><div className="s-label">Duration</div><div className="s-value">{duration(trip.duration_seconds)}</div></div>
          <div className="sensor"><div className="s-label">Score</div><div className="s-value"><Score value={trip.score} /></div></div>
          <div className="sensor"><div className="s-label">Avg / max speed</div><div className="s-value">{num(trip.avg_speed, 0)} / {num(trip.max_speed, 0)}</div></div>
          <div className="sensor"><div className="s-label">Harsh accel / brake</div><div className="s-value">{trip.harsh_accel_count} / {trip.harsh_brake_count}</div></div>
          <div className="sensor"><div className="s-label">Fuel used</div><div className="s-value">{num(trip.fuel_used_liters)} L</div></div>
          <div className="sensor"><div className="s-label">Idle</div><div className="s-value">{duration(trip.idle_seconds)}</div></div>
          <div className="sensor"><div className="s-label">Speeding</div><div className="s-value">{duration(trip.overspeed_seconds)}</div></div>
          <div className="sensor"><div className="s-label">Over-rev readings</div><div className="s-value">{trip.over_rev_count}</div></div>
        </div>
        {pts.length > 1 && (
          <Card title="Speed profile"><MetricLine data={pts.map((p) => ({ t: p.t, speed: p.speed }))} dataKey="speed" unit="km/h" threshold={100} /></Card>
        )}
        <div className="small muted">Driver: {trip.driver_name ?? "unassigned"}</div>
      </div>
    </Drawer>
  );
}

export function TripsTable({ deviceId }: { deviceId?: string }) {
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<Trip | null>(null);
  const trips = useQuery({
    queryKey: ["trips", deviceId, page],
    queryFn: () => api.get<Paginated<Trip>>("/trips", { device_id: deviceId, page, page_size: 25 }),
    refetchInterval: 60_000,
  });
  return (
    <Card flush title="Trips" sub="Split on gaps of more than 5 minutes between readings."
      actions={<button className="btn sm" onClick={() => api.download("/reports/export/trips", { device_id: deviceId }, "trips.csv")}><Download size={14} /> CSV</button>}>
      {trips.isLoading && <Loading />}
      {trips.data && !trips.data.items.length && <Empty>No trips recorded yet. Trips appear after the worker processes telemetry.</Empty>}
      {!!trips.data?.items.length && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Started</th>{!deviceId && <th>Vehicle</th>}<th>Driver</th><th className="num">Distance</th><th className="num">Duration</th><th className="num">Avg speed</th><th className="num">Harsh events</th><th className="num">Fuel</th><th className="num">Score</th></tr></thead>
            <tbody>
              {trips.data.items.map((t) => (
                <tr key={t.id} className="clickable" onClick={() => setOpen(t)}>
                  <td className="nowrap">{fmtDate(t.started_at)} {t.is_open && <span className="badge green">Live</span>}</td>
                  {!deviceId && <td>{t.device_name}</td>}
                  <td>{t.driver_name ?? <span className="muted">—</span>}</td>
                  <td className="num">{num(t.distance_km)} km</td>
                  <td className="num">{duration(t.duration_seconds)}</td>
                  <td className="num">{num(t.avg_speed, 0)} km/h</td>
                  <td className="num">{t.harsh_accel_count + t.harsh_brake_count}</td>
                  <td className="num">{num(t.fuel_used_liters)} L</td>
                  <td className="num"><Score value={t.score} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pager page={page} pages={trips.data?.pages ?? 1} onPage={setPage} />
      {open && <TripDrawer trip={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}

function Scorecards() {
  const [days, setDays] = useState(30);
  const scores = useQuery({ queryKey: ["driver-scores", days], queryFn: () => api.get<DriverScore[]>("/drivers/scores", { days }) });
  return (
    <Card flush title="Driver scorecards" sub="100 = smooth, legal, efficient driving. Penalties are rate-based, so long trips aren't punished for length."
      actions={<select value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ width: "auto" }}>
        {[7, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}
      </select>}>
      {!scores.data?.length && <Empty icon={<Trophy size={28} />}>No driving recorded in this period.</Empty>}
      {!!scores.data?.length && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>#</th><th>Driver</th><th className="num">Score</th><th className="num">Trips</th><th className="num">Distance</th><th className="num">Hours</th><th className="num">Harsh accel /100 km</th><th className="num">Harsh brake /100 km</th><th className="num">Speeding</th><th className="num">Idle</th><th className="num">L/100 km</th></tr></thead>
            <tbody>
              {scores.data.map((s, i) => (
                <tr key={s.driver_id ?? "none"}>
                  <td>{i + 1}</td>
                  <td><strong>{s.driver_name}</strong></td>
                  <td className="num"><Score value={s.score} /></td>
                  <td className="num">{s.trips}</td>
                  <td className="num">{num(s.distance_km, 0)} km</td>
                  <td className="num">{num(s.driving_hours)}</td>
                  <td className="num">{num(s.harsh_accel_per_100km)}</td>
                  <td className="num">{num(s.harsh_brake_per_100km)}</td>
                  <td className="num">{num(s.overspeed_pct)}%</td>
                  <td className="num">{num(s.idle_pct)}%</td>
                  <td className="num">{num(s.fuel_l_per_100km)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function DriversAdmin() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ name: "", email: "", phone: "", license_number: "" });
  const drivers = useQuery({ queryKey: ["drivers"], queryFn: () => api.get<Driver[]>("/drivers") });
  const create = useMutation({
    mutationFn: () => api.post("/drivers", { name: f.name, email: f.email || null, phone: f.phone || null, license_number: f.license_number || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["drivers"] }); setAdding(false); setF({ name: "", email: "", phone: "", license_number: "" }); },
  });
  const del = useMutation({ mutationFn: (id: string) => api.del(`/drivers/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["drivers"] }) });
  const manage = atLeast(user?.role, "MANAGER");
  return (
    <Card flush title="Drivers" sub="Assign a driver to a vehicle from the vehicle's Settings tab."
      actions={manage && <button className="btn sm" onClick={() => setAdding(true)}><Plus size={14} /> Add driver</button>}>
      {!drivers.data?.length && <Empty>No drivers yet.</Empty>}
      {!!drivers.data?.length && (
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Phone</th><th>Licence</th><th /></tr></thead>
          <tbody>
            {drivers.data.map((d) => (
              <tr key={d.id}>
                <td><strong>{d.name}</strong></td><td>{d.email ?? "—"}</td><td>{d.phone ?? "—"}</td><td>{d.license_number ?? "—"}</td>
                <td className="right">{manage && <button className="btn sm ghost icon" aria-label="Delete driver" onClick={() => confirm(`Remove ${d.name}?`) && del.mutate(d.id)}><Trash2 size={14} /></button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {adding && (
        <Modal title="Add driver" onClose={() => setAdding(false)}>
          <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <div className="form-grid">
              <Field label="Name" full><input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
              <Field label="Email"><input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
              <Field label="Phone"><input value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
              <Field label="Licence number" full><input value={f.license_number} onChange={(e) => setF({ ...f, license_number: e.target.value })} /></Field>
            </div>
            <ErrorBox error={create.error} />
            <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn primary" disabled={create.isPending}>Add</button></div>
          </form>
        </Modal>
      )}
    </Card>
  );
}

export default function Trips() {
  const [tab, setTab] = useState<"scores" | "trips" | "drivers">("scores");
  return (
    <>
      <PageHead title="Trips & drivers" sub="Trip history, driver behaviour and scorecards." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "scores", label: "Scorecards" }, { id: "trips", label: "Trips" }, { id: "drivers", label: "Drivers" }]} />
      {tab === "scores" && <Scorecards />}
      {tab === "trips" && <TripsTable />}
      {tab === "drivers" && <DriversAdmin />}
    </>
  );
}
