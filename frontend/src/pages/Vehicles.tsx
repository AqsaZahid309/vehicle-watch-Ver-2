import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus, Truck } from "lucide-react";
import { api } from "../api/client";
import type { Device, LiveDevice } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Card, Empty, ErrorBox, Field, Health, Loading, Modal, PageHead } from "../components/ui";
import { atLeast, num, timeAgo } from "../lib/format";

export function VehicleForm({ initial, onSubmit, busy, error, submitLabel }: {
  initial?: Partial<Device>; onSubmit: (body: Record<string, unknown>) => void; busy: boolean; error: unknown; submitLabel: string;
}) {
  const [f, setF] = useState({
    name: initial?.name ?? "", device_type: initial?.device_type ?? "truck", license_plate: initial?.license_plate ?? "",
    vin: initial?.vin ?? "", make: initial?.make ?? "", model: initial?.model ?? "", year: initial?.year?.toString() ?? "",
    fuel_tank_liters: initial?.fuel_tank_liters?.toString() ?? "300", odometer_km: initial?.odometer_km?.toString() ?? "0",
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({
      name: f.name, device_type: f.device_type, license_plate: f.license_plate || null, vin: f.vin || null,
      make: f.make || null, model: f.model || null, year: f.year ? Number(f.year) : null,
      fuel_tank_liters: Number(f.fuel_tank_liters) || 300, odometer_km: Number(f.odometer_km) || 0,
    });
  };
  return (
    <form onSubmit={submit} className="stack">
      <div className="form-grid">
        <Field label="Name"><input required value={f.name} onChange={set("name")} placeholder="Truck-07" /></Field>
        <Field label="Type">
          <select value={f.device_type} onChange={set("device_type")}>
            {["truck", "van", "car", "bus", "trailer", "machinery"].map((t) => <option key={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="Licence plate"><input value={f.license_plate} onChange={set("license_plate")} /></Field>
        <Field label="VIN"><input value={f.vin} onChange={set("vin")} maxLength={32} /></Field>
        <Field label="Make"><input value={f.make} onChange={set("make")} /></Field>
        <Field label="Model"><input value={f.model} onChange={set("model")} /></Field>
        <Field label="Year"><input type="number" min={1950} max={2100} value={f.year} onChange={set("year")} /></Field>
        <Field label="Fuel tank (L)"><input type="number" min={1} value={f.fuel_tank_liters} onChange={set("fuel_tank_liters")} /></Field>
        <Field label="Odometer (km)" full><input type="number" min={0} value={f.odometer_km} onChange={set("odometer_km")} /></Field>
      </div>
      <ErrorBox error={error} />
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button className="btn primary" disabled={busy}>{busy ? "Saving…" : submitLabel}</button>
      </div>
    </form>
  );
}

export default function Vehicles() {
  const { user } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const devices = useQuery({ queryKey: ["devices"], queryFn: () => api.get<Device[]>("/devices") });
  const live = useQuery({ queryKey: ["live"], queryFn: () => api.get<LiveDevice[]>("/devices/live"), refetchInterval: 30_000 });
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post<Device>("/devices", body),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["devices"] });
      qc.invalidateQueries({ queryKey: ["live"] });
      setAdding(false);
      nav(`/vehicles/${d.id}?tab=settings`);
    },
  });

  const liveById = new Map((live.data ?? []).map((d) => [d.id, d]));
  return (
    <>
      <PageHead
        title="Vehicles"
        sub="Every vehicle in your fleet, with live status and health."
        actions={atLeast(user?.role, "MANAGER") && <button className="btn primary" onClick={() => setAdding(true)}><Plus size={16} /> Add vehicle</button>}
      />
      <Card flush>
        {devices.isLoading && <Loading />}
        {devices.data && !devices.data.length && <Empty icon={<Truck size={28} />}>No vehicles yet. Add one, then give its telematics unit an API key.</Empty>}
        {!!devices.data?.length && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Vehicle</th><th>Status</th><th>Health</th><th>Driver</th><th className="num">Speed</th><th className="num">Fuel</th><th className="num">Odometer</th><th>Last seen</th></tr>
              </thead>
              <tbody>
                {devices.data.map((d) => {
                  const l = liveById.get(d.id);
                  return (
                    <tr key={d.id} className="clickable" onClick={() => nav(`/vehicles/${d.id}`)}>
                      <td>
                        <strong>{d.name}</strong>
                        <div className="small muted">{[d.make, d.model, d.year].filter(Boolean).join(" ") || d.device_type}{d.license_plate ? ` · ${d.license_plate}` : ""}</div>
                      </td>
                      <td>
                        {!d.is_active ? <span className="badge">Inactive</span> : l?.online ? <span className="badge green">Online</span> : <span className="badge">Offline</span>}
                        {!!l?.open_alerts && <span className="badge red" style={{ marginLeft: 4 }}>{l.open_alerts} alerts</span>}
                      </td>
                      <td>{l ? <Health score={l.health_score} /> : "—"}</td>
                      <td>{l?.driver_name ?? <span className="muted">—</span>}</td>
                      <td className="num">{l?.latest ? `${num(l.latest.speed, 0)} km/h` : "—"}</td>
                      <td className="num">{l?.latest ? `${num(l.latest.fuel_level, 0)}%` : "—"}</td>
                      <td className="num">{num(d.odometer_km, 0)} km</td>
                      <td className="small muted nowrap">{timeAgo(d.last_seen_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {adding && (
        <Modal title="Add vehicle" onClose={() => setAdding(false)}>
          <VehicleForm onSubmit={(b) => create.mutate(b)} busy={create.isPending} error={create.error} submitLabel="Add vehicle" />
        </Modal>
      )}
    </>
  );
}
