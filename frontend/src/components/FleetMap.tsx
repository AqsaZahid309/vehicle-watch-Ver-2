import { useEffect, type ReactNode } from "react";
import { Circle, CircleMarker, MapContainer, Polygon, Polyline, Popup, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import type { LatLngExpression } from "leaflet";
import { Link } from "react-router-dom";
import type { Geofence, LiveDevice, TripPoint } from "../api/types";
import { healthColor, num, timeAgo } from "../lib/format";

const KIND_COLOR: Record<string, string> = { DEPOT: "#2563eb", CUSTOMER: "#16a34a", SERVICE: "#7c3aed", RESTRICTED: "#dc2626" };
const DEFAULT_CENTER: [number, number] = [51.5074, -0.1278];

function Tiles() {
  // Standard OpenStreetMap tiles (no API key). Dark mode inverts them in CSS.
  return (
    <TileLayer
      attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      maxZoom={19}
    />
  );
}

function FitTo({ points, once = true }: { points: [number, number][]; once?: boolean }) {
  const map = useMap();
  const key = once ? points.length > 0 : JSON.stringify(points);
  useEffect(() => {
    if (!points.length) return;
    if (points.length === 1) map.setView(points[0], 13);
    else map.fitBounds(points, { padding: [40, 40], maxZoom: 14 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}

function ClickCapture({ onClick }: { onClick: (lat: number, lon: number) => void }) {
  useMapEvents({ click: (e) => onClick(e.latlng.lat, e.latlng.lng) });
  return null;
}

export function GeofenceLayer({ fences }: { fences: Geofence[] }) {
  return (
    <>
      {fences.map((g) => {
        const color = KIND_COLOR[g.kind] ?? "#64748b";
        const tip = (
          <Tooltip>
            <strong>{g.name}</strong> · {g.kind.toLowerCase()}
            {g.speed_limit_kmh ? ` · limit ${g.speed_limit_kmh} km/h` : ""}
          </Tooltip>
        );
        if (g.shape === "POLYGON" && g.polygon)
          return <Polygon key={g.id} positions={g.polygon as LatLngExpression[]} pathOptions={{ color, weight: 1.5, fillOpacity: 0.12 }}>{tip}</Polygon>;
        if (g.center_lat == null || g.center_lon == null || !g.radius_m) return null;
        return (
          <Circle key={g.id} center={[g.center_lat, g.center_lon]} radius={g.radius_m} pathOptions={{ color, weight: 1.5, fillOpacity: 0.12, dashArray: g.kind === "RESTRICTED" ? "4 4" : undefined }}>
            {tip}
          </Circle>
        );
      })}
    </>
  );
}

export function VehicleLayer({ devices, selected }: { devices: LiveDevice[]; selected?: string | null }) {
  return (
    <>
      {devices.filter((d) => d.latest).map((d) => (
        <CircleMarker
          key={d.id}
          center={[d.latest!.gps_lat, d.latest!.gps_lon]}
          radius={selected === d.id ? 11 : 8}
          pathOptions={{
            color: "#fff", weight: 2, fillOpacity: d.online ? 1 : 0.45,
            fillColor: d.online ? healthColor(d.health_score) : "#94a3b8",
          }}
        >
          <Tooltip direction="top" offset={[0, -8]}>{d.name} · {num(d.latest!.speed, 0)} km/h</Tooltip>
          <Popup>
            <div style={{ minWidth: 180 }}>
              <strong>{d.name}</strong> {d.license_plate && <span className="muted">({d.license_plate})</span>}
              <div className="small">Driver: {d.driver_name ?? "—"}</div>
              <div className="small">Speed {num(d.latest!.speed, 0)} km/h · Fuel {num(d.latest!.fuel_level, 0)}%</div>
              <div className="small">Engine {num(d.latest!.engine_temp)} °C · Battery {num(d.latest!.battery_voltage, 2)} V</div>
              <div className="small muted">Health {d.health_score} · {d.open_alerts} open alerts · {timeAgo(d.last_seen_at)}</div>
              <Link to={`/vehicles/${d.id}`}>Open vehicle →</Link>
            </div>
          </Popup>
        </CircleMarker>
      ))}
    </>
  );
}

export function RouteLayer({ points }: { points: TripPoint[] }) {
  if (points.length < 2) return null;
  const pos = points.map((p) => [p.lat, p.lon]) as [number, number][];
  return (
    <>
      <Polyline positions={pos} pathOptions={{ color: "#2563eb", weight: 4, opacity: 0.8 }} />
      <CircleMarker center={pos[0]} radius={6} pathOptions={{ color: "#fff", fillColor: "#16a34a", fillOpacity: 1 }}><Tooltip>Start</Tooltip></CircleMarker>
      <CircleMarker center={pos[pos.length - 1]} radius={6} pathOptions={{ color: "#fff", fillColor: "#dc2626", fillOpacity: 1 }}><Tooltip>End</Tooltip></CircleMarker>
    </>
  );
}

export default function FleetMap({ className = "map", fit = [], fitOnce = true, onMapClick, children }: {
  className?: string; fit?: [number, number][]; fitOnce?: boolean; onMapClick?: (lat: number, lon: number) => void; children?: ReactNode;
}) {
  return (
    <MapContainer className={className} center={fit[0] ?? DEFAULT_CENTER} zoom={11} scrollWheelZoom>
      <Tiles />
      <FitTo points={fit} once={fitOnce} />
      {onMapClick && <ClickCapture onClick={onMapClick} />}
      {children}
    </MapContainer>
  );
}
