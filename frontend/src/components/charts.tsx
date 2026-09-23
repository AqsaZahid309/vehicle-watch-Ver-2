import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

// Hex colours (not CSS variables) — SVG presentation attributes need concrete values.
export const C = {
  blue: "#3b82f6", orange: "#f97316", red: "#ef4444", green: "#22c55e", purple: "#8b5cf6", yellow: "#eab308",
  grid: "rgba(148,163,184,0.25)", axis: "#94a3b8",
};

const tooltipStyle = {
  contentStyle: { background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "var(--text-2)" },
};
const axis = { stroke: C.axis, fontSize: 11, tickLine: false, axisLine: false } as const;

export function SeverityTimeline({ data }: { data: { date: string; LOW: number; MEDIUM: number; CRITICAL: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ left: -20, right: 8, top: 8 }}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="date" {...axis} tickFormatter={(d) => d.slice(5)} />
        <YAxis {...axis} allowDecimals={false} />
        <Tooltip {...tooltipStyle} cursor={{ fill: "rgba(148,163,184,0.1)" }} />
        <Legend iconSize={8} wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="CRITICAL" stackId="a" fill={C.red} />
        <Bar dataKey="MEDIUM" stackId="a" fill={C.orange} />
        <Bar dataKey="LOW" stackId="a" fill={C.yellow} radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function MetricLine({ data, dataKey, color = C.blue, unit = "", threshold, height = 160 }: {
  data: Record<string, any>[]; dataKey: string; color?: string; unit?: string; threshold?: number; height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ left: -18, right: 8, top: 8 }}>
        <defs>
          <linearGradient id={`g-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="t" {...axis} tickFormatter={(t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} minTickGap={40} />
        <YAxis {...axis} domain={["auto", "auto"]} width={50} />
        <Tooltip {...tooltipStyle} labelFormatter={(t) => new Date(String(t)).toLocaleTimeString()} formatter={(v: any) => [`${Number(v).toFixed(2)} ${unit}`, dataKey]} />
        {threshold !== undefined && <ReferenceLine y={threshold} stroke={C.red} strokeDasharray="4 4" />}
        <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2} fill={`url(#g-${dataKey})`} isAnimationActive={false} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function ForecastChart({ history, threshold, slope, current, hoursTo, unit }: {
  history: { h: number; v: number }[]; threshold: number; slope: number; current: number; hoursTo: number | null; unit: string;
}) {
  const horizon = hoursTo && hoursTo > 0 ? Math.min(hoursTo * 1.15, 72) : 3;
  const data = [
    ...history.map((p) => ({ h: p.h, actual: p.v })),
    { h: 0, trend: current },
    { h: +horizon.toFixed(2), trend: current + slope * horizon },
  ];
  return (
    <ResponsiveContainer width="100%" height={180}>
      <LineChart data={data} margin={{ left: -12, right: 12, top: 8 }}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="h" type="number" domain={["dataMin", "dataMax"]} {...axis} tickFormatter={(h) => (h === 0 ? "now" : `${h > 0 ? "+" : ""}${Math.round(h)}h`)} />
        <YAxis {...axis} domain={["auto", "auto"]} width={46} />
        <Tooltip {...tooltipStyle} labelFormatter={(h) => `${Number(h).toFixed(1)} h`} formatter={(v: any, n: any) => [`${Number(v).toFixed(2)} ${unit}`, n]} />
        <ReferenceLine y={threshold} stroke={C.red} strokeDasharray="4 4" label={{ value: `limit ${threshold}`, fill: C.red, fontSize: 11, position: "insideTopRight" }} />
        <ReferenceLine x={0} stroke={C.axis} />
        <Line type="monotone" dataKey="actual" stroke={C.blue} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
        <Line type="linear" dataKey="trend" stroke={C.orange} strokeWidth={2} strokeDasharray="6 4" dot={false} isAnimationActive={false} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function HBar({ data, dataKey, nameKey, color = C.blue, height = 220, unit = "" }: {
  data: Record<string, any>[]; dataKey: string; nameKey: string; color?: string; height?: number; unit?: string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
        <CartesianGrid stroke={C.grid} horizontal={false} />
        <XAxis type="number" {...axis} />
        <YAxis type="category" dataKey={nameKey} {...axis} width={120} />
        <Tooltip {...tooltipStyle} cursor={{ fill: "rgba(148,163,184,0.1)" }} formatter={(v: any) => [`${v} ${unit}`, dataKey]} />
        <Bar dataKey={dataKey} fill={color} radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
