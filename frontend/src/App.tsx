import { useEffect, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
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

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString("de-AT", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function StatCard({
  icon,
  label,
  value,
  unit,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  unit: string;
  color: string;
}) {
  return (
    <div className="rounded-2xl bg-slate-800 p-5 flex flex-col gap-2">
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <span style={{ color }}>{icon}</span>
        {label}
      </div>
      <div className="text-2xl font-semibold text-white">
        {value.toFixed(0)}{" "}
        <span className="text-sm font-normal text-slate-400">{unit}</span>
      </div>
    </div>
  );
}

export default function App() {
  const [history, setHistory] = useState<PowerLog[]>([]);
  const [live, setLive] = useState<PowerLog | null>(null);

  useEffect(() => {
    const fetchHistory = async () => {
      try {
        const res = await api.get<PowerLog[]>("/api/history");
        setHistory(res.data);
        if (res.data.length > 0) {
          setLive(res.data[res.data.length - 1]);
        }
      } catch (err) {
        console.error("History-Abruf fehlgeschlagen", err);
      }
    };

    fetchHistory();
    const interval = setInterval(fetchHistory, 60_000);
    return () => clearInterval(interval);
  }, []);

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
          value={live?.pv_power ?? 0}
          unit="W"
          color="#facc15"
        />
        <StatCard
          icon={<Home size={20} />}
          label="Hausverbrauch"
          value={live?.load_power ?? 0}
          unit="W"
          color="#38bdf8"
        />
        <StatCard
          icon={<Zap size={20} />}
          label="Netz"
          value={live?.grid_power ?? 0}
          unit="W"
          color="#a78bfa"
        />
        <StatCard
          icon={<Battery size={20} />}
          label="Batterie"
          value={live?.battery_soc ?? 0}
          unit="%"
          color="#4ade80"
        />
      </div>

      {/* Chart */}
      <div className="rounded-2xl bg-slate-800 p-5">
        <h2 className="text-lg font-semibold text-white mb-4">
          Verlauf (letzte 24 h)
        </h2>
        <ResponsiveContainer width="100%" height={400}>
          <LineChart data={history}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
            <XAxis
              dataKey="created"
              tickFormatter={formatTime}
              stroke="#64748b"
              tick={{ fontSize: 12 }}
            />
            <YAxis stroke="#64748b" tick={{ fontSize: 12 }} unit=" W" />
            <Tooltip
              contentStyle={{
                backgroundColor: "#1e293b",
                border: "1px solid #334155",
                borderRadius: 8,
              }}
              labelFormatter={formatTime}
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
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
