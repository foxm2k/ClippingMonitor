import axios from "axios";

const api = axios.create({
  baseURL: "http://localhost:8000",
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

export function getTimeRange(filter: "24h" | "today" | "yesterday" | "tomorrow"): TimeRange {
  const now = new Date();

  if (filter === "24h") {
    const start = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    return { start: start.toISOString(), end: now.toISOString() };
  }

  const localDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  if (filter === "today") {
    const start = new Date(localDate);
    const end = new Date(localDate.getTime() + 24 * 60 * 60 * 1000 - 1);
    return { start: start.toISOString(), end: end.toISOString() };
  }

  if (filter === "yesterday") {
    const start = new Date(localDate.getTime() - 24 * 60 * 60 * 1000);
    const end = new Date(localDate.getTime() - 1);
    return { start: start.toISOString(), end: end.toISOString() };
  }

  // tomorrow
  const start = new Date(localDate.getTime() + 24 * 60 * 60 * 1000);
  const end = new Date(localDate.getTime() + 48 * 60 * 60 * 1000 - 1);
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

export default api;
