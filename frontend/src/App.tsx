import { useEffect, useRef, useState } from "react";
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
import { Sun, Home, Zap, Battery, Settings, Bot, RefreshCw } from "lucide-react";
import api from "./api";
import type { AppSettings, ForecastPoint, PowerLog, TimeRange } from "./api";
import { fetchSettings, updateSettings, fetchHistory, fetchLatest, fetchForecast, getTimeRange, setChargeLimit } from "./api";

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


type FilterMode = "24h" | "today" | "yesterday" | "tomorrow" | "sunwindow";

interface ChartPoint {
  time: string;
  pv_power: number | null;
  load_power: number | null;
  grid_power: number | null;
  battery_power: number | null;
  battery_soc: number | null;
  expected_kw: number | null;
}

function calculateEnergyKWh(data: ChartPoint[], key: "pv_power" | "expected_kw"): number {
  let energyWh = 0;
  let prevTime: number | null = null;
  let prevVal: number | null = null;

  for (const pt of data) {
    const val = pt[key];
    if (val == null) { prevTime = null; prevVal = null; continue; }
    const t = new Date(pt.time).getTime();
    if (prevTime != null && prevVal != null) {
      const dtMs = t - prevTime;
      if (dtMs > 0 && dtMs <= 1_800_000) {
        energyWh += ((prevVal + val) / 2) * (dtMs / 3_600_000);
      }
    }
    prevTime = t;
    prevVal = val;
  }

  return energyWh / 1000;
}

function calculateExcessEnergyKWh(data: ChartPoint[], key: "pv_power" | "expected_kw", limitWatts: number): number {
  let energyWh = 0;
  let prevTime: number | null = null;
  let prevExcess: number | null = null;

  for (const pt of data) {
    const val = pt[key];
    if (val == null) { prevTime = null; prevExcess = null; continue; }
    const excess = Math.max(0, val - limitWatts);
    const t = new Date(pt.time).getTime();
    if (prevTime != null && prevExcess != null) {
      const dtMs = t - prevTime;
      if (dtMs > 0 && dtMs <= 1_800_000) {
        energyWh += ((prevExcess + excess) / 2) * (dtMs / 3_600_000);
      }
    }
    prevTime = t;
    prevExcess = excess;
  }

  return energyWh / 1000;
}

function calculateExportedEnergyKWh(data: ChartPoint[]): number {
  let energyWh = 0;
  let prevTime: number | null = null;
  let prevVal: number | null = null;

  for (const pt of data) {
    if (pt.grid_power == null) { prevTime = null; prevVal = null; continue; }
    const val = Math.max(0, -pt.grid_power);
    const t = new Date(pt.time).getTime();
    if (prevTime != null && prevVal != null) {
      const dtMs = t - prevTime;
      if (dtMs > 0 && dtMs <= 1_800_000) {
        energyWh += ((prevVal + val) / 2) * (dtMs / 3_600_000);
      }
    }
    prevTime = t;
    prevVal = val;
  }

  return energyWh / 1000;
}

function calculateExportExcessEnergyKWh(data: ChartPoint[], limitWatts: number): number {
  let energyWh = 0;
  let prevTime: number | null = null;
  let prevExcess: number | null = null;

  for (const pt of data) {
    if (pt.grid_power == null) { prevTime = null; prevExcess = null; continue; }
    const excess = Math.max(0, -pt.grid_power - limitWatts);
    const t = new Date(pt.time).getTime();
    if (prevTime != null && prevExcess != null) {
      const dtMs = t - prevTime;
      if (dtMs > 0 && dtMs <= 1_800_000) {
        energyWh += ((prevExcess + excess) / 2) * (dtMs / 3_600_000);
      }
    }
    prevTime = t;
    prevExcess = excess;
  }

  return energyWh / 1000;
}

function mergeChartData(history: PowerLog[], forecast: ForecastPoint[], range: TimeRange): ChartPoint[] {
  // Open-Meteo liefert "2026-03-25T10:15" (UTC, 15-min) → key = "2026-03-25T10:15Z"
  const forecastByTime = new Map<string, number>();
  for (const f of forecast) {
    forecastByTime.set(f.time + "Z", f.expected_kw);
  }

  const rangeStart = new Date(range.start);
  const rangeEnd = new Date(range.end);
  const points: ChartPoint[] = [];

  // History points with matched forecast value per 15-min interval
  for (const h of history) {
    // PocketBase UTC: "2026-03-25T10:07:00.000Z" → round down to "2026-03-25T10:00Z"
    const d = new Date(h.created);
    d.setMinutes(d.getMinutes() - (d.getMinutes() % 15), 0, 0);
    const quarterKey = d.toISOString().slice(0, 16) + "Z";
    const fVal = forecastByTime.get(quarterKey) ?? null;
    points.push({
      time: h.created,
      pv_power: h.pv_power,
      load_power: h.load_power,
      grid_power: h.grid_power,
      battery_power: h.battery_power,
      battery_soc: h.battery_soc,
      expected_kw: fVal !== null ? fVal * 1000 : null,
    });
  }

  // Forecast-only points interpolated to 1-min resolution
  const latestHistory = history.length > 0 ? new Date(history[history.length - 1].created) : rangeStart;

  if (forecastByTime.size > 0) {
    // Collect future forecast anchor points (15-min intervals)
    // Include one extra anchor beyond rangeEnd so interpolation covers the full range
    const futureForecast: { time: number; value: number }[] = [];
    const latestMs = latestHistory.getTime();
    const collectEnd = rangeEnd.getTime() + 900_000;
    for (const [key, val] of forecastByTime) {
      const t = new Date(key).getTime();
      if (t >= latestMs && t <= collectEnd) {
        futureForecast.push({ time: t, value: val * 1000 });
      }
    }
    futureForecast.sort((a, b) => a.time - b.time);

    if (futureForecast.length >= 2) {
      // Walk in 1-min steps, linearly interpolating between 15-min anchors
      const start1 = new Date(latestHistory);
      start1.setSeconds(0, 0);
      start1.setMinutes(start1.getMinutes() + 1);

      let idx = 0;
      for (let t = start1.getTime(); t <= rangeEnd.getTime(); t += 60_000) {
        while (idx < futureForecast.length - 1 && futureForecast[idx + 1].time <= t) {
          idx++;
        }
        if (t < futureForecast[0].time || t > futureForecast[futureForecast.length - 1].time) continue;

        // Guard: idx on last anchor → emit its value directly (no idx+1 available)
        if (idx >= futureForecast.length - 1) {
          points.push({
            time: new Date(t).toISOString(),
            pv_power: null,
            load_power: null,
            grid_power: null,
            battery_power: null,
            battery_soc: null,
            expected_kw: futureForecast[idx].value,
          });
          continue;
        }

        const prev = futureForecast[idx];
        const next = futureForecast[idx + 1];
        const interpValue = prev.time === next.time
          ? prev.value
          : prev.value + (next.value - prev.value) * (t - prev.time) / (next.time - prev.time);

        points.push({
          time: new Date(t).toISOString(),
          pv_power: null,
          load_power: null,
          grid_power: null,
          battery_power: null,
          battery_soc: null,
          expected_kw: interpValue,
        });
      }
    } else if (futureForecast.length === 1) {
      points.push({
        time: new Date(futureForecast[0].time).toISOString(),
        pv_power: null,
        load_power: null,
        grid_power: null,
        battery_power: null,
        battery_soc: null,
        expected_kw: futureForecast[0].value,
      });
    }
  }

  points.sort((a, b) => a.time.localeCompare(b.time));
  return points;
}

function ChargeLimitCard({
  battery,
  autoControlActive,
  onLimitApplied,
  onToggleAutoMode,
}: {
  battery: BatteryStatus;
  autoControlActive: boolean;
  onLimitApplied: (newPct: number) => void;
  onToggleAutoMode: (v: boolean) => void;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  const [writing, setWriting] = useState(false);
  const [localAutoMode, setLocalAutoMode] = useState(autoControlActive);

  useEffect(() => {
    setLocalAutoMode(autoControlActive);
  }, [autoControlActive]);

  const handleToggle = (v: boolean) => {
    setLocalAutoMode(v);
    setTimeout(() => onToggleAutoMode(v), 0);
  };

  const displayPct = draft ?? battery.charge_limit_pct;

  const handleApply = async () => {
    if (draft === null) return;
    setWriting(true);
    try {
      await setChargeLimit(draft);
      const result = await api.get<BatteryStatus>("/api/battery/status");
      if (result.data.success) onLimitApplied(result.data.charge_limit_pct);
      setDraft(null);
    } catch (err) {
      console.error("Failed to set charge limit:", err);
    } finally {
      setWriting(false);
    }
  };

  return (
    <div
      className={`col-span-2 lg:col-span-2 rounded-xl p-4 transition-colors border ${
        localAutoMode
          ? "bg-sky-900/20 border-sky-500/30"
          : "bg-slate-700/50 border-transparent"
      }`}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs text-slate-400">Ladelimit</div>
        <div className="flex items-center gap-2">
          <span className={`text-xs ${localAutoMode ? "text-sky-400" : "text-slate-500"}`}>
            <Bot size={12} className="inline -mt-0.5 mr-0.5" />
            KI
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={localAutoMode}
            onClick={() => handleToggle(!localAutoMode)}
            className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 ${
              localAutoMode ? "bg-sky-500" : "bg-slate-600"
            }`}
          >
            <span
              className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${
                localAutoMode ? "translate-x-[18px]" : "translate-x-[3px]"
              }`}
            />
          </button>
        </div>
      </div>
      <div className="text-2xl font-semibold text-emerald-400 mb-0.5">
        {displayPct.toFixed(1)} %
      </div>
      <div className="text-xs text-slate-400 mb-3">
        = {formatPowerString((displayPct / 100) * battery.wchamax_watt)}
      </div>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={displayPct}
        onChange={(e) => setDraft(Number(e.target.value))}
        disabled={localAutoMode || writing}
        className="w-full accent-emerald-400 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
      />
      {localAutoMode && (
        <div className="text-xs text-sky-400/70 mt-2">
          Automatik aktiv – Slider gesperrt
        </div>
      )}
      {!localAutoMode && draft !== null && draft !== battery.charge_limit_pct && (
        <div className="flex gap-2 mt-3">
          <button
            onClick={() => setDraft(null)}
            className="px-3 py-1.5 text-sm rounded-full bg-slate-600/40 text-slate-300 hover:bg-slate-600/60 transition-colors"
          >
            Abbrechen
          </button>
          <button
            onClick={handleApply}
            disabled={writing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-full bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 transition-colors disabled:opacity-50"
          >
            {writing ? (
              <>
                <span className="inline-block w-3 h-3 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin" />
                Schreibe…
              </>
            ) : (
              "Übernehmen"
            )}
          </button>
        </div>
      )}
    </div>
  );
}

interface AutoControlLogEntry {
  timestamp: string;
  soc: number;
  grid_power: number;
  inwrte_pct: number;
  should_write: boolean;
  reason: string;
  energy_needed_kwh: number;
  total_clipping_kwh: number;
  plan_summary: string;
}

function AutoControlLog({
  settings,
  battery,
  refreshTrigger,
}: {
  settings: AppSettings;
  battery: BatteryStatus;
  refreshTrigger: number;
}) {
  const [entries, setEntries] = useState<AutoControlLogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [limit, setLimit] = useState(120);

  const fetchLog = async (n: number) => {
    setLoading(true);
    try {
      const res = await api.get<AutoControlLogEntry[]>(`/api/auto_control/log?limit=${n}&_t=${Date.now()}`);
      setEntries(res.data);
    } catch (err) {
      console.error("Failed to fetch auto control log:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLog(limit);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshTrigger]);

  const handleLimitChange = (n: number) => {
    setLimit(n);
    fetchLog(n);
  };

  const formatTimestamp = (iso: string) =>
    new Date(iso).toLocaleTimeString("de-AT", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZone: settings.timezone,
    });

  return (
    <div className="rounded-2xl bg-slate-800 p-6 mt-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot size={20} className="text-sky-400" />
          <h2 className="text-lg font-semibold text-white">
            KI-Entscheidungsprotokoll
          </h2>
        </div>
        <div className="text-sm text-slate-400">
          {entries.length > 0 && <span>{entries.length} Einträge</span>}
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3 mt-4 mb-3">
        <button
          type="button"
          onClick={() => fetchLog(limit)}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-full bg-slate-700 text-slate-300 hover:bg-slate-600 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Aktualisieren
        </button>
        <select
          value={limit}
          onChange={(e) => handleLimitChange(Number(e.target.value))}
          className="rounded-lg bg-slate-700 border border-slate-600 text-white px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500"
        >
          <option value={30}>30 Einträge</option>
          <option value={60}>60 Einträge</option>
          <option value={120}>120 Einträge</option>
        </select>
      </div>

      {/* Log list */}
      <div className="max-h-96 overflow-y-auto space-y-2">
        {loading && entries.length === 0 && (
          <p className="text-sm text-slate-400 py-4 text-center">Lade Protokoll…</p>
        )}
        {!loading && entries.length === 0 && (
          <p className="text-sm text-slate-400 py-4 text-center">Noch keine Einträge vorhanden.</p>
        )}
        {entries.map((e, i) => {
          const isNotEnoughSun = e.plan_summary.includes("Nicht genug Sonne");
          const chargeWatt = (e.inwrte_pct / 100) * battery.wchamax_watt;
          return (
            <div
              key={i}
              className={`rounded-lg bg-slate-700/30 p-3 border-l-2 ${
                e.should_write ? "border-l-emerald-400" : "border-l-slate-600"
              }`}
            >
              {/* Kopfzeile: Zeit, SOC, Energiebedarf */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400 mb-2">
                <span className="font-mono">{formatTimestamp(e.timestamp)}</span>
                <span className="text-slate-600">|</span>
                <span>Batterie: {e.soc.toFixed(0)}%</span>
                <span className="text-slate-600">|</span>
                <span>Noch {e.energy_needed_kwh.toFixed(1)} kWh bis voll</span>
              </div>

              {/* Aktion */}
              <div className="text-sm mb-2">
                {e.should_write ? (
                  <span className="text-emerald-400">
                    ✍️ Ladelimit auf {e.inwrte_pct.toFixed(1)}% gesetzt
                    ({"\u2248"} {formatPowerString(chargeWatt)})
                  </span>
                ) : (
                  <span className="text-slate-400">
                    ⏸️ Keine Änderung — Ladelimit bleibt bei {e.inwrte_pct.toFixed(1)}%
                  </span>
                )}
              </div>

              {/* Grund */}
              <div
                className={`text-xs leading-relaxed ${
                  isNotEnoughSun ? "text-amber-400" : "text-slate-400"
                }`}
              >
                <span className="text-slate-500 font-medium">Grund: </span>
                {e.reason}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function App() {
  const [chartData, setChartData] = useState<ChartPoint[]>([]);
  const [live, setLive] = useState<PowerLog | null>(null);
  const [battery, setBattery] = useState<BatteryStatus | null>(null);
  const [logTrigger, setLogTrigger] = useState(0);
  const [activeFilter, setActiveFilter] = useState<FilterMode>("today");
  const [hiddenLines, setHiddenLines] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem("hiddenLines");
      return stored ? new Set<string>(JSON.parse(stored)) : new Set<string>();
    } catch {
      return new Set<string>();
    }
  });
  useEffect(() => {
    localStorage.setItem("hiddenLines", JSON.stringify([...hiddenLines]));
  }, [hiddenLines]);

  const activeFilterRef = useRef<FilterMode>("today");
  const forecastRef = useRef<ForecastPoint[]>([]);

  const [tab, setTab] = useState<"dashboard" | "settings">("dashboard");
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [settingsForm, setSettingsForm] = useState<AppSettings | null>(null);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsSuccess, setSettingsSuccess] = useState(false);

  const formatTime = makeTimeFormatter(settings?.timezone ?? "Europe/Berlin");

  const fetchLive = async () => {
    try {
      const latest = await fetchLatest();
      if (latest) setLive(latest);
    } catch (err) {
      console.error("fetchLive fehlgeschlagen:", err);
    }
  };

  const loadChartData = async (filter: FilterMode, forecastData: ForecastPoint[]) => {
    let range: TimeRange | undefined;

    if (filter === "sunwindow") {
      const todayRange = getTimeRange("today");
      const rangeStart = new Date(todayRange.start);
      const rangeEnd = new Date(todayRange.end);
      const todayForecast = forecastData.filter((f) => {
        const fTime = new Date(f.time + "Z");
        return fTime >= rangeStart && fTime <= rangeEnd;
      });
      const sunPoints = todayForecast.filter((f) => f.expected_kw > 0);
      if (sunPoints.length > 0) {
        const first = new Date(sunPoints[0].time + "Z");
        const last = new Date(sunPoints[sunPoints.length - 1].time + "Z");
        range = { start: first.toISOString(), end: last.toISOString() };
      } else {
        range = getTimeRange("today");
      }
    } else {
      range = getTimeRange(filter);
    }

    const histData = await fetchHistory(range);
    setChartData(mergeChartData(histData, forecastData, range!));
  };

  useEffect(() => {
    const init = async () => {
      const [forecastResult, batteryResult] = await Promise.allSettled([
        fetchForecast(),
        api.get<BatteryStatus>("/api/battery/status"),
        fetchLive(),
      ]);

      const forecastData = forecastResult.status === "fulfilled" ? forecastResult.value : [];
      forecastRef.current = forecastData;

      if (batteryResult.status === "fulfilled" && batteryResult.value.data.success) {
        setBattery(batteryResult.value.data);
      }

      await loadChartData(activeFilterRef.current, forecastData);
    };

    fetchSettings()
      .then((s) => { setSettings(s); setSettingsForm(s); })
      .catch((e) => console.error("Settings-Abruf fehlgeschlagen", e));

    init();

    // SSE: Echtzeit-Updates statt Polling
    const baseUrl = import.meta.env.VITE_API_BASE_URL || "";
    const eventSource = new EventSource(`${baseUrl}/api/events`);

    eventSource.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);

        if (payload.type === "live" && payload.data) {
          setLive(payload.data as PowerLog);
        }

        if (payload.type === "battery" && payload.data) {
          setBattery(payload.data as BatteryStatus);
        }

        if (payload.type === "chart") {
          loadChartData(activeFilterRef.current, forecastRef.current).catch((err) =>
            console.error("SSE chart reload failed:", err)
          );
        }

        if (payload.type === "autocontrol_log") {
          setLogTrigger((prev) => prev + 1);
        }
      } catch (err) {
        console.error("SSE message parse error:", err);
      }
    };

    eventSource.onerror = () => {
      console.warn("SSE-Verbindung unterbrochen – Browser versucht automatisch Reconnect.");
    };

    const forecastInterval = setInterval(async () => {
      try {
        const newForecast = await fetchForecast();
        forecastRef.current = newForecast;
        loadChartData(activeFilterRef.current, forecastRef.current).catch(console.error);
      } catch (err) {
        console.error("Automatischer Forecast-Reload fehlgeschlagen:", err);
      }
    }, 15 * 60 * 1000);

    return () => {
      clearInterval(forecastInterval);
      eventSource.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleLegendClick = (entry: { dataKey?: string | number | ((obj: unknown) => unknown) }) => {
    if (typeof entry.dataKey !== "string") return;
    const key = entry.dataKey;
    setHiddenLines((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const handleFilterChange = async (filter: FilterMode) => {
    activeFilterRef.current = filter;
    setActiveFilter(filter);
    await loadChartData(filter, forecastRef.current);
  };

  const handleToggleAutoMode = async (newValue: boolean) => {
    if (!settings) return;
    setSettings({ ...settings, auto_control_active: newValue });
    setSettingsForm((prev) => prev ? { ...prev, auto_control_active: newValue } : prev);
    try {
      await updateSettings({ ...settings, auto_control_active: newValue });
    } catch (e) {
      console.error("Failed to save auto_control_active:", e);
    }
  };

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

  const exportLimitWatts = settings
    ? (settings.system_capacity_kwp * 1000) * (settings.export_limit_percent / 100)
    : null;

  const generatedKWh = calculateEnergyKWh(chartData, "pv_power");
  const expectedKWh = calculateEnergyKWh(chartData, "expected_kw");
  const excessKWh = exportLimitWatts != null ? calculateExcessEnergyKWh(chartData, "pv_power", exportLimitWatts) : 0;
  const expectedExcessKWh = exportLimitWatts != null ? calculateExcessEnergyKWh(chartData, "expected_kw", exportLimitWatts) : 0;
  const exportedKWh = calculateExportedEnergyKWh(chartData);
  const exportedExcessKWh = exportLimitWatts != null ? calculateExportExcessEnergyKWh(chartData, exportLimitWatts) : 0;

  const pvFormatted = formatPower(live?.pv_power ?? 0);
  const loadFormatted = formatPower(live?.load_power ?? 0);

  const controlModeLabels: Record<number, string> = {
    0: "Limits aus",
    1: "Ladelimit",
    2: "Entladelimit",
    3: "Lade + Entladelimit",
  };

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
              extra={`Ladestand: ${battSoc.toFixed(0)} %${settings && settings.battery_capacity_kwh > 0 ? ` ≈ ${(battSoc / 100 * settings.battery_capacity_kwh).toFixed(1)} kWh` : ""}`}
            />
          </div>

          {/* Time filter bar */}
          <div className="flex flex-wrap gap-2 mb-4">
            {(
              [
                { key: "today", label: "Heute" },
                { key: "24h", label: "Letzte 24h" },
                { key: "yesterday", label: "Gestern" },
                { key: "tomorrow", label: "Morgen" },
                { key: "sunwindow", label: "☀ Sonnenfenster" },
              ] as { key: FilterMode; label: string }[]
            ).map(({ key, label }) => (
              <button
                key={key}
                onClick={() => handleFilterChange(key)}
                className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
                  activeFilter === key
                    ? "bg-amber-400 text-slate-900"
                    : "bg-slate-700 text-slate-300 hover:bg-slate-600 hover:text-white"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Energy summary */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm font-medium bg-slate-800/60 px-5 py-2.5 rounded-xl mb-4">
            <span className="text-amber-400">
              ⚡ Erzeugt: {generatedKWh.toFixed(1)} kWh
            </span>
            {settings && settings.export_limit_percent < 100 && (
              <span className="text-red-400">
                ⚠️ Über Limit: {excessKWh.toFixed(1)} kWh
              </span>
            )}
            <span className="border-l border-slate-600 h-4" />
            <span className="text-amber-400/50">
              🌤️ Erwartet: {expectedKWh.toFixed(1)} kWh
            </span>
            {settings && settings.export_limit_percent < 100 && (
              <span className="text-red-400/50">
                🔮 Erwartet über Limit: {expectedExcessKWh.toFixed(1)} kWh
              </span>
            )}
            <span className="border-l border-slate-600 h-4" />
            <span className="text-purple-400">
              🔌 Eingespeist: {exportedKWh.toFixed(1)} kWh
            </span>
            {settings && settings.export_limit_percent < 100 && (
              <span className="text-red-400">
                🛑 Über Limit: {exportedExcessKWh.toFixed(1)} kWh
              </span>
            )}
          </div>

          {/* Chart */}
          <div className="rounded-2xl bg-slate-900 p-5">
            <ResponsiveContainer width="100%" height={400}>
              <ComposedChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis
                  dataKey="time"
                  tickFormatter={(v: string) => formatTime(v)}
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
                {exportLimitWatts != null &&
                  settings!.export_limit_percent > 0 &&
                  !hiddenLines.has("pv_power") && (
                    <ReferenceLine
                      y={exportLimitWatts}
                      stroke="#ef4444"
                      strokeDasharray="3 3"
                      strokeOpacity={0.8}
                      label={{
                        position: "insideBottomRight",
                        value: "Erzeugungslimit",
                        fill: "#ef4444",
                        fontSize: 12,
                      }}
                    />
                  )}
                {exportLimitWatts != null &&
                  settings!.export_limit_percent > 0 &&
                  !hiddenLines.has("grid_power") && (
                    <ReferenceLine
                      y={-exportLimitWatts}
                      stroke="#ef4444"
                      strokeDasharray="3 3"
                      strokeOpacity={0.8}
                      label={{
                        position: "insideTopRight",
                        value: "Einspeiselimit",
                        fill: "#ef4444",
                        fontSize: 12,
                      }}
                    />
                  )}
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: 8,
                  }}
                  labelFormatter={(label: unknown) => formatTime(String(label))}
                  formatter={(value: unknown, name: unknown) => {
                    if (name === "Vorhersage PV") {
                      return [formatPowerString(Number(value)), "Erwartete PV-Leistung"];
                    }
                    return [formatPowerString(Number(value)), String(name)];
                  }}
                  itemSorter={(item) => {
                    const order = ["pv_power", "expected_kw", "load_power", "battery_power", "grid_power"];
                    const idx = order.indexOf(item.dataKey as string);
                    return idx >= 0 ? idx : 999;
                  }}
                />
                <Legend
                  onClick={handleLegendClick}
                  wrapperStyle={{ cursor: "pointer" }}
                  itemSorter={(item) => {
                    const order = ["pv_power", "expected_kw", "load_power", "battery_power", "grid_power"];
                    const idx = order.indexOf(item.dataKey as string);
                    return idx >= 0 ? idx : 999;
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="pv_power"
                  name="PV"
                  stroke="#facc15"
                  dot={false}
                  strokeWidth={2}
                  hide={hiddenLines.has("pv_power")}
                />
                <Line
                  type="monotone"
                  dataKey="expected_kw"
                  name="Vorhersage PV"
                  stroke="#fbbf24"
                  strokeDasharray="5 5"
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                  hide={hiddenLines.has("expected_kw")}
                />
                <Line
                  type="monotone"
                  dataKey="load_power"
                  name="Verbrauch"
                  stroke="#38bdf8"
                  dot={false}
                  strokeWidth={2}
                  hide={hiddenLines.has("load_power")}
                />
                <Line
                  type="monotone"
                  dataKey="battery_power"
                  name="Batterie"
                  stroke="#4ade80"
                  dot={false}
                  strokeWidth={2}
                  hide={hiddenLines.has("battery_power")}
                />
                <Line
                  type="monotone"
                  dataKey="grid_power"
                  name="Netz"
                  stroke="#a78bfa"
                  dot={false}
                  strokeWidth={2}
                  hide={hiddenLines.has("grid_power")}
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
              <div className="grid grid-cols-2 lg:grid-cols-6 gap-4">
                <ChargeLimitCard
                  battery={battery}
                  autoControlActive={settings?.auto_control_active === true}
                  onLimitApplied={(pct) => setBattery({ ...battery, charge_limit_pct: pct })}
                  onToggleAutoMode={handleToggleAutoMode}
                />
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
                  {settings && settings.battery_capacity_kwh > 0 && (
                    <div className="text-xs text-slate-400 mt-1">
                      = {(battery.reserve_pct / 100 * settings.battery_capacity_kwh).toFixed(1)} kWh
                    </div>
                  )}
                </div>
                <div className="rounded-xl bg-slate-700/50 p-4">
                  <div className="text-xs text-slate-400 mb-1">Max. Ladeleistung</div>
                  <div className="text-lg font-semibold text-white">
                    {formatPowerString(battery.wchamax_watt)}
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

          {/* Auto-Control Entscheidungsprotokoll */}
          {battery && settings?.auto_control_active === true && (
            <AutoControlLog settings={settings} battery={battery} refreshTrigger={logTrigger} />
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
              <div className="flex flex-col gap-4">

                {/* Cluster: Allgemein */}
                <div className="rounded-xl bg-slate-700/30 p-4 flex flex-col gap-4">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                    Allgemein
                  </h3>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-sm font-medium text-slate-300">Zeitzone</label>
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

                  <div className="flex flex-col gap-1.5">
                    <div className="flex items-center justify-between">
                      <label className="text-sm font-medium text-slate-300">
                        Forecast-Sicherheitsfaktor
                      </label>
                      <span className="text-sm font-semibold text-sky-400">
                        {(settingsForm.safety_factor * 100).toFixed(0)} %
                      </span>
                    </div>
                    <input
                      type="range"
                      min={0.5}
                      max={1.0}
                      step={0.01}
                      value={settingsForm.safety_factor}
                      onChange={(e) =>
                        setSettingsForm({
                          ...settingsForm,
                          safety_factor: parseFloat(e.target.value),
                        })
                      }
                      className="w-full accent-sky-500"
                    />
                    <div className="flex justify-between text-xs text-slate-500">
                      <span>50 % (konservativ)</span>
                      <span>100 % (optimistisch)</span>
                    </div>
                    <p className="text-xs text-slate-500">
                      Abschlag auf den PV-Forecast für die KI-Batteriesteuerung. Niedrigere Werte = früherer Ladebeginn als Sicherheitspuffer.
                    </p>
                  </div>
                </div>

                {/* Cluster: Standort */}
                <div className="rounded-xl bg-slate-700/30 p-4 flex flex-col gap-4">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                    Standort
                  </h3>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="flex flex-col gap-1.5">
                      <label className="text-sm font-medium text-slate-300">
                        Breitengrad
                      </label>
                      <input
                        type="number"
                        step={0.001}
                        value={settingsForm.location_lat}
                        onChange={(e) =>
                          setSettingsForm({
                            ...settingsForm,
                            location_lat: parseFloat(e.target.value) || 0,
                          })
                        }
                        className="rounded-lg bg-slate-700 border border-slate-600 text-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-sm font-medium text-slate-300">
                        Längengrad
                      </label>
                      <input
                        type="number"
                        step={0.001}
                        value={settingsForm.location_lon}
                        onChange={(e) =>
                          setSettingsForm({
                            ...settingsForm,
                            location_lon: parseFloat(e.target.value) || 0,
                          })
                        }
                        className="rounded-lg bg-slate-700 border border-slate-600 text-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
                      />
                    </div>
                  </div>
                </div>

                {/* Cluster: PV-Anlage */}
                <div className="rounded-xl bg-slate-700/30 p-4 flex flex-col gap-4">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                    PV-Anlage
                  </h3>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="flex flex-col gap-1.5">
                      <label className="text-sm font-medium text-slate-300">
                        Neigungswinkel (°)
                      </label>
                      <input
                        type="number"
                        min={0}
                        max={90}
                        step={1}
                        value={settingsForm.panel_tilt}
                        onChange={(e) =>
                          setSettingsForm({
                            ...settingsForm,
                            panel_tilt: parseInt(e.target.value, 10) || 0,
                          })
                        }
                        className="rounded-lg bg-slate-700 border border-slate-600 text-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-sm font-medium text-slate-300">
                        Azimut (°)
                      </label>
                      <input
                        type="number"
                        min={-180}
                        max={180}
                        step={1}
                        value={settingsForm.panel_azimuth}
                        onChange={(e) =>
                          setSettingsForm({
                            ...settingsForm,
                            panel_azimuth: parseInt(e.target.value, 10) || 0,
                          })
                        }
                        className="rounded-lg bg-slate-700 border border-slate-600 text-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
                      />
                      <p className="text-xs text-slate-500">Süd=0, Ost=−90, West=90</p>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="flex flex-col gap-1.5">
                      <label className="text-sm font-medium text-slate-300">
                        Modulleistung (kWp)
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
                        className="rounded-lg bg-slate-700 border border-slate-600 text-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-sm font-medium text-slate-300">
                        Wechselrichter (kW)
                      </label>
                      <input
                        type="number"
                        min={0.1}
                        max={1000}
                        step={0.1}
                        value={settingsForm.inverter_max_kw}
                        onChange={(e) =>
                          setSettingsForm({
                            ...settingsForm,
                            inverter_max_kw: parseFloat(e.target.value) || 0,
                          })
                        }
                        className="rounded-lg bg-slate-700 border border-slate-600 text-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
                      />
                    </div>
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <div className="flex items-center justify-between">
                      <label className="text-sm font-medium text-slate-300">
                        Systemwirkungsgrad
                      </label>
                      <span className="text-sm font-semibold text-sky-400">
                        {(settingsForm.system_efficiency * 100).toFixed(0)} %
                      </span>
                    </div>
                    <input
                      type="range"
                      min={0.5}
                      max={1.0}
                      step={0.01}
                      value={settingsForm.system_efficiency}
                      onChange={(e) =>
                        setSettingsForm({
                          ...settingsForm,
                          system_efficiency: parseFloat(e.target.value),
                        })
                      }
                      className="w-full accent-sky-500"
                    />
                    <div className="flex justify-between text-xs text-slate-500">
                      <span>50 %</span>
                      <span>100 %</span>
                    </div>
                  </div>
                </div>

                {/* Cluster: Batteriespeicher */}
                <div className="bg-slate-700/30 rounded-xl p-4 space-y-4">
                  <h3 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
                    Batteriespeicher
                  </h3>
                  <div className="flex flex-col gap-1.5">
                    <label className="text-sm font-medium text-slate-300">
                      Kapazität Batteriespeicher (kWh)
                    </label>
                    <input
                      type="number"
                      min={0}
                      max={1000}
                      step={0.1}
                      value={settingsForm.battery_capacity_kwh}
                      onChange={(e) =>
                        setSettingsForm({
                          ...settingsForm,
                          battery_capacity_kwh: parseFloat(e.target.value) || 0,
                        })
                      }
                      className="rounded-lg bg-slate-700 border border-slate-600 text-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
                    />
                    <p className="text-xs text-slate-500">
                      Nutzbare Kapazität des installierten Batteriespeichers.
                    </p>
                  </div>
                </div>

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
