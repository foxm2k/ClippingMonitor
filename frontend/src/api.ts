import axios from "axios";

const api = axios.create({
  baseURL: "http://localhost:8000",
});

export interface AppSettings {
  timezone: string;
  system_capacity_kwp: number;
  export_limit_percent: number;
  auto_control_active: boolean;
}

export async function fetchSettings(): Promise<AppSettings> {
  const response = await api.get<AppSettings>("/api/settings");
  return response.data;
}

export async function updateSettings(settings: AppSettings): Promise<AppSettings> {
  const response = await api.post<AppSettings>("/api/settings", settings);
  return response.data;
}

export default api;
