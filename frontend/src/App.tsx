import { useEffect, useState } from "react";
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  ReferenceLine,
  ReferenceArea,
} from "recharts";
import { Sun, Home, Zap, Battery } from "lucide-react";
import api from "./api";

interface PowerLog {
  id: string;
  created: string;
  pv_power: number;
  load_power: number;
  grid_power: number;
  battery_power: number;
  battery_soc: number;
}

interface BatteryStatus {
  success: boolean;
  id_check: number;
  wchamax_watt: number;
  charge_limit_pct: number;
  discharge_limit_pct: number;
  charge_limit_watt: number;
  discharge_limit_watt: number;
  reserve_pct: number;
  control_mode: number;
}

function formatPower(watts: number): { value: string; unit: string } {
  if (Math.abs(watts) >= 1000) {
    return { value: (watts / 1000).toFixed(1), unit: "kW" };
  }
  return { value: Math.round(watts).toString(), unit: "W" };
}

function formatPowerString(watts: number): string {
  const { value, unit } = formatPower(watts);
  return `${value} ${unit}`;
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString("de-AT", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function yAxisTickFormatter(val: number): string {
  if (Math.abs(val) >= 1000) {
    return `${(val / 1000).toFixed(1)} kW`;
  }
  return `${val} W`;
}

function StatCard({
  icon,
  label,
  value,
  unit,
  color,
  extra,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit: string;
  color: string;
  extra?: string;
}) {
  return (
    <div className="rounded-2xl bg-slate-800 p-5 flex flex-col gap-2">
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <span style={{ color }}>{icon}</span>
        {label}
      </div>
      <div className="text-2xl font-semibold text-white">
        {value}{" "}
        <span className="text-sm font-normal text-slate-400">{unit}</span>
      </div>
      {extra && (
        <div className="text-sm text-slate-400">{extra}</div>
      )}
    </div>
  );
}

export default function App() {
  const [history, setHistory] = useState<PowerLog[]>([]);
  const [live, setLive] = useState<PowerLog | null>(null);
  const [battery, setBattery] = useState<BatteryStatus | null>(null);

  useEffect(() => {
    const fetchAll = async () => {
      const [historyResult, batteryResult] = await Promise.allSettled([
        api.get<PowerLog[]>("/api/history"),
        api.get<BatteryStatus>("/api/battery/status"),
      ]);

      if (historyResult.status === "fulfilled") {
        setHistory(historyResult.value.data);
        if (historyResult.value.data.length > 0) {
          setLive(historyResult.value.data[historyResult.value.data.length - 1]);
        }
      } else {
        console.error("History-Abruf fehlgeschlagen", historyResult.reason);
      }

      if (batteryResult.status === "fulfilled" && batteryResult.value.data.success) {
        setBattery(batteryResult.value.data);
      }
    };

    fetchAll();
    const interval = setInterval(fetchAll, 60_000);
    return () => clearInterval(interval);
  }, []);

  const gridPower = live?.grid_power ?? 0;
  const gridLabel = gridPower >= 0 ? "Netzbezug" : "Einspeisung";
  const gridFormatted = formatPower(Math.abs(gridPower));

  const battPower = live?.battery_power ?? 0;
  const battLabel = battPower >= 0 ? "Batterie entlädt" : "Batterie lädt";
  const battFormatted = formatPower(Math.abs(battPower));
  const battSoc = live?.battery_soc ?? 0;

  const pvFormatted = formatPower(live?.pv_power ?? 0);
  const loadFormatted = formatPower(live?.load_power ?? 0);

  const controlModeLabels: Record<number, string> = {
    0: "Limits aus",
    1: "Ladelimit",
    2: "Entladelimit",
    3: "Lade + Entladelimit",
  };

  const chargeLimitWatt = battery?.charge_limit_watt ?? 0;
  const dischargeLimitWatt = battery?.discharge_limit_watt ?? 0;


  return (
    <div className="min-h-screen bg-slate-900 p-6 md:p-10">
      <h1 className="text-2xl font-bold text-white mb-8">
        PV Monitoring Dashboard
      </h1>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          icon={<Sun size={20} />}
          label="PV-Erzeugung"
          value={pvFormatted.value}
          unit={pvFormatted.unit}
          color="#facc15"
        />
        <StatCard
          icon={<Home size={20} />}
          label="Hausverbrauch"
          value={loadFormatted.value}
          unit={loadFormatted.unit}
          color="#38bdf8"
        />
        <StatCard
          icon={<Zap size={20} />}
          label={gridLabel}
          value={gridFormatted.value}
          unit={gridFormatted.unit}
          color="#a78bfa"
        />
        <StatCard
          icon={<Battery size={20} />}
          label={battLabel}
          value={battFormatted.value}
          unit={battFormatted.unit}
          color="#4ade80"
          extra={`Ladestand: ${battSoc.toFixed(0)} %`}
        />
      </div>

      {/* Chart */}
      <div className="rounded-2xl bg-slate-900 p-5">
        <h2 className="text-lg font-semibold text-white mb-4">
          Verlauf (letzte 24 h)
        </h2>
        <ResponsiveContainer width="100%" height={400}>
          <ComposedChart data={history}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
            <XAxis
              dataKey="created"
              tickFormatter={formatTime}
              stroke="#64748b"
              tick={{ fontSize: 12 }}
            />
            <YAxis
              stroke="#64748b"
              tick={{ fontSize: 12 }}
              tickFormatter={yAxisTickFormatter}
            />
            <ReferenceArea
              y1={0}
              ifOverflow="hidden"
              fill="rgba(185, 28, 28, 0.15)"
              strokeOpacity={0}
            />
            <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="3 3" />
            <Tooltip
              contentStyle={{
                backgroundColor: "#1e293b",
                border: "1px solid #334155",
                borderRadius: 8,
              }}
              labelFormatter={formatTime}
              formatter={(value: number) => formatPowerString(value)}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey="pv_power"
              name="PV"
              stroke="#facc15"
              dot={false}
              strokeWidth={2}
            />
            <Line
              type="monotone"
              dataKey="load_power"
              name="Verbrauch"
              stroke="#38bdf8"
              dot={false}
              strokeWidth={2}
            />
            <Line
              type="monotone"
              dataKey="grid_power"
              name="Netz"
              stroke="#a78bfa"
              dot={false}
              strokeWidth={2}
            />
            <Line
              type="monotone"
              dataKey="battery_power"
              name="Batterie"
              stroke="#4ade80"
              dot={false}
              strokeWidth={2}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Modbus Batterie-Steuerung */}
      {battery && (
        <div className="rounded-2xl bg-slate-800 p-6 mt-6">
          <h2 className="text-lg font-semibold text-white mb-4">
            Modbus Batterie-Steuerung
          </h2>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
            <div className="rounded-xl bg-slate-700/50 p-4">
              <div className="text-xs text-slate-400 mb-1">Max. Ladeleistung</div>
              <div className="text-lg font-semibold text-white">
                {formatPowerString(battery.wchamax_watt)}
              </div>
            </div>
            <div className="rounded-xl bg-slate-700/50 p-4">
              <div className="text-xs text-slate-400 mb-1">Ladelimit</div>
              <div className="text-lg font-semibold text-emerald-400">
                {Math.abs(battery.charge_limit_pct).toFixed(1)} %
              </div>
              <div className="text-xs text-slate-400 mt-1">
                = {formatPowerString(chargeLimitWatt)}
              </div>
            </div>
            <div className="rounded-xl bg-slate-700/50 p-4">
              <div className="text-xs text-slate-400 mb-1">Entladelimit</div>
              <div className="text-lg font-semibold text-orange-400">
                {Math.abs(battery.discharge_limit_pct).toFixed(1)} %
              </div>
              <div className="text-xs text-slate-400 mt-1">
                = {formatPowerString(dischargeLimitWatt)}
              </div>
            </div>
            <div className="rounded-xl bg-slate-700/50 p-4">
              <div className="text-xs text-slate-400 mb-1">Reserve</div>
              <div className="text-lg font-semibold text-sky-400">
                {battery.reserve_pct.toFixed(1)} %
              </div>
            </div>
            <div className="rounded-xl bg-slate-700/50 p-4">
              <div className="text-xs text-slate-400 mb-1">Steuerungsmodus</div>
              <div className="text-lg font-semibold text-white">
                {controlModeLabels[battery.control_mode] ?? `Modus ${battery.control_mode}`}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
