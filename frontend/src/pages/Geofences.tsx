import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Circle } from "react-leaflet";
import { MapPinned, Plus, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { Geofence, GeofenceEvent, GeofenceKind, LiveDevice } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import FleetMap, { GeofenceLayer, VehicleLayer } from "../components/FleetMap";
import { Badge, Card, Empty, ErrorBox, Field, PageHead } from "../components/ui";
import { atLeast, fmtDate, num } from "../lib/format";

const blank = { name: "", kind: "CUSTOMER" as GeofenceKind, radius_m: "400", alert_on_enter: false, alert_on_exit: false, speed_limit_kmh: "" };

export default function Geofences() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const manage = atLeast(user?.role, "MANAGER");
  const [draft, setDraft] = useState<typeof blank | null>(null);
  const [center, setCenter] = useState<[number, number] | null>(null);
  const fences = useQuery({ queryKey: ["geofences"], queryFn: () => api.get<Geofence[]>("/geofences") });
  const events = useQuery({ queryKey: ["geofence-events"], queryFn: () => api.get<GeofenceEvent[]>("/geofences/events", { limit: 50 }), refetchInterval: 30_000 });
  const live = useQuery({ queryKey: ["live"], queryFn: () => api.get<LiveDevice[]>("/devices/live") });

  const create = useMutation({
    mutationFn: () => api.post("/geofences", {
      name: draft!.name, kind: draft!.kind, shape: "CIRCLE", center_lat: center![0], center_lon: center![1],
      radius_m: Number(draft!.radius_m), alert_on_enter: draft!.alert_on_enter, alert_on_exit: draft!.alert_on_exit,
      speed_limit_kmh: draft!.speed_limit_kmh ? Number(draft!.speed_limit_kmh) : null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["geofences"] }); setDraft(null); setCenter(null); },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api.patch(`/geofences/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["geofences"] }),
  });
  const del = useMutation({ mutationFn: (id: string) => api.del(`/geofences/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["geofences"] }) });

  const list = fences.data ?? [];
  const fit = list.filter((g) => g.center_lat != null).map((g) => [g.center_lat!, g.center_lon!] as [number, number]);

  return (
    <>
      <PageHead title="Geofences" sub="Depots, customer sites and restricted zones. Entering, leaving and speeding inside a zone are detected automatically."
        actions={manage && !draft && <button className="btn primary" onClick={() => setDraft({ ...blank })}><Plus size={16} /> New geofence</button>} />
      <div className="grid grid-3" style={{ marginBottom: 16 }}>
        <div className="card span-2">
          {draft && <div className="info-box" style={{ borderRadius: "var(--radius) var(--radius) 0 0" }}>{center ? "Centre set. Click again to move it." : "Click the map to place the geofence centre."}</div>}
          <FleetMap className="map" fit={fit} onMapClick={draft ? (lat, lon) => setCenter([lat, lon]) : undefined}>
            <GeofenceLayer fences={list} />
            <VehicleLayer devices={live.data ?? []} />
            {draft && center && <Circle center={center} radius={Number(draft.radius_m) || 100} pathOptions={{ color: "#f97316", dashArray: "6 4" }} />}
          </FleetMap>
        </div>
        {draft ? (
          <Card title="New geofence">
            <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
              <Field label="Name"><input required value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
              <Field label="Type">
                <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as GeofenceKind })}>
                  <option value="DEPOT">Depot</option><option value="CUSTOMER">Customer site</option><option value="SERVICE">Service centre</option><option value="RESTRICTED">Restricted zone</option>
                </select>
              </Field>
              <Field label="Radius (m)"><input type="number" min={10} value={draft.radius_m} onChange={(e) => setDraft({ ...draft, radius_m: e.target.value })} /></Field>
              <Field label="Speed limit inside (km/h)" help="Optional. A SPEEDING event fires once per episode."><input type="number" min={1} value={draft.speed_limit_kmh} onChange={(e) => setDraft({ ...draft, speed_limit_kmh: e.target.value })} /></Field>
              <label className="checkbox"><input type="checkbox" checked={draft.alert_on_enter} onChange={(e) => setDraft({ ...draft, alert_on_enter: e.target.checked })} /> Notify on enter</label>
              <label className="checkbox"><input type="checkbox" checked={draft.alert_on_exit} onChange={(e) => setDraft({ ...draft, alert_on_exit: e.target.checked })} /> Notify on exit</label>
              {draft.kind === "RESTRICTED" && <div className="small muted">Entering a restricted zone always raises a CRITICAL notification.</div>}
              <ErrorBox error={create.error} />
              <div className="row">
                <button className="btn primary" disabled={!center || create.isPending}>Create</button>
                <button type="button" className="btn" onClick={() => { setDraft(null); setCenter(null); }}>Cancel</button>
              </div>
            </form>
          </Card>
        ) : (
          <Card title="Recent events" flush>
            {!events.data?.length && <Empty>No geofence events yet.</Empty>}
            <div style={{ maxHeight: 420, overflowY: "auto" }}>
              {events.data?.map((e) => (
                <div key={e.id} style={{ padding: "9px 14px", borderBottom: "1px solid var(--border)" }}>
                  <div className="row between"><strong>{e.device_name}</strong><Badge value={e.event} /></div>
                  <div className="small muted">{e.geofence_name} · {num(e.speed, 0)} km/h · {fmtDate(e.occurred_at)}</div>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
      <Card title="Zones" flush>
        {!list.length && <Empty icon={<MapPinned size={28} />}>No geofences yet.</Empty>}
        {!!list.length && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Type</th><th className="num">Radius</th><th className="num">Speed limit</th><th>Notify</th><th className="num">Vehicles inside</th><th /></tr></thead>
              <tbody>
                {list.map((g) => (
                  <tr key={g.id}>
                    <td><strong>{g.name}</strong></td>
                    <td><Badge value={g.kind} /></td>
                    <td className="num">{g.radius_m ? `${num(g.radius_m, 0)} m` : "polygon"}</td>
                    <td className="num">{g.speed_limit_kmh ? `${g.speed_limit_kmh} km/h` : "—"}</td>
                    <td>
                      <label className="checkbox small"><input type="checkbox" disabled={!manage} checked={g.alert_on_enter} onChange={(e) => update.mutate({ id: g.id, body: { alert_on_enter: e.target.checked } })} /> enter</label>{" "}
                      <label className="checkbox small"><input type="checkbox" disabled={!manage} checked={g.alert_on_exit} onChange={(e) => update.mutate({ id: g.id, body: { alert_on_exit: e.target.checked } })} /> exit</label>
                    </td>
                    <td className="num">{g.vehicles_inside}</td>
                    <td className="right">{manage && <button className="btn sm ghost icon" aria-label="Delete geofence" onClick={() => confirm(`Delete ${g.name}?`) && del.mutate(g.id)}><Trash2 size={14} /></button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
