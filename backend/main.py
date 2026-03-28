import os
import asyncio
import datetime
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from pocketbase import PocketBase
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from auto_control import AutoController
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

FRONIUS_URL = os.getenv("FRONIUS_URL", "http://192.168.123.79/solar_api/v1/GetPowerFlowRealtimeData.fcgi")
POCKETBASE_URL = os.getenv("POCKETBASE_URL", "http://127.0.0.1:8090")
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
POLL_INTERVAL = 60  # Sekunden
SETTINGS_FILE = Path(__file__).parent / "config" / "settings.json"


class ChargeLimitRequest(BaseModel):
    limit_pct: float


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
    safety_factor: float = 0.80


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
    except Exception:
        logger.error(f"Fehler beim Parsen der Fronius Daten:")
        return {
            "pv_power": 0.0,
            "load_power": 0.0,
            "grid_power": 0.0,
            "battery_power": 0.0,
            "battery_soc": 0.0,
        }


auto_controller: AutoController | None = None

_forecast_cache: list[dict] = []
_forecast_cache_time: datetime.datetime | None = None
_forecast_cache_settings_key: tuple | None = None
FORECAST_CACHE_TTL = 900  # 15 Minuten in Sekunden

_battery_status_cache: dict | None = None

# ---------------------------------------------------------------------------
# SSE Broadcaster – eine asyncio.Queue pro verbundenem Client
# ---------------------------------------------------------------------------
_sse_clients: list[asyncio.Queue] = []


async def broadcast_event(payload: dict) -> None:
    """Schreibt ein Event-Payload in alle aktiven Client-Queues."""
    msg = json.dumps(payload)
    disconnected: list[asyncio.Queue] = []
    for q in _sse_clients:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            disconnected.append(q)
    for q in disconnected:
        _sse_clients.remove(q)


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

            # SSE: Live-Daten + Chart-Signal an alle Clients senden
            live_record = {
                "id": record.id,
                "created": _format_created(record.created),
                **parsed,
            }
            await broadcast_event({"type": "live", "data": live_record})
            await broadcast_event({"type": "chart"})

            # Auto-Control prüfen
            battery_changed = False
            try:
                settings_response = await get_settings()
                if (
                    isinstance(settings_response, AppSettings)
                    and settings_response.auto_control_active
                    and auto_controller is not None
                ):
                    forecast_data = await _get_cached_forecast(settings_response)

                    battery_status = await _get_cached_battery_status()
                    wchamax = (
                        battery_status.get("wchamax_watt", 15000)
                        if battery_status
                        else 15000
                    )

                    export_limit_w = (
                        settings_response.system_capacity_kwp
                        * 1000
                        * settings_response.export_limit_percent
                        / 100
                    )

                    ac_result = auto_controller.run_cycle(
                        soc=parsed["battery_soc"],
                        grid_power=parsed["grid_power"],
                        forecast=forecast_data,
                        battery_cap_kwh=settings_response.battery_capacity_kwh,
                        wchamax_watt=wchamax,
                        export_limit_w=export_limit_w,
                        safety_factor=settings_response.safety_factor,
                    )

                    logger.info("AutoControl: %s", ac_result.reason)
                    await broadcast_event({"type": "autocontrol_log"})

                    if ac_result.should_write:
                        modbus_client = FroniusModbusClient()
                        write_result = await modbus_client.set_charge_limit(
                            ac_result.inwrte_pct
                        )
                        if write_result.get("success"):
                            auto_controller.update_current_inwrte(ac_result.inwrte_pct)
                            battery_changed = True
                            logger.info(
                                "AutoControl: InWRte auf %.1f%% gesetzt",
                                ac_result.inwrte_pct,
                            )
                        else:
                            logger.error(
                                "AutoControl: Modbus-Write fehlgeschlagen: %s",
                                write_result,
                            )
            except Exception:
                logger.exception("Fehler im Auto-Control-Zyklus")

            # SSE: Batterie-Status senden, wenn sich etwas geändert hat
            if battery_changed:
                try:
                    modbus_fresh = FroniusModbusClient()
                    batt_result = await modbus_fresh.get_battery_status()
                    if batt_result.get("success"):
                        await broadcast_event({"type": "battery", "data": batt_result})
                except Exception:
                    logger.exception("Fehler beim SSE-Broadcast des Batterie-Status")

        except httpx.ConnectError:
            logger.warning("Wechselrichter nicht erreichbar – überspringe Zyklus")
        except httpx.TimeoutException:
            logger.warning("Timeout beim Wechselrichter – überspringe Zyklus")
        except Exception:
            logger.exception("Fehler im Poll-Zyklus")

        await asyncio.sleep(POLL_INTERVAL)


def _forecast_settings_key(s: AppSettings) -> tuple:
    """Gibt einen hashbaren Schlüssel der forecast-relevanten Settings zurück."""
    return (
        s.location_lat,
        s.location_lon,
        s.panel_tilt,
        s.panel_azimuth,
        s.system_capacity_kwp,
        s.inverter_max_kw,
        s.system_efficiency,
    )


async def _get_cached_forecast(settings: AppSettings) -> list[dict]:
    global _forecast_cache, _forecast_cache_time, _forecast_cache_settings_key
    now = datetime.datetime.now(datetime.timezone.utc)
    current_key = _forecast_settings_key(settings)
    if (
        _forecast_cache_time is None
        or (now - _forecast_cache_time).total_seconds() > FORECAST_CACHE_TTL
        or current_key != _forecast_cache_settings_key
    ):
        _forecast_cache = await _fetch_forecast(settings)
        _forecast_cache_time = now
        _forecast_cache_settings_key = current_key
        logger.info("Forecast-Cache aktualisiert (%d Slots, Settings-Key: %s)", len(_forecast_cache), current_key)
    return _forecast_cache


async def _get_cached_battery_status() -> dict | None:
    global _battery_status_cache
    if _battery_status_cache is None:
        try:
            modbus = FroniusModbusClient()
            result = await modbus.get_battery_status()
            if result.get("success"):
                _battery_status_cache = result
                logger.info("Battery-Status-Cache initialisiert: wchamax=%d W", result.get("wchamax_watt", 0))
            else:
                logger.warning("Battery-Status konnte nicht gelesen werden: %s", result)
        except Exception:
            logger.exception("Fehler beim Lesen des Battery-Status für Cache")
    return _battery_status_cache


@asynccontextmanager
async def lifespan(app: FastAPI):
    global auto_controller
    auto_controller = AutoController()

    # Modbus-Verbindungstest beim Start
    modbus = FroniusModbusClient()
    result = await modbus.test_connection()
    if result["success"]:
        logger.info("Modbus-Test erfolgreich: %s", result)
    else:
        logger.warning("Modbus-Test fehlgeschlagen: %s", result)

    # Battery-Status initial cachen
    await _get_cached_battery_status()

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


@app.get("/api/events")
async def sse_events(request: Request):
    """SSE-Endpunkt: streamt Live-, Battery- und Chart-Events an den Browser."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    _sse_clients.append(queue)
    logger.info("SSE-Client verbunden (%d aktive Clients)", len(_sse_clients))

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"data": msg}
                except asyncio.TimeoutError:
                    # Keepalive-Kommentar, damit Nginx/Proxies die Verbindung nicht schließen
                    yield {"comment": "keepalive"}
        finally:
            if queue in _sse_clients:
                _sse_clients.remove(queue)
            logger.info("SSE-Client getrennt (%d aktive Clients)", len(_sse_clients))

    return EventSourceResponse(event_generator())


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


@app.post("/api/battery/charge_limit")
async def set_charge_limit(request: ChargeLimitRequest):
    try:
        modbus = FroniusModbusClient()
        result = await modbus.set_charge_limit(request.limit_pct)
        if not result.get("success"):
            return JSONResponse(status_code=503, content=result)
        return result
    except Exception:
        logger.exception("Fehler beim Setzen des Ladelimits")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Interner Fehler"},
        )


@app.get("/api/latest")
async def get_latest():
    try:
        pb = PocketBase(POCKETBASE_URL)
        result = await asyncio.to_thread(
            pb.collection("power_logs").get_list,
            1, 1, {"sort": "-created"},
        )
        if result.items:
            r = result.items[0]
            return {
                "id": r.id,
                "created": _format_created(r.created),
                "pv_power": r.pv_power,
                "load_power": r.load_power,
                "grid_power": r.grid_power,
                "battery_power": r.battery_power,
                "battery_soc": r.battery_soc,
            }
        return None
    except Exception:
        logger.exception("Fehler beim Abrufen des letzten Datenpunkts")
        return JSONResponse(status_code=500, content={"error": "Letzter Datenpunkt nicht verfügbar"})


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
                "battery_capacity_kwh": 0.0,
                "system_efficiency": 0.85,
                "safety_factor": 0.80,
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
        "minutely_15": "global_tilted_irradiance",
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

    times = data["minutely_15"]["time"]
    irradiances = data["minutely_15"]["global_tilted_irradiance"]

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


@app.get("/api/auto_control/log")
async def get_auto_control_log(limit: int = Query(default=120, ge=1, le=200)):
    if auto_controller is None:
        return []
    return auto_controller.get_log(limit)


@app.get("/api/auto_control/status")
async def get_auto_control_status():
    if auto_controller is None:
        return {"active": False}
    settings_response = await get_settings()
    active = (
        isinstance(settings_response, AppSettings)
        and settings_response.auto_control_active
    )
    return {
        "active": active,
        "current_inwrte": auto_controller._current_inwrte,
    }
