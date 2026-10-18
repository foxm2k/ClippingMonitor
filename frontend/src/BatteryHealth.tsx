import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { HeartPulse, ScrollText, RefreshCw } from "lucide-react";
import { fetchBatteryEvents } from "./api";
import type { BatteryEvent, BatteryHealth, BatterySource, HealthLevel } from "./api";

const STALE_AFTER_MS = 3 * 60 * 1000;

const LEVEL_COLOR: Record<HealthLevel, string> = {
  ok: "text-emerald-400",
  warn: "text-amber-400",
  crit: "text-red-400",
};

const LEVEL_LABEL: Record<HealthLevel, string> = {
  ok: "",
  warn: "Warnung",
  crit: "Kritisch",
};

const SEVERITY_LABEL: Record<BatteryEvent["severity"], string> = {
  info: "Info",
  warn: "Warnung",
  crit: "Kritisch",
};

const SEVERITY_BORDER: Record<BatteryEvent["severity"], string> = {
  info: "border-l-slate-500",
  warn: "border-l-amber-400",
  crit: "border-l-red-400",
};

const SEVERITY_TEXT: Record<BatteryEvent["severity"], string> = {
  info: "text-slate-300",
  warn: "text-amber-400",
  crit: "text-red-400",
};

const CATEGORY_LABEL: Record<string, string> = {
  mode: "Modus",
  standby: "Standby",
  backup: "Notstrom",
  cell_state: "Zellstatus",
  inverter: "Wechselrichter",
  error: "Fehler",
  source: "Datenquelle",
  temperature: "Temperatur",
  soh: "State of Health",
  cell_spread: "Zellspannung",
};

const SOURCE_LABEL: Record<BatterySource, string> = {
  components: "Komponenten-API",
  solar_api: "Solar API (eingeschränkt)",
  unavailable: "nicht erreichbar",
};

function fmt(value: number | null, digits: number, unit: string): string {
  return value == null ? "–" : `${value.toFixed(digits)} ${unit}`;
}

// Farbe nur bei vorhandenem Wert; die Stufe zusätzlich als Text (nicht nur Farbe)
function levelStyle(value: number | null, level: HealthLevel) {
  if (value == null) return { valueClass: "text-slate-500", levelNote: undefined };
  return {
    valueClass: LEVEL_COLOR[level] ?? "text-white",
    levelNote: level === "ok" ? undefined : LEVEL_LABEL[level],
  };
}

// Uhrzeit, bei anderem Kalendertag (in der Anzeige-Zeitzone) zusätzlich Tag und Monat
function formatUpdated(iso: string, nowMs: number, timezone: string): string {
  const date = new Date(iso);
  const day = (d: Date) => d.toLocaleDateString("de-AT", { timeZone: timezone });
  const time = date.toLocaleTimeString("de-AT", { hour: "2-digit", minute: "2-digit", timeZone: timezone });
  if (day(date) === day(new Date(nowMs))) return time;
  const dm = date.toLocaleDateString("de-AT", { day: "2-digit", month: "2-digit", timeZone: timezone });
  return `${dm}, ${time}`;
}

function Tile({
  label,
  value,
  valueClass = "text-white",
  levelNote,
  children,
}: {
  label: string;
  value: ReactNode;
  valueClass?: string;
  levelNote?: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-xl bg-slate-700/50 p-4">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-lg font-semibold ${valueClass}`}>
        <span className={levelNote ? "whitespace-nowrap" : undefined}>{value}</span>
        {levelNote && <span className="ml-2 inline-block text-xs font-medium">{levelNote}</span>}
      </div>
      {children && <div className="text-xs text-slate-400 mt-1 space-y-0.5">{children}</div>}
    </div>
  );
}

export function BatteryHealthCard({ health, timezone }: { health: BatteryHealth; timezone: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const modeNormal = health.battery_mode === "normal";
  const usableKwh =
    health.soh != null && health.capacity_wh != null
      ? (health.soh / 100) * (health.capacity_wh / 1000)
      : null;
  const current = health.current_dc;
  const currentHint = current == null || current === 0 ? "" : current > 0 ? " (lädt)" : " (entlädt)";
  const updated = formatUpdated(health.timestamp, now, timezone);
  const sourceLabel = SOURCE_LABEL[health.source] ?? health.source;
  const temp = levelStyle(health.temp_cell, health.levels.temp);
  const soh = levelStyle(health.soh, health.levels.soh);
  const spread = levelStyle(health.cell_spread_mv, health.levels.spread);
  const stale = now - Date.parse(health.timestamp) > STALE_AFTER_MS;

  return (
    <div className="rounded-2xl bg-slate-800 p-6 mt-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <HeartPulse size={20} className="text-emerald-400" />
          <h2 className="text-lg font-semibold text-white">Batterie-Zustand</h2>
        </div>
        <span className="text-xs text-slate-500">Stand {updated}</span>
      </div>

      {stale && (
        <div className="mb-4 rounded-lg bg-amber-400/10 px-3 py-2 text-xs text-amber-300">
          Daten veraltet – letzter Stand {updated}
        </div>
      )}

      {health.source !== "components" && (
        <div className="mb-4 rounded-lg bg-amber-400/10 px-3 py-2 text-xs text-amber-300">
          Datenquelle: {sourceLabel} – nicht alle Werte verfügbar.
        </div>
      )}

      <div className={`grid grid-cols-2 lg:grid-cols-6 gap-4${stale ? " opacity-60" : ""}`}>
        <Tile
          label="Batteriemodus"
          value={
            <span
              className={`inline-block rounded-full px-2.5 py-0.5 text-sm capitalize ${
                modeNormal ? "bg-emerald-400/15 text-emerald-300" : "bg-amber-400/15 text-amber-300"
              }`}
            >
              {health.battery_mode ?? "–"}
            </span>
          }
        >
          {health.battery_standby && <div>Standby</div>}
          {health.backup_mode && <div className="text-amber-300">Notstrom aktiv</div>}
        </Tile>

        <Tile label="Zelltemperatur" value={fmt(health.temp_cell, 1, "°C")} valueClass={temp.valueClass} levelNote={temp.levelNote}>
          {health.temp_cell_min != null && health.temp_cell_max != null && (
            <div>
              min {health.temp_cell_min.toFixed(1)} / max {health.temp_cell_max.toFixed(1)} °C
            </div>
          )}
          {health.temp_ambient != null && <div>Umgebung {health.temp_ambient.toFixed(1)} °C</div>}
          {health.temp_limit_min != null && health.temp_limit_max != null && (
            <div className="text-slate-500">
              Grenzen {health.temp_limit_min.toFixed(0)}…{health.temp_limit_max.toFixed(0)} °C
            </div>
          )}
        </Tile>

        <Tile label="State of Health" value={fmt(health.soh, 1, "%")} valueClass={soh.valueClass} levelNote={soh.levelNote}>
          {usableKwh != null && <div>≈ {usableKwh.toFixed(1)} kWh Kapazität</div>}
        </Tile>

        <Tile
          label="Zellspannung Δ"
          value={fmt(health.cell_spread_mv, 0, "mV")}
          valueClass={spread.valueClass}
          levelNote={spread.levelNote}
        >
          {health.cell_voltage_min != null && health.cell_voltage_max != null && (
            <div>
              {health.cell_voltage_min.toFixed(3)}–{health.cell_voltage_max.toFixed(3)} V
            </div>
          )}
          {health.module_count != null && health.module_count > 0 && <div>{health.module_count} Module</div>}
        </Tile>

        <Tile label="DC-Seite" value={fmt(health.voltage_dc, 0, "V")}>
          {current != null && (
            <div>
              {current > 0 ? "+" : ""}
              {current.toFixed(1)} A{currentHint}
            </div>
          )}
        </Tile>

        <Tile label="Vollzyklen" value={health.cycles == null ? "–" : health.cycles.toFixed(0)}>
          {health.lifetime_discharged_kwh != null && (
            <div>{health.lifetime_discharged_kwh.toFixed(0)} kWh entladen</div>
          )}
        </Tile>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-4 text-xs text-slate-500">
        <span>Zellstatus {health.cell_state ?? "–"}</span>
        <span className="text-slate-600">|</span>
        <span>BMS-Status {health.status_bms ?? "–"}</span>
        <span className="text-slate-600">|</span>
        <span>
          WR: {health.inverter_state ?? "–"}
          {health.inverter_status_code != null && ` (Status ${health.inverter_status_code})`}
        </span>
        {health.inverter_error_code != null && health.inverter_error_code !== 0 && (
          <span className="text-red-400">Fehlercode {health.inverter_error_code}</span>
        )}
        <span className="text-slate-600">|</span>
        <span>Quelle: {sourceLabel}</span>
      </div>
    </div>
  );
}

export function BatteryEventLog({ timezone, refreshTrigger }: { timezone: string; refreshTrigger: number }) {
  const [events, setEvents] = useState<BatteryEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [limit, setLimit] = useState(50);
  const [error, setError] = useState(false);

  const load = async (n: number) => {
    setLoading(true);
    try {
      setEvents(await fetchBatteryEvents(n));
      setError(false);
    } catch (err) {
      console.error("Batterie-Ereignisse konnten nicht geladen werden:", err);
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(limit);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshTrigger]);

  const handleLimitChange = (n: number) => {
    setLimit(n);
    load(n);
  };

  const formatTimestamp = (iso: string) =>
    new Date(iso).toLocaleString("de-AT", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: timezone,
    });

  return (
    <div className="rounded-2xl bg-slate-800 p-6 mt-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ScrollText size={20} className="text-emerald-400" />
          <h2 className="text-lg font-semibold text-white">Batterie-Ereignisse</h2>
        </div>
        <div className="text-sm text-slate-400">
          {events.length > 0 && <span>{events.length} Einträge</span>}
        </div>
      </div>

      <div className="flex items-center gap-3 mt-4 mb-3">
        <button
          type="button"
          onClick={() => load(limit)}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-full bg-slate-700 text-slate-300 hover:bg-slate-600 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Aktualisieren
        </button>
        <select
          aria-label="Anzahl Einträge"
          value={limit}
          onChange={(e) => handleLimitChange(Number(e.target.value))}
          className="rounded-lg bg-slate-700 border border-slate-600 text-white px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500"
        >
          <option value={50}>50 Einträge</option>
          <option value={100}>100 Einträge</option>
          <option value={200}>200 Einträge</option>
        </select>
      </div>

      {error && (
        <div className="mb-3 rounded-lg bg-amber-400/10 px-3 py-2 text-xs text-amber-300">
          Ereignisse konnten nicht geladen werden.
        </div>
      )}

      <div className="max-h-96 overflow-y-auto space-y-2">
        {loading && events.length === 0 && (
          <p className="text-sm text-slate-400 py-4 text-center">Lade Ereignisse…</p>
        )}
        {!loading && !error && events.length === 0 && (
          <p className="text-sm text-slate-400 py-4 text-center">
            Noch keine Ereignisse – Einträge entstehen nur bei Zustandsänderungen.
          </p>
        )}
        {events.map((e) => (
          <div
            key={e.id}
            className={`rounded-lg bg-slate-700/30 p-3 border-l-2 ${SEVERITY_BORDER[e.severity] ?? "border-l-slate-500"}`}
          >
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400 mb-1">
              <span className="font-mono">{formatTimestamp(e.created)}</span>
              <span className="text-slate-600">|</span>
              <span>{CATEGORY_LABEL[e.category] ?? e.category}</span>
            </div>
            <div className={`text-sm ${SEVERITY_TEXT[e.severity] ?? "text-slate-300"}`}>
              <span className="sr-only">{SEVERITY_LABEL[e.severity] ?? e.severity}: </span>
              {e.message}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
