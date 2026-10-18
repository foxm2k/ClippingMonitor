import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "",
});

export interface AppSettings {
  timezone: string;
  export_limit_percent: number;
  auto_control_active: boolean;
  location_lat: number;
  location_lon: number;
  panel_tilt: number;
  panel_azimuth: number;
  system_capacity_kwp: number;
  inverter_max_kw: number;
  battery_capacity_kwh: number;
  system_efficiency: number;
  safety_factor: number;
}

export interface PowerLog {
  id: string;
  created: string;
  pv_power: number;
  load_power: number;
  grid_power: number;
  battery_power: number;
  battery_soc: number;
}

export interface ForecastPoint {
  time: string;
  expected_kw: number;
}

export interface TimeRange {
  start: string;
  end: string;
}

export type HealthLevel = "ok" | "warn" | "crit";
export type BatterySource = "components" | "solar_api" | "unavailable";

export interface BatteryHealth {
  timestamp: string;
  source: BatterySource;
  battery_mode: string | null;
  battery_standby: boolean | null;
  backup_mode: boolean | null;
  soc: number | null;
  soh: number | null;
  temp_cell: number | null;
  temp_cell_min: number | null;
  temp_cell_max: number | null;
  temp_ambient: number | null;
  temp_limit_min: number | null;
  temp_limit_max: number | null;
  voltage_dc: number | null;
  current_dc: number | null;
  cell_state: number | null;
  status_bms: number | null;
  status_battery: number | null;
  stack_state_flags: number | null;
  cell_voltage_min: number | null;
  cell_voltage_max: number | null;
  cell_spread_mv: number | null;
  module_count: number | null;
  capacity_wh: number | null;
  lifetime_charged_kwh: number | null;
  lifetime_discharged_kwh: number | null;
  cycles: number | null;
  inverter_state: string | null;
  inverter_status_code: number | null;
  inverter_error_code: number | null;
  levels: { temp: HealthLevel; soh: HealthLevel; spread: HealthLevel };
}

export interface BatteryEvent {
  id: string;
  created: string;
  category: string;
  severity: "info" | "warn" | "crit";
  message: string;
  old_value: string;
  new_value: string;
}

export function getTimeRange(filter: "24h" | "today" | "yesterday" | "tomorrow"): TimeRange {
  const now = new Date();

  if (filter === "24h") {
    const start = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    return { start: start.toISOString(), end: now.toISOString() };
  }

  const y = now.getFullYear(), m = now.getMonth(), d = now.getDate();

  if (filter === "today") {
    const start = new Date(y, m, d);
    const end = new Date(new Date(y, m, d + 1).getTime() - 1);
    return { start: start.toISOString(), end: end.toISOString() };
  }

  if (filter === "yesterday") {
    const start = new Date(y, m, d - 1);
    const end = new Date(new Date(y, m, d).getTime() - 1);
    return { start: start.toISOString(), end: end.toISOString() };
  }

  // tomorrow
  const start = new Date(y, m, d + 1);
  const end = new Date(new Date(y, m, d + 2).getTime() - 1);
  return { start: start.toISOString(), end: end.toISOString() };
}

export async function fetchHistory(range?: TimeRange): Promise<PowerLog[]> {
  const params = range ? `?start=${encodeURIComponent(range.start)}&end=${encodeURIComponent(range.end)}` : "";
  const response = await api.get<PowerLog[]>(`/api/history${params}`);
  return response.data;
}

export async function fetchLatest(): Promise<PowerLog | null> {
  const response = await api.get<PowerLog | null>("/api/latest");
  return response.data;
}

export async function fetchSettings(): Promise<AppSettings> {
  const response = await api.get<AppSettings>("/api/settings");
  return response.data;
}

export async function updateSettings(settings: AppSettings): Promise<AppSettings> {
  const response = await api.post<AppSettings>("/api/settings", settings);
  return response.data;
}

export async function fetchForecast(): Promise<ForecastPoint[]> {
  const response = await api.get<ForecastPoint[]>("/api/forecast");
  return response.data;
}

export async function setChargeLimit(limitPct: number): Promise<void> {
  await api.post("/api/battery/charge_limit", { limit_pct: limitPct });
}

export async function fetchBatteryHealth(): Promise<BatteryHealth> {
  const response = await api.get<BatteryHealth>("/api/battery/health");
  return response.data;
}

export async function fetchBatteryEvents(limit: number): Promise<BatteryEvent[]> {
  const response = await api.get<BatteryEvent[]>(`/api/battery/events?limit=${limit}&_t=${Date.now()}`);
  return response.data;
}

export default api;
