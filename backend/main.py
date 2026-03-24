import asyncio
import datetime
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pocketbase import PocketBase
from pydantic import BaseModel

from modbus_service import FroniusModbusClient


def _format_created(val) -> str:
    """Normalisiert PocketBase-Zeitstempel zu UTC ISO-8601 mit Z-Suffix."""
    if isinstance(val, datetime.datetime):
        return val.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # Fallback: SDK hat String zurückgegeben (kein Parse-Erfolg)
    s = str(val).replace(" ", "T")
    return s if s.endswith("Z") else s[:19] + ".000Z"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

FRONIUS_URL = "http://192.168.123.79/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
POCKETBASE_URL = "http://127.0.0.1:8090"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
POLL_INTERVAL = 60  # Sekunden
SETTINGS_FILE = Path(__file__).parent / "config" / "settings.json"


class AppSettings(BaseModel):
    timezone: str = "Europe/Berlin"
    export_limit_percent: int = 0
    auto_control_active: bool = False
    location_lat: float = 48.137
    location_lon: float = 11.576
    panel_tilt: int = 30
    panel_azimuth: int = 0
    system_capacity_kwp: float = 0.0
    inverter_max_kw: float = 15.0
    battery_capacity_kwh: float = 0.0
    system_efficiency: float = 0.85


def parse_fronius_data(data: dict) -> dict:
    try:
        body_data = data.get("Body", {}).get("Data", {})
        site = body_data.get("Site", {})
        inverters = body_data.get("Inverters", {})

        # Sicheres Auslesen mit Fallback auf 0.0, falls der Wert None (null) ist
        p_pv = float(site.get("P_PV") or 0.0)
        p_load = abs(float(site.get("P_Load") or 0.0))
        p_grid = float(site.get("P_Grid") or 0.0)
        p_akku = float(site.get("P_Akku") or 0.0)

        # SOC liegt oft unter dem Schlüssel "1" bei Inverters
        inverter_1 = inverters.get("1", {})
        soc = float(inverter_1.get("SOC") or 0.0)

        return {
            "pv_power": p_pv,
            "load_power": p_load,
            "grid_power": p_grid,
            "battery_power": p_akku,
            "battery_soc": soc,
        }
    except Exception as e:
        print(f"Fehler beim Parsen der Fronius Daten: {e}")
        return {
            "pv_power": 0.0,
            "load_power": 0.0,
            "grid_power": 0.0,
            "battery_power": 0.0,
            "battery_soc": 0.0,
        }


async def poll_and_store_data():
    """Fragt zyklisch den Wechselrichter ab und speichert die Daten in PocketBase."""
    logger.info("Background-Poller gestartet (Intervall: %ds)", POLL_INTERVAL)

    while True:
        try:
            # Fronius-Daten asynchron abrufen
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(FRONIUS_URL)
                response.raise_for_status()
                raw_data = response.json()

            parsed = parse_fronius_data(raw_data)
            logger.info("Fronius-Daten empfangen: %s", parsed)

            # PocketBase SDK ist synchron → in Thread ausführen
            pb = PocketBase(POCKETBASE_URL)
            record = await asyncio.to_thread(
                pb.collection("power_logs").create, parsed
            )
            logger.info("Gespeichert in PocketBase (ID: %s)", record.id)

        except httpx.ConnectError:
            logger.warning("Wechselrichter nicht erreichbar – überspringe Zyklus")
        except httpx.TimeoutException:
            logger.warning("Timeout beim Wechselrichter – überspringe Zyklus")
        except Exception:
            logger.exception("Fehler im Poll-Zyklus")

        await asyncio.sleep(POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Modbus-Verbindungstest beim Start
    modbus = FroniusModbusClient()
    result = await modbus.test_connection()
    if result["success"]:
        logger.info("Modbus-Test erfolgreich: %s", result)
    else:
        logger.warning("Modbus-Test fehlgeschlagen: %s", result)

    task = asyncio.create_task(poll_and_store_data())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Background-Poller gestoppt")


app = FastAPI(title="PV Monitoring API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/history")
async def get_history(
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
):
    try:
        pb = PocketBase(POCKETBASE_URL)
        if start and end:
            # PocketBase erwartet Leerzeichen statt T im Zeitstempel
            pb_start = start.replace("T", " ")
            pb_end = end.replace("T", " ")
            query_params = {
                "sort": "created",
                "filter": f"created >= '{pb_start}' && created <= '{pb_end}'",
            }
            # get_full_list paginiert automatisch (PB-Limit: 1000/Seite)
            items = await asyncio.to_thread(
                pb.collection("power_logs").get_full_list,
                500,
                query_params,
            )
        else:
            query_params = {"sort": "-created"}
            result = await asyncio.to_thread(
                pb.collection("power_logs").get_list,
                1,
                1000,
                query_params,
            )
            items = result.items
        records = [
            {
                "id": r.id,
                "created": _format_created(r.created),
                "pv_power": r.pv_power,
                "load_power": r.load_power,
                "grid_power": r.grid_power,
                "battery_power": r.battery_power,
                "battery_soc": r.battery_soc,
            }
            for r in items
        ]
        # wenn kein Start/End: war absteigend sortiert, umkehren
        if not (start and end):
            records = list(reversed(records))
        return records
    except Exception:
        logger.exception("Fehler beim Abrufen der History")
        return JSONResponse(
            status_code=500,
            content={"error": "History konnte nicht geladen werden"},
        )


@app.get("/api/powerflow")
async def get_powerflow():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(FRONIUS_URL)
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"error": "Wechselrichter nicht erreichbar"},
        )
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"error": "Zeitüberschreitung bei Verbindung zum Wechselrichter"},
        )
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            status_code=502,
            content={"error": f"Wechselrichter-Fehler: {e.response.status_code}"},
        )


@app.get("/api/battery/status")
async def get_battery_status():
    try:
        modbus = FroniusModbusClient()
        result = await modbus.get_battery_status()
        if not result.get("success"):
            return JSONResponse(
                status_code=503,
                content=result,
            )
        return result
    except Exception:
        logger.exception("Fehler beim Abrufen des Batterie-Status")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Interner Fehler"},
        )


@app.get("/api/settings")
async def get_settings():
    try:
        if not SETTINGS_FILE.exists():
            initial = {
                "timezone": "Europe/Berlin",
                "export_limit_percent": 0,
                "auto_control_active": False,
                "location_lat": 48.137,
                "location_lon": 11.576,
                "panel_tilt": 30,
                "panel_azimuth": 0,
                "system_capacity_kwp": 0.0,
                "inverter_max_kw": 15.0,
                "system_efficiency": 0.85,
            }
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(initial, indent=4), encoding="utf-8")
            logger.info("settings.json nicht vorhanden – Datei mit Initialwerten angelegt")
            return AppSettings(**initial)
        content = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return AppSettings(**content)
    except Exception:
        logger.exception("Fehler beim Lesen der Einstellungen")
        return JSONResponse(
            status_code=500,
            content={"error": "Einstellungen konnten nicht geladen werden"},
        )


@app.post("/api/settings")
async def save_settings(settings: AppSettings):
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(settings.model_dump(), indent=4), encoding="utf-8"
        )
        logger.info("Einstellungen gespeichert: %s", settings)
        return settings
    except Exception:
        logger.exception("Fehler beim Speichern der Einstellungen")
        return JSONResponse(
            status_code=500,
            content={"error": "Einstellungen konnten nicht gespeichert werden"},
        )


async def _fetch_forecast(s: AppSettings) -> list[dict]:
    params = {
        "latitude": s.location_lat,
        "longitude": s.location_lon,
        "hourly": "global_tilted_irradiance",
        "tilt": s.panel_tilt,
        "azimuth": s.panel_azimuth,
        "past_days": 1,
        "forecast_days": 3,
        "timezone": "UTC",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(OPEN_METEO_URL, params=params)
        response.raise_for_status()
        data = response.json()

    times = data["hourly"]["time"]
    irradiances = data["hourly"]["global_tilted_irradiance"]

    result = []
    for time_str, irr in zip(times, irradiances):
        irr_val = float(irr or 0.0)
        expected_kw = (irr_val / 1000.0) * s.system_capacity_kwp * s.system_efficiency
        expected_kw = min(expected_kw, s.inverter_max_kw)
        result.append({"time": time_str, "expected_kw": round(expected_kw, 3)})

    return result


@app.get("/api/forecast")
async def get_forecast():
    try:
        s = await get_settings()
        if isinstance(s, JSONResponse):
            return s
        forecast = await _fetch_forecast(s)
        return forecast
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"error": "Open-Meteo nicht erreichbar"},
        )
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"error": "Timeout bei Open-Meteo"},
        )
    except Exception:
        logger.exception("Fehler beim Abrufen der Wettervorhersage")
        return JSONResponse(
            status_code=500,
            content={"error": "Vorhersage konnte nicht geladen werden"},
        )
