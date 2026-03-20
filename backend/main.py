import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pocketbase import PocketBase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

FRONIUS_URL = "http://192.168.123.79/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
POCKETBASE_URL = "http://127.0.0.1:8090"
POLL_INTERVAL = 60  # Sekunden


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
async def get_history():
    try:
        pb = PocketBase(POCKETBASE_URL)
        result = await asyncio.to_thread(
            pb.collection("power_logs").get_list,
            1,       # page
            1440,    # per_page (24h bei 1-Min-Intervall)
            {"sort": "created"},
        )
        records = [
            {
                "id": r.id,
                "created": r.created,
                "pv_power": r.pv_power,
                "load_power": r.load_power,
                "grid_power": r.grid_power,
                "battery_power": r.battery_power,
                "battery_soc": r.battery_soc,
            }
            for r in result.items
        ]
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
