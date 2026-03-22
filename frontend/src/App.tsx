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
import { Sun, Home, Zap, Battery, Settings } from "lucide-react";
import api from "./api";
import type { AppSettings } from "./api";
import { fetchSettings, updateSettings } from "./api";

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

function makeTimeFormatter(timezone: string) {
  return (iso: string): string =>
    new Date(iso).toLocaleTimeString("de-AT", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: timezone,
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

function ToggleSwitch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-slate-300">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 ${
          checked ? "bg-sky-500" : "bg-slate-600"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
            checked ? "translate-x-6" : "translate-x-1"
          }`}
        />
      </button>
    </div>
  );
}

export default function App() {
  const [history, setHistory] = useState<PowerLog[]>([]);
  const [live, setLive] = useState<PowerLog | null>(null);
  const [battery, setBattery] = useState<BatteryStatus | null>(null);

  const [tab, setTab] = useState<"dashboard" | "settings">("dashboard");
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [settingsForm, setSettingsForm] = useState<AppSettings | null>(null);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsSuccess, setSettingsSuccess] = useState(false);

  const formatTime = makeTimeFormatter(settings?.timezone ?? "Europe/Berlin");

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

    fetchSettings()
      .then((s) => {
        setSettings(s);
        setSettingsForm(s);
      })
      .catch((e) => console.error("Settings-Abruf fehlgeschlagen", e));

    fetchAll();
    const interval = setInterval(fetchAll, 60_000);
    return () => clearInterval(interval);
  }, []);

  const handleSaveSettings = async () => {
    if (!settingsForm) return;
    setSettingsSaving(true);
    setSettingsError(null);
    setSettingsSuccess(false);
    try {
      const saved = await updateSettings(settingsForm);
      setSettings(saved);
      setSettingsForm(saved);
      setSettingsSuccess(true);
      setTimeout(() => setSettingsSuccess(false), 3000);
    } catch (e) {
      setSettingsError("Einstellungen konnten nicht gespeichert werden.");
      console.error(e);
    } finally {
      setSettingsSaving(false);
    }
  };

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
      {/* Header with tab switcher */}
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-2xl font-bold text-white">
          PV Monitoring Dashboard
        </h1>
        <div className="flex gap-2">
          <button
            onClick={() => setTab("dashboard")}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === "dashboard"
                ? "bg-slate-700 text-white"
                : "text-slate-400 hover:text-white hover:bg-slate-800"
            }`}
          >
            Dashboard
          </button>
          <button
            onClick={() => setTab("settings")}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === "settings"
                ? "bg-slate-700 text-white"
                : "text-slate-400 hover:text-white hover:bg-slate-800"
            }`}
          >
            <Settings size={16} />
            Einstellungen
          </button>
        </div>
      </div>

      {/* Dashboard tab */}
      {tab === "dashboard" && (
        <>
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
        </>
      )}

      {/* Settings tab */}
      {tab === "settings" && (
        <div className="max-w-lg">
          <div className="rounded-2xl bg-slate-800 p-6">
            <h2 className="text-lg font-semibold text-white mb-6">
              Systemeinstellungen
            </h2>

            {!settingsForm ? (
              <p className="text-slate-400 text-sm">Lade Einstellungen…</p>
            ) : (
              <div className="flex flex-col gap-6">

                {/* Timezone */}
                <div className="flex flex-col gap-1.5">
                  <label className="text-sm font-medium text-slate-300">
                    Zeitzone
                  </label>
                  <select
                    value={settingsForm.timezone}
                    onChange={(e) =>
                      setSettingsForm({ ...settingsForm, timezone: e.target.value })
                    }
                    className="rounded-lg bg-slate-700 border border-slate-600 text-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
                  >
                    <option value="Europe/Berlin">Europe/Berlin (CET/CEST)</option>
                    <option value="Europe/Vienna">Europe/Vienna (CET/CEST)</option>
                    <option value="Europe/London">Europe/London (GMT/BST)</option>
                    <option value="UTC">UTC</option>
                  </select>
                </div>

                {/* System capacity */}
                <div className="flex flex-col gap-1.5">
                  <label className="text-sm font-medium text-slate-300">
                    Anlagenleistung (kWp)
                  </label>
                  <input
                    type="number"
                    min={0.1}
                    max={1000}
                    step={0.1}
                    value={settingsForm.system_capacity_kwp}
                    onChange={(e) =>
                      setSettingsForm({
                        ...settingsForm,
                        system_capacity_kwp: parseFloat(e.target.value) || 0,
                      })
                    }
                    className="rounded-lg bg-slate-700 border border-slate-600 text-white px-3 py-2 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-sky-500"
                  />
                </div>

                {/* Export limit */}
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium text-slate-300">
                      Einspeisebegrenzung
                    </label>
                    <span className="text-sm font-semibold text-sky-400">
                      {settingsForm.export_limit_percent} %
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    step={1}
                    value={settingsForm.export_limit_percent}
                    onChange={(e) =>
                      setSettingsForm({
                        ...settingsForm,
                        export_limit_percent: parseInt(e.target.value, 10),
                      })
                    }
                    className="w-full accent-sky-500"
                  />
                  <div className="flex justify-between text-xs text-slate-500">
                    <span>0 %</span>
                    <span>100 %</span>
                  </div>
                </div>

                {/* Auto control */}
                <ToggleSwitch
                  checked={settingsForm.auto_control_active}
                  onChange={(v) =>
                    setSettingsForm({ ...settingsForm, auto_control_active: v })
                  }
                  label="Automatische Batteriesteuerung aktiv"
                />

                {/* Feedback */}
                {settingsError && (
                  <p className="text-sm text-red-400">{settingsError}</p>
                )}
                {settingsSuccess && (
                  <p className="text-sm text-emerald-400">
                    Einstellungen gespeichert.
                  </p>
                )}

                {/* Save button */}
                <button
                  type="button"
                  onClick={handleSaveSettings}
                  disabled={settingsSaving}
                  className="self-start rounded-lg bg-sky-500 hover:bg-sky-400 disabled:bg-slate-600 disabled:cursor-not-allowed text-white px-5 py-2.5 text-sm font-semibold transition-colors"
                >
                  {settingsSaving ? "Speichern…" : "Speichern"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
