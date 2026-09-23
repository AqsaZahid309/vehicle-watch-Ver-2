import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Geofence, LiveDevice } from "../api/types";
import FleetMap, { GeofenceLayer, VehicleLayer } from "../components/FleetMap";
import { Card, Health, PageHead } from "../components/ui";
import { num, timeAgo } from "../lib/format";

export default function LiveMap() {
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const live = useQuery({ queryKey: ["live"], queryFn: () => api.get<LiveDevice[]>("/devices/live"), refetchInterval: 30_000 });
  const fences = useQuery({ queryKey: ["geofences"], queryFn: () => api.get<Geofence[]>("/geofences") });

  const devices = useMemo(
    () => (live.data ?? []).filter((d) => d.name.toLowerCase().includes(filter.toLowerCase())),
    [live.data, filter],
  );
  const sel = devices.find((d) => d.id === selected);
  const fit = sel?.latest
    ? [[sel.latest.gps_lat, sel.latest.gps_lon] as [number, number]]
    : devices.filter((d) => d.latest).map((d) => [d.latest!.gps_lat, d.latest!.gps_lon] as [number, number]);

  return (
    <>
      <PageHead title="Live map" sub="Positions update in real time as telemetry arrives." />
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) 300px" }}>
        <div className="card">
          <FleetMap className="map tall" fit={fit} fitOnce={!selected}>
            <GeofenceLayer fences={fences.data ?? []} />
            <VehicleLayer devices={devices} selected={selected} />
          </FleetMap>
        </div>
        <Card title={`Vehicles (${devices.length})`} flush>
          <div style={{ padding: 12 }}>
            <input placeholder="Filter vehicles…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
          <div style={{ maxHeight: "calc(100vh - 290px)", overflowY: "auto" }}>
            {devices.map((d) => (
              <div
                key={d.id}
                onClick={() => setSelected(d.id === selected ? null : d.id)}
                style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", cursor: "pointer", background: d.id === selected ? "var(--primary-soft)" : undefined }}
              >
                <div className="row between">
                  <strong><span className={`dot ${d.online ? "green" : ""}`} /> {d.name}</strong>
                  <Health score={d.health_score} />
                </div>
                <div className="small muted">
                  {d.latest ? `${num(d.latest.speed, 0)} km/h · fuel ${num(d.latest.fuel_level, 0)}%` : "No position yet"} · {timeAgo(d.last_seen_at)}
                </div>
                {d.driver_name && <div className="small muted">Driver: {d.driver_name}</div>}
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
