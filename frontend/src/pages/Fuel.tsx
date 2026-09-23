import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Download, Fuel as FuelIcon } from "lucide-react";
import { api } from "../api/client";
import type { FuelEvent, FuelStat } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { C, HBar } from "../components/charts";
import { Badge, Card, Empty, PageHead, Stat } from "../components/ui";
import { canOperate, fmtDate, money, num } from "../lib/format";

export default function Fuel() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const cur = user?.organization.currency ?? "USD";
  const [days, setDays] = useState(30);
  const [onlyTheft, setOnlyTheft] = useState(false);
  const stats = useQuery({ queryKey: ["fuel-stats", days], queryFn: () => api.get<FuelStat[]>("/fuel/stats", { days }) });
  const events = useQuery({
    queryKey: ["fuel-events", onlyTheft],
    queryFn: () => api.get<FuelEvent[]>("/fuel/events", { event: onlyTheft ? "THEFT_SUSPECTED" : undefined, limit: 100 }),
    refetchInterval: 60_000,
  });
  const review = useMutation({ mutationFn: (id: string) => api.post(`/fuel/events/${id}/review`), onSuccess: () => qc.invalidateQueries({ queryKey: ["fuel-events"] }) });

  const rows = stats.data ?? [];
  const totalL = rows.reduce((s, r) => s + r.fuel_used_liters, 0);
  const totalKm = rows.reduce((s, r) => s + r.distance_km, 0);
  const totalCost = rows.reduce((s, r) => s + r.fuel_cost, 0);
  const thefts = rows.reduce((s, r) => s + r.theft_suspected, 0);
  const eff = rows.filter((r) => r.l_per_100km != null).map((r) => ({ name: r.device_name, l: r.l_per_100km }));

  return (
    <>
      <PageHead title="Fuel" sub="Consumption, cost per km, refuels and suspected theft (sharp drops while parked)."
        actions={<>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ width: "auto" }}>
            {[7, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}
          </select>
          <button className="btn" onClick={() => api.download("/reports/export/fuel_events", {}, "fuel-events.csv")}><Download size={16} /> Events CSV</button>
        </>} />
      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat label="Fuel used" value={`${num(totalL, 0)} L`} />
        <Stat label="Fuel cost" value={money(totalCost, cur)} hint={`at ${user?.organization.fuel_price_per_liter}/L`} />
        <Stat label="Fleet economy" value={totalKm > 1 ? `${num((100 * totalL) / totalKm)} L/100 km` : "—"} hint={`${num(totalKm, 0)} km driven`} />
        <Stat label="Suspected thefts" value={thefts} color={thefts ? "var(--red)" : undefined} />
      </div>
      <div className="grid grid-3" style={{ marginBottom: 16 }}>
        <Card className="span-2" title="By vehicle" flush>
          {!rows.length && <Empty icon={<FuelIcon size={28} />}>No fuel data yet.</Empty>}
          {!!rows.length && (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Vehicle</th><th className="num">Distance</th><th className="num">Fuel</th><th className="num">L/100 km</th><th className="num">Cost</th><th className="num">Cost/km</th><th className="num">Refuels</th><th className="num">Thefts</th><th className="num">Tank now</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.device_id}>
                      <td><Link to={`/vehicles/${r.device_id}`}><strong>{r.device_name}</strong></Link></td>
                      <td className="num">{num(r.distance_km, 0)} km</td>
                      <td className="num">{num(r.fuel_used_liters)} L</td>
                      <td className="num">{num(r.l_per_100km)}</td>
                      <td className="num">{money(r.fuel_cost, cur)}</td>
                      <td className="num">{r.cost_per_km != null ? r.cost_per_km.toFixed(2) : "—"}</td>
                      <td className="num">{r.refuels}</td>
                      <td className="num">{r.theft_suspected ? <span className="badge red">{r.theft_suspected}</span> : 0}</td>
                      <td className="num">{r.current_level != null ? `${num(r.current_level, 0)}%` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card title="Economy (L/100 km)">
          {eff.length ? <HBar data={eff} dataKey="l" nameKey="name" color={C.orange} unit="L/100 km" /> : <Empty>Needs at least 1 km of driving.</Empty>}
        </Card>
      </div>
      <Card title="Fuel events" flush actions={<label className="checkbox small"><input type="checkbox" checked={onlyTheft} onChange={(e) => setOnlyTheft(e.target.checked)} /> Suspected theft only</label>}>
        {!events.data?.length && <Empty>No refuels or suspicious drops recorded.</Empty>}
        {!!events.data?.length && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>When</th><th>Vehicle</th><th>Event</th><th className="num">Level</th><th className="num">Litres</th><th>Location</th><th /></tr></thead>
              <tbody>
                {events.data.map((e) => (
                  <tr key={e.id}>
                    <td className="nowrap small">{fmtDate(e.occurred_at)}</td>
                    <td>{e.device_name}</td>
                    <td><Badge value={e.event} /></td>
                    <td className="num">{num(e.level_before, 0)}% → {num(e.level_after, 0)}%</td>
                    <td className="num">{num(e.liters, 0)}</td>
                    <td className="small"><a href={`https://www.openstreetmap.org/?mlat=${e.lat}&mlon=${e.lon}#map=16/${e.lat}/${e.lon}`} target="_blank" rel="noreferrer">{e.lat.toFixed(4)}, {e.lon.toFixed(4)}</a></td>
                    <td className="right">
                      {e.event === "THEFT_SUSPECTED" && (e.reviewed ? <span className="badge green">Reviewed</span> :
                        canOperate(user?.role) && <button className="btn sm" onClick={() => review.mutate(e.id)}>Mark reviewed</button>)}
                    </td>
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
