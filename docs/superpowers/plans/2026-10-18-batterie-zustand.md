# Batterie-Zustand & Ereignisprotokoll – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Batterie-Zustandswerte des Fronius Verto/Reserva (Modus, SoH, Zelltemperaturen, Zellspannungen, Zyklen, WR-Status) alle 60 s im Dashboard anzeigen, alle 5 min in PocketBase speichern und Zustandsänderungen in einem eigenen, persistenten Ereignisprotokoll festhalten.

**Architecture:** Ein neues, testbares Backend-Modul `battery_health.py` holt die Werte im bestehenden 60-s-Poller (interne WR-API `/api/components/readable`, Fallback offizielle Solar API), erkennt Ereignisse per Vergleich mit dem Vorzustand und drosselt DB-Schreibvorgänge auf 5 min. `main.py` verdrahtet das Modul mit SSE (`battery_health`, `battery_event`), PocketBase (`battery_health_logs`, `battery_events`) und zwei GET-Endpunkten. Im Frontend kommen zwei neue Komponenten in `BatteryHealth.tsx` dazu.

**Tech Stack:** FastAPI, httpx, PocketBase 0.36.7 (JS-Migrations), pytest; React 19 + TypeScript + Tailwind 4 + lucide-react.

---

## Bestätigte Anforderungen (2026-10-18)

1. Werte: Battery_Mode, BatteryStandby, BackupMode, Zelltemperatur (Ø/min/max), Zellstatus, SoH, DC-Spannung/-Strom, WR-Status/Fehlercode. Ergänzt nach Sichtung der WR-Weboberfläche: Zellspannung min/max + Differenz, Lifetime-Durchsatz → Vollzyklen, Umgebungstemperatur, Temperaturgrenzen aus dem Typenschild.
2. UI: neue Karte „Batterie-Zustand“ **über** „Modbus Batterie-Steuerung“, gleicher Kachel-Stil, farbige Werte; Badge an der Batterie-StatCard, wenn Modus ≠ `normal`. Kein Temperatur-Chart.
3. Log: Ereignisprotokoll (nur Änderungen/Schwellwertwechsel) in der UI; Messwerte nur in der DB.
4. DB: zwei neue Collections `battery_health_logs` (alle **5 min**) und `battery_events` (sofort). UI-Aktualisierung alle **60 s** per SSE. Keine Retention.
5. Schwellen fest im Code: Temperatur gelb < 5 °C / > 40 °C, rot < 0 °C / > 50 °C (1 K Hysterese); SoH gelb < 80 %; Zellspannungs-Differenz gelb ≥ 50 mV, rot ≥ 100 mV.
6. `Status_BatteryCell` / BMS-Status nur als Rohwert (Fronius zeigt selbst nur `STATE_3`).
7. KI-Steuerung bleibt unverändert.
8. Karte und Ereignisprotokoll immer sichtbar (unabhängig vom KI-Modus).

## Datenquellen (am 2026-10-18 am Gerät verifiziert, ohne Login)

| Quelle | Inhalt |
|---|---|
| `GET /solar_api/v1/GetPowerFlowRealtimeData.fcgi` (wird bereits gepollt) | `Inverters.1.Battery_Mode`, `Site.BatteryStandby`, `Site.BackupMode` |
| `GET /api/components/readable` (intern, undokumentiert) | Komponenten mit `attributes.device-class` = `Storage` / `BatteryStack` / `BatteryModule`; Kanäle siehe Fixture in Task 1 |
| `GET /solar_api/v1/GetStorageRealtimeData.cgi` (Fallback) | Temperatur, SOC, U/I, `Status_BatteryCell` – **kein** echter SoH (`Capacity_Maximum` = Nennkapazität) |
| `GET /solar_api/v1/GetInverterInfo.cgi` | `InverterState`, `StatusCode`, `ErrorCode` |

**Risiken / Annahmen, die der Implementierer kennen muss:**
- Die Komponenten-API ist undokumentiert und kann sich mit WR-Firmware-Updates ändern → Fallback-Kette + `source`-Feld in jedem Datensatz.
- Komponenten über `device-class` suchen, **nie** über Device-IDs (enthalten Hashes; ein getauschtes Modul taucht als deaktivierter Eintrag mit `channels: null` auf).
- Energiezähler sind in Wattsekunden (Plausibilitätsprüfung: 10 896 404 400 Ws = 3 027 kWh entladen ≈ 192 Vollzyklen bei 15,8 kWh).
- `BAT_CURRENT_DC_F64` > 0 = Laden (verifiziert: SOC stieg bei +4 A).
- PocketBase-Zahlenfelder kennen kein `null`: fehlende Werte werden als 0 gespeichert. Das Feld `source` zeigt, ob ein Datensatz aus dem Fallback stammt.

## Dateistruktur

| Datei | Aktion | Verantwortung |
|---|---|---|
| `backend/battery_health.py` | Create | Parser, Schwellen-Level, Ereignis-Erkennung, Schreib-Drossel, HTTP-Abruf mit Fallback. Keine FastAPI-/PocketBase-Abhängigkeit. |
| `backend/tests/test_battery_health.py` | Create | Unit-Tests für alles in `battery_health.py` |
| `backend/tests/fixtures/*.json` | Create | Gekürzte echte WR-Antworten |
| `backend/requirements-dev.txt` | Create | pytest |
| `backend/main.py` | Modify | Poller-Integration, SSE, PocketBase-Writes, 2 Endpunkte |
| `database/pb_migrations/1792320000_created_battery_health_logs.js` | Create | Collection Messwerte |
| `database/pb_migrations/1792320001_created_battery_events.js` | Create | Collection Ereignisse |
| `frontend/src/api.ts` | Modify | Typen + Fetch-Funktionen |
| `frontend/src/BatteryHealth.tsx` | Create | `BatteryHealthCard`, `BatteryEventLog` |
| `frontend/src/App.tsx` | Modify | State, SSE-Handler, Einbindung, StatCard-Badge |
| `README.md` | Modify | Endpunkte dokumentieren |

Befehle werden aus PowerShell im Repo-Root `C:\Users\Andi\Dev\ClippingManager` ausgeführt.

---

### Task 0: Test-Setup

**Files:**
- Create: `backend/requirements-dev.txt`

- [ ] **Step 1: Dev-Requirements anlegen**

```text
-r requirements.txt
pytest==8.4.2
```

- [ ] **Step 2: Installieren**

Run: `cd backend; .venv\Scripts\python.exe -m pip install -r requirements-dev.txt`
Expected: `Successfully installed ... pytest-8.4.2`

- [ ] **Step 3: Commit**

```bash
git add backend/requirements-dev.txt
git commit -m "pytest als Dev-Abhaengigkeit fuer Backend-Tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Parser für die WR-Antworten

**Files:**
- Create: `backend/tests/fixtures/components_readable.json`
- Create: `backend/tests/fixtures/storage_realtime.json`
- Create: `backend/tests/fixtures/powerflow.json`
- Create: `backend/tests/fixtures/inverter_info.json`
- Create: `backend/tests/test_battery_health.py`
- Create: `backend/battery_health.py`

- [ ] **Step 1: Fixtures anlegen** (gekürzte echte Antworten vom 2026-10-18; zwei deaktivierte Module prüfen die Filterung)

`backend/tests/fixtures/components_readable.json`:
```json
{
  "Body": {
    "Data": {
      "Fronius_Storage_test": {
        "attributes": {
          "device-class": "Storage",
          "[ENABLE]": "true",
          "nameplate": "{\"capacity_wh\":15795,\"tCellMax\":56.0,\"tCellMin\":-20.0}"
        },
        "channels": {
          "BAT_CURRENT_DC_F64": 4.0,
          "BAT_ENERGYACTIVE_LIFETIME_CHARGED_F64": 11683609200.0,
          "BAT_ENERGYACTIVE_LIFETIME_DISCHARGED_F64": 10896404400.0,
          "BAT_MODE_CELL_STATE_U16": 3.0,
          "BAT_MODE_STATUS_BATTERY_U32": 131072.0,
          "BAT_MODE_STATUS_BMS_U32": 2.0,
          "BAT_TEMPERATURE_CELL_F64": 20.0,
          "BAT_TEMPERATURE_CELL_MAX_F64": 20.8,
          "BAT_TEMPERATURE_CELL_MIN_F64": 19.3,
          "BAT_VALUE_STATE_OF_CHARGE_RELATIVE_F32": 15.800000190734863,
          "BAT_VALUE_STATE_OF_HEALTH_RELATIVE_U16": 99.0,
          "BAT_VOLTAGE_DC_INTERNAL_F64": 531.3,
          "DEVICE_TEMPERATURE_AMBIENTEMEAN_F32": 28.600000381469727
        }
      },
      "Fronius_BatteryStack_test": {
        "attributes": { "device-class": "BatteryStack", "[ENABLE]": "true" },
        "channels": {
          "BATSTACK_PERCENT_SOH_F32": 99.69999694824219,
          "BATSTACK_VALUE_STATE_FLAGS_U16": 176.0
        }
      },
      "Fronius_BatteryModule_a": {
        "attributes": { "device-class": "BatteryModule", "[ENABLE]": "true" },
        "channels": {
          "BATMODULE_VOLTAGE_CELL_MIN_F32": 3.313,
          "BATMODULE_VOLTAGE_CELL_MAX_F32": 3.322
        }
      },
      "Fronius_BatteryModule_b": {
        "attributes": { "device-class": "BatteryModule", "[ENABLE]": "true" },
        "channels": {
          "BATMODULE_VOLTAGE_CELL_MIN_F32": 3.316,
          "BATMODULE_VOLTAGE_CELL_MAX_F32": 3.319
        }
      },
      "Fronius_BatteryModule_getauscht": {
        "attributes": { "device-class": "BatteryModule", "[ENABLE]": "false" },
        "channels": null
      },
      "Fronius_BatteryModule_deaktiviert": {
        "attributes": { "device-class": "BatteryModule", "[ENABLE]": "false" },
        "channels": {
          "BATMODULE_VOLTAGE_CELL_MIN_F32": 3.0,
          "BATMODULE_VOLTAGE_CELL_MAX_F32": 3.5
        }
      },
      "Fronius_Inverter_test": {
        "attributes": { "model": "Fronius Gen24" },
        "channels": { "BAT_MODE_ENFORCED_U16": 0.0 }
      }
    }
  }
}
```

`backend/tests/fixtures/storage_realtime.json`:
```json
{
  "Body": {
    "Data": {
      "0": {
        "Controller": {
          "Capacity_Maximum": 15795.0,
          "Current_DC": -1.9,
          "DesignedCapacity": 15795.0,
          "Enable": 1,
          "StateOfCharge_Relative": 4.9000000953674316,
          "Status_BatteryCell": 3.0,
          "Temperature_Cell": 19.5,
          "Voltage_DC": 510.0
        },
        "Modules": []
      }
    }
  }
}
```

`backend/tests/fixtures/powerflow.json`:
```json
{
  "Body": {
    "Data": {
      "Inverters": { "1": { "Battery_Mode": "normal", "SOC": 4.9 } },
      "Site": { "BatteryStandby": true, "BackupMode": false, "P_Akku": 1089.0 }
    }
  }
}
```

`backend/tests/fixtures/inverter_info.json`:
```json
{
  "Body": {
    "Data": {
      "1": { "InverterState": "Running", "StatusCode": 7, "ErrorCode": 0 }
    }
  }
}
```

- [ ] **Step 2: Failing Tests schreiben**

`backend/tests/test_battery_health.py`:
```python
import json
from pathlib import Path

import pytest

from battery_health import (
    HEALTH_KEYS,
    parse_components,
    parse_inverter_info,
    parse_powerflow_battery,
    parse_storage_realtime,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_parse_components_reads_bms_values():
    h = parse_components(load("components_readable.json"))
    assert h["soh"] == 99.7  # Stack-Wert (F32) hat Vorrang vor Storage-U16
    assert h["soc"] == 15.8
    assert h["temp_cell"] == 20.0
    assert h["temp_cell_min"] == 19.3
    assert h["temp_cell_max"] == 20.8
    assert h["temp_ambient"] == 28.6
    assert h["temp_limit_min"] == -20.0
    assert h["temp_limit_max"] == 56.0
    assert h["voltage_dc"] == 531.3
    assert h["current_dc"] == 4.0
    assert h["cell_state"] == 3
    assert h["status_bms"] == 2
    assert h["status_battery"] == 131072
    assert h["stack_state_flags"] == 176
    assert h["capacity_wh"] == 15795
    assert h["lifetime_charged_kwh"] == 3245.4
    assert h["lifetime_discharged_kwh"] == 3026.8
    assert h["cycles"] == 191.6


def test_parse_components_ignores_disabled_modules():
    h = parse_components(load("components_readable.json"))
    assert h["module_count"] == 2
    assert h["cell_voltage_min"] == 3.313
    assert h["cell_voltage_max"] == 3.322
    assert h["cell_spread_mv"] == 9.0


def test_parse_components_returns_all_health_keys():
    assert set(parse_components(load("components_readable.json"))) == set(HEALTH_KEYS)


def test_parse_components_without_storage_raises():
    with pytest.raises(ValueError):
        parse_components({"Body": {"Data": {}}})


def test_parse_components_uses_storage_soh_without_stack():
    raw = load("components_readable.json")
    del raw["Body"]["Data"]["Fronius_BatteryStack_test"]
    h = parse_components(raw)
    assert h["soh"] == 99.0
    assert h["stack_state_flags"] is None


def test_parse_storage_realtime_fallback():
    h = parse_storage_realtime(load("storage_realtime.json"))
    assert set(h) == set(HEALTH_KEYS)
    assert h["soc"] == 4.9
    assert h["temp_cell"] == 19.5
    assert h["voltage_dc"] == 510.0
    assert h["current_dc"] == -1.9
    assert h["cell_state"] == 3
    assert h["capacity_wh"] == 15795
    assert h["soh"] is None  # Solar API liefert keinen echten SoH
    assert h["cell_spread_mv"] is None


def test_parse_storage_realtime_empty_raises():
    with pytest.raises(ValueError):
        parse_storage_realtime({"Body": {"Data": {}}})


def test_parse_powerflow_battery():
    assert parse_powerflow_battery(load("powerflow.json")) == {
        "battery_mode": "normal",
        "battery_standby": True,
        "backup_mode": False,
    }


def test_parse_inverter_info():
    assert parse_inverter_info(load("inverter_info.json")) == {
        "inverter_state": "Running",
        "inverter_status_code": 7,
        "inverter_error_code": 0,
    }
```

- [ ] **Step 3: Tests laufen lassen – müssen fehlschlagen**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'battery_health'`

- [ ] **Step 4: Parser implementieren**

`backend/battery_health.py`:
```python
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

WS_PER_KWH = 3_600_000  # Fronius-Energiezaehler liefern Wattsekunden

HEALTH_KEYS = (
    "soc",
    "soh",
    "temp_cell",
    "temp_cell_min",
    "temp_cell_max",
    "temp_ambient",
    "temp_limit_min",
    "temp_limit_max",
    "voltage_dc",
    "current_dc",
    "cell_state",
    "status_bms",
    "status_battery",
    "stack_state_flags",
    "cell_voltage_min",
    "cell_voltage_max",
    "cell_spread_mv",
    "module_count",
    "capacity_wh",
    "lifetime_charged_kwh",
    "lifetime_discharged_kwh",
    "cycles",
)
POWERFLOW_KEYS = ("battery_mode", "battery_standby", "backup_mode")
INVERTER_KEYS = ("inverter_state", "inverter_status_code", "inverter_error_code")
# Felder der PocketBase-Collection battery_health_logs
DB_FIELDS = ("source",) + POWERFLOW_KEYS + HEALTH_KEYS + INVERTER_KEYS


def _round(value, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def _int(value) -> int | None:
    return None if value is None else int(value)


def _empty(keys: tuple[str, ...]) -> dict:
    return dict.fromkeys(keys)


def _active_components(data: dict, device_class: str) -> list[dict]:
    """Aktive Komponenten einer Geraeteklasse.

    Getauschte/deaktivierte Module bleiben in der WR-Liste stehen
    ([ENABLE]=false, teils channels=None) und muessen ignoriert werden.
    """
    result = []
    for comp in data.values():
        attrs = comp.get("attributes") or {}
        if attrs.get("device-class") != device_class:
            continue
        if attrs.get("[ENABLE]") != "true" or not comp.get("channels"):
            continue
        result.append(comp)
    return result


def _parse_nameplate(raw: str | None) -> dict:
    """Das Typenschild steckt als JSON-String im Attribut 'nameplate'."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        logger.warning("Batterie-Typenschild nicht lesbar: %r", raw[:80])
        return {}


def parse_components(raw: dict) -> dict:
    """Wertet die interne WR-API /api/components/readable aus.

    Komponenten werden ueber ihre device-class gefunden, nicht ueber die
    Device-ID (enthaelt Hashes, die sich z. B. nach Modultausch aendern).
    """
    data = raw.get("Body", {}).get("Data", {})
    storages = _active_components(data, "Storage")
    if not storages:
        raise ValueError("Keine aktive Storage-Komponente gefunden")
    storage = storages[0]["channels"]
    nameplate = _parse_nameplate(storages[0]["attributes"].get("nameplate"))

    stacks = _active_components(data, "BatteryStack")
    stack = stacks[0]["channels"] if stacks else {}
    modules = [m["channels"] for m in _active_components(data, "BatteryModule")]

    cell_mins = [
        m["BATMODULE_VOLTAGE_CELL_MIN_F32"]
        for m in modules
        if m.get("BATMODULE_VOLTAGE_CELL_MIN_F32") is not None
    ]
    cell_maxs = [
        m["BATMODULE_VOLTAGE_CELL_MAX_F32"]
        for m in modules
        if m.get("BATMODULE_VOLTAGE_CELL_MAX_F32") is not None
    ]
    cell_min = min(cell_mins) if cell_mins else None
    cell_max = max(cell_maxs) if cell_maxs else None

    capacity_wh = nameplate.get("capacity_wh")
    charged_ws = storage.get("BAT_ENERGYACTIVE_LIFETIME_CHARGED_F64")
    discharged_ws = storage.get("BAT_ENERGYACTIVE_LIFETIME_DISCHARGED_F64")

    result = _empty(HEALTH_KEYS)
    result.update({
        "soc": _round(storage.get("BAT_VALUE_STATE_OF_CHARGE_RELATIVE_F32"), 1),
        # Stack liefert SoH mit Nachkommastelle, Storage nur ganzzahlig
        "soh": _round(
            stack.get("BATSTACK_PERCENT_SOH_F32",
                      storage.get("BAT_VALUE_STATE_OF_HEALTH_RELATIVE_U16")),
            1,
        ),
        "temp_cell": _round(storage.get("BAT_TEMPERATURE_CELL_F64"), 1),
        "temp_cell_min": _round(storage.get("BAT_TEMPERATURE_CELL_MIN_F64"), 1),
        "temp_cell_max": _round(storage.get("BAT_TEMPERATURE_CELL_MAX_F64"), 1),
        "temp_ambient": _round(storage.get("DEVICE_TEMPERATURE_AMBIENTEMEAN_F32"), 1),
        "temp_limit_min": _round(nameplate.get("tCellMin"), 1),
        "temp_limit_max": _round(nameplate.get("tCellMax"), 1),
        "voltage_dc": _round(storage.get("BAT_VOLTAGE_DC_INTERNAL_F64"), 1),
        "current_dc": _round(storage.get("BAT_CURRENT_DC_F64"), 2),
        "cell_state": _int(storage.get("BAT_MODE_CELL_STATE_U16")),
        "status_bms": _int(storage.get("BAT_MODE_STATUS_BMS_U32")),
        "status_battery": _int(storage.get("BAT_MODE_STATUS_BATTERY_U32")),
        "stack_state_flags": _int(stack.get("BATSTACK_VALUE_STATE_FLAGS_U16")),
        "cell_voltage_min": _round(cell_min, 3),
        "cell_voltage_max": _round(cell_max, 3),
        "cell_spread_mv": (
            _round((cell_max - cell_min) * 1000, 1)
            if cell_min is not None and cell_max is not None
            else None
        ),
        "module_count": len(modules),
        "capacity_wh": capacity_wh,
        "lifetime_charged_kwh": (
            _round(charged_ws / WS_PER_KWH, 1) if charged_ws is not None else None
        ),
        "lifetime_discharged_kwh": (
            _round(discharged_ws / WS_PER_KWH, 1) if discharged_ws is not None else None
        ),
        "cycles": (
            _round(discharged_ws / WS_PER_KWH / (capacity_wh / 1000), 1)
            if discharged_ws is not None and capacity_wh
            else None
        ),
    })
    return result


def parse_storage_realtime(raw: dict) -> dict:
    """Fallback: offizielle Solar API GetStorageRealtimeData.

    Liefert weniger Werte. SoH bleibt leer, weil Capacity_Maximum dort
    immer der Nennkapazitaet entspricht und 100 % vortaeuschen wuerde.
    """
    data = raw.get("Body", {}).get("Data", {})
    if not data:
        raise ValueError("Keine Speicherdaten in der Solar API")
    ctrl = next(iter(data.values()))["Controller"]

    result = _empty(HEALTH_KEYS)
    result.update({
        "soc": _round(ctrl.get("StateOfCharge_Relative"), 1),
        "temp_cell": _round(ctrl.get("Temperature_Cell"), 1),
        "voltage_dc": _round(ctrl.get("Voltage_DC"), 1),
        "current_dc": _round(ctrl.get("Current_DC"), 2),
        "cell_state": _int(ctrl.get("Status_BatteryCell")),
        "capacity_wh": ctrl.get("DesignedCapacity"),
    })
    return result


def parse_powerflow_battery(raw: dict) -> dict:
    """Batterie-Zustandsfelder aus GetPowerFlowRealtimeData."""
    data = raw.get("Body", {}).get("Data", {})
    site = data.get("Site", {})
    inverter = (data.get("Inverters") or {}).get("1", {})
    return {
        "battery_mode": inverter.get("Battery_Mode"),
        "battery_standby": site.get("BatteryStandby"),
        "backup_mode": site.get("BackupMode"),
    }


def parse_inverter_info(raw: dict) -> dict:
    """WR-Betriebszustand aus GetInverterInfo."""
    data = raw.get("Body", {}).get("Data", {})
    if not data:
        raise ValueError("Keine Wechselrichter-Info")
    inv = next(iter(data.values()))
    return {
        "inverter_state": inv.get("InverterState"),
        "inverter_status_code": _int(inv.get("StatusCode")),
        "inverter_error_code": _int(inv.get("ErrorCode")),
    }
```

- [ ] **Step 5: Tests laufen lassen – müssen bestehen**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests -v`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add backend/battery_health.py backend/tests
git commit -m "Batterie-Zustand: Parser fuer Komponenten-API, Solar API und WR-Info

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Schwellen-Level (ok / warn / crit)

**Files:**
- Modify: `backend/battery_health.py` (Konstanten oben, Funktionen ans Ende)
- Modify: `backend/tests/test_battery_health.py`

- [ ] **Step 1: Failing Tests ergänzen**

Import-Block erweitern um `soh_level, spread_level, temp_level`, dann ans Dateiende:
```python
# ---------------------------------------------------------------------------
# Level
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("temp, expected", [
    (20.0, "ok"), (5.0, "ok"), (40.0, "ok"),
    (4.9, "warn"), (40.1, "warn"),
    (-0.1, "crit"), (50.1, "crit"),
    (None, None),
])
def test_temp_level_thresholds(temp, expected):
    assert temp_level(temp) == expected


def test_temp_level_hysteresis():
    # Zurueck in ein besseres Level erst mit 1 K Abstand zur Schwelle
    assert temp_level(39.5, prev="warn") == "warn"
    assert temp_level(38.9, prev="warn") == "ok"
    assert temp_level(49.5, prev="crit") == "crit"
    assert temp_level(48.0, prev="crit") == "warn"
    assert temp_level(5.5, prev="warn") == "warn"
    assert temp_level(6.1, prev="warn") == "ok"


@pytest.mark.parametrize("soh, expected", [(99.7, "ok"), (80.0, "ok"), (79.9, "warn"), (None, None)])
def test_soh_level(soh, expected):
    assert soh_level(soh) == expected


@pytest.mark.parametrize("mv, expected", [
    (9.0, "ok"), (49.9, "ok"), (50.0, "warn"), (99.9, "warn"), (100.0, "crit"), (None, None),
])
def test_spread_level(mv, expected):
    assert spread_level(mv) == expected
```

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests -v`
Expected: FAIL mit `ImportError: cannot import name 'soh_level'`

- [ ] **Step 3: Implementieren**

In `backend/battery_health.py` direkt unter `WS_PER_KWH`:
```python
# Warnschwellen (bestaetigt 2026-10-18)
TEMP_WARN_LOW = 5.0
TEMP_WARN_HIGH = 40.0
TEMP_CRIT_LOW = 0.0
TEMP_CRIT_HIGH = 50.0
TEMP_HYSTERESIS = 1.0  # K – verhindert Flattern der Ereignisse an der Schwelle
SOH_WARN = 80.0
SPREAD_WARN_MV = 50.0
SPREAD_CRIT_MV = 100.0

LEVELS = ("ok", "warn", "crit")
```

Ans Dateiende:
```python
def _worst(levels) -> str:
    return max(levels, key=LEVELS.index)


def _temp_raw_level(temp: float, shift: float) -> str:
    if temp < TEMP_CRIT_LOW + shift or temp > TEMP_CRIT_HIGH - shift:
        return "crit"
    if temp < TEMP_WARN_LOW + shift or temp > TEMP_WARN_HIGH - shift:
        return "warn"
    return "ok"


def temp_level(temp: float | None, prev: str = "ok") -> str | None:
    """Level einer Zelltemperatur. None = Wert fehlt (Level nicht aendern)."""
    if temp is None:
        return None
    level = _temp_raw_level(temp, 0.0)
    if LEVELS.index(level) < LEVELS.index(prev):
        # Verbesserung erst akzeptieren, wenn TEMP_HYSTERESIS innerhalb der Grenze
        level = _temp_raw_level(temp, TEMP_HYSTERESIS)
    return level


def soh_level(soh: float | None) -> str | None:
    if soh is None:
        return None
    return "warn" if soh < SOH_WARN else "ok"


def spread_level(spread_mv: float | None) -> str | None:
    if spread_mv is None:
        return None
    if spread_mv >= SPREAD_CRIT_MV:
        return "crit"
    if spread_mv >= SPREAD_WARN_MV:
        return "warn"
    return "ok"
```

- [ ] **Step 4: Tests laufen lassen – müssen bestehen**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests -v`
Expected: alle passed (9 + 8 + 1 + 4 + 6 = 28)

- [ ] **Step 5: Commit**

```bash
git add backend/battery_health.py backend/tests/test_battery_health.py
git commit -m "Batterie-Zustand: Warnschwellen mit Temperatur-Hysterese

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Ereignis-Erkennung

**Files:**
- Modify: `backend/battery_health.py`
- Modify: `backend/tests/test_battery_health.py`

Regeln:
- Erster Snapshot nach Start = Basis, **keine** Änderungsereignisse; Level ≠ ok werden aber gemeldet.
- Änderung nur, wenn alter **und** neuer Wert nicht `None` sind (fehlende Werte erzeugen keine Ereignisse; der Ausfall selbst wird über `source` gemeldet).
- Fehlt ein Level-Wert, bleibt das bisherige Level stehen.

- [ ] **Step 1: Failing Tests ergänzen**

Import-Block erweitern um `DB_FIELDS, BatteryEventDetector`, dann ans Dateiende:
```python
# ---------------------------------------------------------------------------
# Ereignis-Erkennung
# ---------------------------------------------------------------------------

def snap(**overrides) -> dict:
    base = dict.fromkeys(DB_FIELDS)
    base.update(
        source="components",
        battery_mode="normal",
        battery_standby=False,
        backup_mode=False,
        cell_state=3,
        inverter_state="Running",
        inverter_error_code=0,
        temp_cell=20.0,
        temp_cell_min=19.0,
        temp_cell_max=21.0,
        soh=99.7,
        cell_spread_mv=9.0,
    )
    base.update(overrides)
    return base


def summary(events) -> list[tuple]:
    return [(e.category, e.severity, e.old_value, e.new_value) for e in events]


def test_first_snapshot_is_baseline():
    d = BatteryEventDetector()
    assert d.process(snap()) == []
    assert d.levels() == {"temp": "ok", "soh": "ok", "spread": "ok"}


def test_first_snapshot_reports_bad_levels():
    events = BatteryEventDetector().process(snap(temp_cell_max=41.0))
    assert summary(events) == [("temperature", "warn", "ok", "warn")]


def test_mode_change_and_back():
    d = BatteryEventDetector()
    d.process(snap())
    assert summary(d.process(snap(battery_mode="service"))) == [("mode", "warn", "normal", "service")]
    assert summary(d.process(snap())) == [("mode", "info", "service", "normal")]


def test_error_code_events():
    d = BatteryEventDetector()
    d.process(snap())
    assert summary(d.process(snap(inverter_error_code=567))) == [("error", "crit", "0", "567")]
    assert summary(d.process(snap())) == [("error", "info", "567", "0")]


def test_standby_backup_inverter_cell_state():
    d = BatteryEventDetector()
    d.process(snap())
    events = d.process(snap(
        battery_standby=True, backup_mode=True, inverter_state="Sleeping", cell_state=4,
    ))
    assert sorted(summary(events)) == sorted([
        ("standby", "info", "False", "True"),
        ("backup", "warn", "False", "True"),
        ("inverter", "warn", "Running", "Sleeping"),
        ("cell_state", "info", "3", "4"),
    ])
    standby = next(e for e in events if e.category == "standby")
    assert standby.message == "Batterie in Standby"


def test_missing_values_create_no_events():
    d = BatteryEventDetector()
    d.process(snap(temp_cell_max=41.0))
    events = d.process(snap(
        battery_mode=None, cell_state=None, inverter_error_code=None,
        temp_cell=None, temp_cell_min=None, temp_cell_max=None,
        soh=None, cell_spread_mv=None,
    ))
    assert events == []
    assert d.levels()["temp"] == "warn"  # Level bleibt stehen


def test_source_change():
    d = BatteryEventDetector()
    d.process(snap())
    assert summary(d.process(snap(source="solar_api"))) == [("source", "warn", "components", "solar_api")]
    assert summary(d.process(snap(source="unavailable"))) == [("source", "crit", "solar_api", "unavailable")]


def test_temperature_hysteresis_prevents_flapping():
    d = BatteryEventDetector()
    assert summary(d.process(snap(temp_cell_max=40.5))) == [("temperature", "warn", "ok", "warn")]
    assert d.process(snap(temp_cell_max=39.5)) == []
    assert summary(d.process(snap(temp_cell_max=38.9))) == [("temperature", "info", "warn", "ok")]


def test_cold_cell_is_critical():
    events = BatteryEventDetector().process(snap(temp_cell_min=-1.0))
    assert summary(events) == [("temperature", "crit", "ok", "crit")]
    assert events[0].message == "Zelltemperatur -1.0–21.0 °C: kritischer Bereich"


def test_soh_and_spread_levels():
    events = BatteryEventDetector().process(snap(soh=79.0, cell_spread_mv=120.0))
    assert sorted(summary(events)) == sorted([
        ("soh", "warn", "ok", "warn"),
        ("cell_spread", "crit", "ok", "crit"),
    ])
```

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests -v`
Expected: FAIL mit `ImportError: cannot import name 'BatteryEventDetector'`

- [ ] **Step 3: Implementieren**

Import oben ergänzen: `from dataclasses import dataclass`

Ans Dateiende:
```python
LEVEL_TEXT = {
    "ok": "wieder im Normalbereich",
    "warn": "Warnbereich",
    "crit": "kritischer Bereich",
}
SOURCE_TEXT = {
    "components": "Komponenten-API",
    "solar_api": "Solar API (eingeschraenkt)",
    "unavailable": "nicht erreichbar",
}
SOURCE_SEVERITY = {"components": "info", "solar_api": "warn", "unavailable": "crit"}


@dataclass
class BatteryEvent:
    category: str   # mode|standby|backup|cell_state|inverter|error|source|temperature|soh|cell_spread
    severity: str   # info|warn|crit
    message: str
    old_value: str
    new_value: str


def _describe_mode(old, new):
    return ("info" if new == "normal" else "warn"), f"Batteriemodus: {old} → {new}"


def _describe_standby(old, new):
    return "info", ("Batterie in Standby" if new else "Batterie aus Standby aufgewacht")


def _describe_backup(old, new):
    return ("warn", "Notstrombetrieb aktiv") if new else ("info", "Notstrombetrieb beendet")


def _describe_cell_state(old, new):
    return "info", f"Zellstatus: {old} → {new}"


def _describe_inverter(old, new):
    return ("info" if new == "Running" else "warn"), f"Wechselrichter: {old} → {new}"


def _describe_error(old, new):
    if new == 0:
        return "info", f"WR-Fehlercode {old} behoben"
    return "crit", f"WR-Fehlercode {new}"


def _describe_source(old, new):
    return SOURCE_SEVERITY[new], f"Datenquelle: {SOURCE_TEXT[old]} → {SOURCE_TEXT[new]}"


# (Snapshot-Feld, Ereignis-Kategorie, Beschreibung)
_CHANGE_RULES = (
    ("battery_mode", "mode", _describe_mode),
    ("battery_standby", "standby", _describe_standby),
    ("backup_mode", "backup", _describe_backup),
    ("cell_state", "cell_state", _describe_cell_state),
    ("inverter_state", "inverter", _describe_inverter),
    ("inverter_error_code", "error", _describe_error),
    ("source", "source", _describe_source),
)


def _fmt_temp(snap: dict) -> str:
    lo, hi = snap.get("temp_cell_min"), snap.get("temp_cell_max")
    if lo is not None and hi is not None:
        return f"{lo:.1f}–{hi:.1f} °C"
    value = hi if hi is not None else lo if lo is not None else snap.get("temp_cell")
    return f"{value:.1f} °C"


class BatteryEventDetector:
    """Vergleicht aufeinanderfolgende Snapshots und erzeugt Ereignisse."""

    def __init__(self):
        self._prev: dict | None = None
        self._levels = {"temp": "ok", "soh": "ok", "spread": "ok"}

    def levels(self) -> dict:
        return dict(self._levels)

    def process(self, snap: dict) -> list[BatteryEvent]:
        events: list[BatteryEvent] = []
        if self._prev is not None:
            for key, category, describe in _CHANGE_RULES:
                old, new = self._prev.get(key), snap.get(key)
                if old is None or new is None or old == new:
                    continue
                severity, message = describe(old, new)
                events.append(BatteryEvent(category, severity, message, str(old), str(new)))
        events.extend(self._update_levels(snap))
        self._prev = snap
        return events

    def _temp_level(self, snap: dict) -> str | None:
        values = [v for v in (snap.get("temp_cell_min"), snap.get("temp_cell_max")) if v is not None]
        if not values and snap.get("temp_cell") is not None:
            values = [snap["temp_cell"]]
        if not values:
            return None
        prev = self._levels["temp"]
        return _worst(temp_level(v, prev) for v in values)

    def _update_levels(self, snap: dict) -> list[BatteryEvent]:
        checks = (
            ("temp", "temperature", self._temp_level(snap),
             lambda: f"Zelltemperatur {_fmt_temp(snap)}"),
            ("soh", "soh", soh_level(snap.get("soh")),
             lambda: f"State of Health {snap['soh']:.1f} %"),
            ("spread", "cell_spread", spread_level(snap.get("cell_spread_mv")),
             lambda: f"Zellspannungs-Differenz {snap['cell_spread_mv']:.0f} mV"),
        )
        events = []
        for name, category, level, describe in checks:
            if level is None:  # Wert fehlt → Level unveraendert lassen
                continue
            old = self._levels[name]
            if level == old:
                continue
            self._levels[name] = level
            events.append(BatteryEvent(
                category=category,
                severity="info" if level == "ok" else level,
                message=f"{describe()}: {LEVEL_TEXT[level]}",
                old_value=old,
                new_value=level,
            ))
        return events
```

- [ ] **Step 4: Tests laufen lassen – müssen bestehen**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests -v`
Expected: alle passed (28 + 10 = 38)

- [ ] **Step 5: Commit**

```bash
git add backend/battery_health.py backend/tests/test_battery_health.py
git commit -m "Batterie-Zustand: Ereignis-Erkennung fuer Modus-, Status- und Schwellwertwechsel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Schreib-Drossel und Abruf mit Fallback

**Files:**
- Modify: `backend/battery_health.py`
- Modify: `backend/tests/test_battery_health.py`

- [ ] **Step 1: Failing Tests ergänzen**

Imports oben ergänzen:
```python
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
```
Import-Block aus `battery_health` erweitern um `COMPONENTS_PATH, INVERTER_INFO_PATH, STORAGE_PATH, PersistThrottle, db_record, fetch_battery_health`. Dann ans Dateiende:
```python
# ---------------------------------------------------------------------------
# Drossel, DB-Datensatz, Abruf
# ---------------------------------------------------------------------------

def test_persist_throttle_every_five_minutes():
    t = PersistThrottle()
    t0 = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    assert t.due(t0)
    t.mark(t0)
    assert not t.due(t0 + timedelta(minutes=4, seconds=59))
    assert t.due(t0 + timedelta(minutes=5))


def test_db_record_drops_missing_and_extra_fields():
    record = db_record({**snap(soh=None), "timestamp": "x", "levels": {}})
    assert "soh" not in record
    assert "timestamp" not in record and "levels" not in record
    assert record["battery_mode"] == "normal"
    assert set(record) <= set(DB_FIELDS)


def run_fetch(routes: dict) -> dict:
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(request.url.path, (404, {}))
        return httpx.Response(status, json=body)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_battery_health(client, "http://wr", load("powerflow.json"))

    return asyncio.run(go())


def test_fetch_uses_components_api():
    s = run_fetch({
        COMPONENTS_PATH: (200, load("components_readable.json")),
        INVERTER_INFO_PATH: (200, load("inverter_info.json")),
    })
    assert s["source"] == "components"
    assert s["soh"] == 99.7
    assert s["battery_mode"] == "normal"
    assert s["inverter_state"] == "Running"
    assert set(DB_FIELDS) <= set(s)
    assert s["timestamp"].endswith("Z")


def test_fetch_falls_back_to_solar_api():
    s = run_fetch({
        COMPONENTS_PATH: (500, {}),
        STORAGE_PATH: (200, load("storage_realtime.json")),
    })
    assert s["source"] == "solar_api"
    assert s["temp_cell"] == 19.5
    assert s["soh"] is None
    assert s["inverter_state"] is None  # GetInverterInfo hier 404


def test_fetch_all_sources_down():
    s = run_fetch({})
    assert s["source"] == "unavailable"
    assert s["soc"] is None
    assert s["battery_mode"] == "normal"  # kommt aus dem bereits geladenen PowerFlow
```

- [ ] **Step 2: Tests laufen lassen – müssen fehlschlagen**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests -v`
Expected: FAIL mit `ImportError: cannot import name 'COMPONENTS_PATH'`

- [ ] **Step 3: Implementieren**

Imports in `backend/battery_health.py` ergänzen:
```python
from datetime import datetime, timedelta, timezone

import httpx
```

Unter den Schwellen-Konstanten:
```python
PERSIST_INTERVAL = timedelta(minutes=5)

COMPONENTS_PATH = "/api/components/readable"
STORAGE_PATH = "/solar_api/v1/GetStorageRealtimeData.cgi"
INVERTER_INFO_PATH = "/solar_api/v1/GetInverterInfo.cgi"
```

Ans Dateiende:
```python
class PersistThrottle:
    """Laesst DB-Schreibvorgaenge hoechstens alle PERSIST_INTERVAL zu."""

    def __init__(self, interval: timedelta = PERSIST_INTERVAL):
        self.interval = interval
        self._last: datetime | None = None

    def due(self, now: datetime) -> bool:
        return self._last is None or now - self._last >= self.interval

    def mark(self, now: datetime) -> None:
        self._last = now


def db_record(snapshot: dict) -> dict:
    """PocketBase-Datensatz: nur bekannte Felder, fehlende Werte weglassen."""
    return {k: snapshot[k] for k in DB_FIELDS if snapshot.get(k) is not None}


async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


async def fetch_battery_health(
    client: httpx.AsyncClient, base_url: str, powerflow_raw: dict
) -> dict:
    """Sammelt alle Batterie-Zustandswerte zu einem Snapshot.

    Reihenfolge: Komponenten-API → Solar API → 'unavailable'.
    Wirft nie – der Poll-Zyklus darf hieran nicht scheitern.
    """
    source = "unavailable"
    health = _empty(HEALTH_KEYS)
    try:
        health = parse_components(await _get_json(client, base_url + COMPONENTS_PATH))
        source = "components"
    except Exception as e:
        logger.warning("Komponenten-API nicht nutzbar (%s) – versuche Solar API", e)
        try:
            health = parse_storage_realtime(await _get_json(client, base_url + STORAGE_PATH))
            source = "solar_api"
        except Exception as e2:
            logger.warning("Speicherdaten der Solar API nicht verfuegbar: %s", e2)

    inverter = _empty(INVERTER_KEYS)
    try:
        inverter = parse_inverter_info(await _get_json(client, base_url + INVERTER_INFO_PATH))
    except Exception as e:
        logger.warning("Wechselrichter-Info nicht verfuegbar: %s", e)

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "source": source,
        **parse_powerflow_battery(powerflow_raw),
        **health,
        **inverter,
    }
```

- [ ] **Step 4: Tests laufen lassen – müssen bestehen**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests -v`
Expected: alle passed (38 + 5 = 43)

- [ ] **Step 5: Smoke-Test gegen den echten WR**

Run: `cd backend; .venv\Scripts\python.exe -c "import asyncio, httpx, json; from battery_health import fetch_battery_health; c = httpx.AsyncClient(timeout=10); pf = httpx.get('http://192.168.123.79/solar_api/v1/GetPowerFlowRealtimeData.fcgi').json(); print(json.dumps(asyncio.run(fetch_battery_health(c, 'http://192.168.123.79', pf)), indent=1))"`
Expected: `"source": "components"`, `soh` ≈ 99–100, `module_count`: 5, `cycles` > 0, `inverter_state`: `"Running"`

- [ ] **Step 6: Commit**

```bash
git add backend/battery_health.py backend/tests/test_battery_health.py
git commit -m "Batterie-Zustand: Abruf mit Fallback-Kette und 5-Minuten-Schreibdrossel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: PocketBase-Collections

**Files:**
- Create: `database/pb_migrations/1792320000_created_battery_health_logs.js`
- Create: `database/pb_migrations/1792320001_created_battery_events.js`

Die Feldliste muss exakt `DB_FIELDS` aus `battery_health.py` entsprechen. Regeln wie bei `power_logs`: list/view/create offen, update/delete gesperrt (das Backend nutzt PocketBase ohne Auth). `id` legt PocketBase selbst an; `created`/`updated` müssen ab PB 0.23 als `autodate` explizit definiert werden.

- [ ] **Step 1: Migration Messwerte**

`database/pb_migrations/1792320000_created_battery_health_logs.js`:
```js
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const text = (name) => ({ name, type: "text" });
  const bool = (name) => ({ name, type: "bool" });
  const num = (name) => ({ name, type: "number" });

  const collection = new Collection({
    type: "base",
    name: "battery_health_logs",
    listRule: "",
    viewRule: "",
    createRule: "",
    updateRule: null,
    deleteRule: null,
    fields: [
      text("source"),
      text("battery_mode"),
      bool("battery_standby"),
      bool("backup_mode"),
      num("soc"),
      num("soh"),
      num("temp_cell"),
      num("temp_cell_min"),
      num("temp_cell_max"),
      num("temp_ambient"),
      num("temp_limit_min"),
      num("temp_limit_max"),
      num("voltage_dc"),
      num("current_dc"),
      num("cell_state"),
      num("status_bms"),
      num("status_battery"),
      num("stack_state_flags"),
      num("cell_voltage_min"),
      num("cell_voltage_max"),
      num("cell_spread_mv"),
      num("module_count"),
      num("capacity_wh"),
      num("lifetime_charged_kwh"),
      num("lifetime_discharged_kwh"),
      num("cycles"),
      text("inverter_state"),
      num("inverter_status_code"),
      num("inverter_error_code"),
      { name: "created", type: "autodate", onCreate: true, onUpdate: false },
      { name: "updated", type: "autodate", onCreate: true, onUpdate: true },
    ],
    indexes: [
      "CREATE INDEX idx_battery_health_logs_created ON battery_health_logs (created)",
    ],
  });

  return app.save(collection);
}, (app) => {
  return app.delete(app.findCollectionByNameOrId("battery_health_logs"));
});
```

- [ ] **Step 2: Migration Ereignisse**

`database/pb_migrations/1792320001_created_battery_events.js`:
```js
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const text = (name) => ({ name, type: "text" });

  const collection = new Collection({
    type: "base",
    name: "battery_events",
    listRule: "",
    viewRule: "",
    createRule: "",
    updateRule: null,
    deleteRule: null,
    fields: [
      text("category"),
      text("severity"),
      text("message"),
      text("old_value"),
      text("new_value"),
      { name: "created", type: "autodate", onCreate: true, onUpdate: false },
      { name: "updated", type: "autodate", onCreate: true, onUpdate: true },
    ],
    indexes: [
      "CREATE INDEX idx_battery_events_created ON battery_events (created)",
    ],
  });

  return app.save(collection);
}, (app) => {
  return app.delete(app.findCollectionByNameOrId("battery_events"));
});
```

- [ ] **Step 3: Anwenden und prüfen**

PocketBase neu starten (`start_pockbetbase.bat`; `serve` wendet neue Migrationen automatisch an). Dann:

Run: `curl.exe -s http://127.0.0.1:8090/api/collections/battery_health_logs/records; curl.exe -s http://127.0.0.1:8090/api/collections/battery_events/records`
Expected: beide `{"items":[],"page":1,"perPage":30,"totalItems":0,"totalPages":0}`

Fehlerfall: Startet PB mit einer Migrationsfehlermeldung, im Admin-UI (`http://127.0.0.1:8090/_/`) das Feldschema einer bestehenden Collection als Referenz exportieren und die Feld-Objekte angleichen.

- [ ] **Step 4: Commit**

```bash
git add database/pb_migrations/1792320000_created_battery_health_logs.js database/pb_migrations/1792320001_created_battery_events.js
git commit -m "PocketBase: Collections battery_health_logs und battery_events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Backend-Integration (Poller, SSE, Endpunkte)

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Imports und Konstante**

Nach `from contextlib import asynccontextmanager`:
```python
from dataclasses import asdict
```
Nach `from auto_control import AutoController`:
```python
from battery_health import BatteryEventDetector, PersistThrottle, db_record, fetch_battery_health
```
Nach `FRONIUS_URL = ...`:
```python
FRONIUS_BASE_URL = os.getenv("FRONIUS_BASE_URL", "http://192.168.123.79")
```

- [ ] **Step 2: Modul-Zustand** – nach `_battery_status_cache: dict | None = None`:
```python
_battery_health_cache: dict | None = None
battery_event_detector = BatteryEventDetector()
battery_persist_throttle = PersistThrottle()
```

- [ ] **Step 3: Verarbeitungsfunktion** – direkt vor `async def poll_and_store_data():`:
```python
async def _process_battery_health(powerflow_raw: dict) -> None:
    """Batterie-Zustand holen, an Clients senden, Ereignisse sofort und
    Messwerte gedrosselt (alle 5 min) in PocketBase speichern."""
    global _battery_health_cache
    async with httpx.AsyncClient(timeout=10.0) as client:
        snapshot = await fetch_battery_health(client, FRONIUS_BASE_URL, powerflow_raw)

    events = battery_event_detector.process(snapshot)
    snapshot["levels"] = battery_event_detector.levels()
    _battery_health_cache = snapshot
    await broadcast_event({"type": "battery_health", "data": snapshot})

    pb = PocketBase(POCKETBASE_URL)
    for ev in events:
        logger.info("Batterie-Ereignis [%s] %s", ev.severity, ev.message)
        await asyncio.to_thread(pb.collection("battery_events").create, asdict(ev))
    if events:
        await broadcast_event({"type": "battery_event"})

    now = datetime.datetime.now(datetime.timezone.utc)
    if battery_persist_throttle.due(now):
        await asyncio.to_thread(
            pb.collection("battery_health_logs").create, db_record(snapshot)
        )
        battery_persist_throttle.mark(now)
        logger.info("Batterie-Zustand gespeichert (Quelle: %s)", snapshot["source"])
```

- [ ] **Step 4: In den Poller einhängen** – in `poll_and_store_data()` direkt nach `await broadcast_event({"type": "chart"})`:
```python

            # Batterie-Zustand – eigener Fehlerkontext, damit Auto-Control weiterlaeuft
            try:
                await _process_battery_health(raw_data)
            except Exception:
                logger.exception("Fehler beim Verarbeiten des Batterie-Zustands")
```

- [ ] **Step 5: Endpunkte** – nach dem Endpunkt `set_charge_limit`:
```python
@app.get("/api/battery/health")
async def get_battery_health():
    if _battery_health_cache is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Noch keine Batterie-Zustandsdaten (erster Poll steht aus)"},
        )
    return _battery_health_cache


@app.get("/api/battery/events")
async def get_battery_events(limit: int = Query(default=50, ge=1, le=200)):
    try:
        pb = PocketBase(POCKETBASE_URL)
        result = await asyncio.to_thread(
            pb.collection("battery_events").get_list,
            1, limit, {"sort": "-created"},
        )
        return [
            {
                "id": r.id,
                "created": _format_created(r.created),
                "category": r.category,
                "severity": r.severity,
                "message": r.message,
                "old_value": r.old_value,
                "new_value": r.new_value,
            }
            for r in result.items
        ]
    except Exception:
        logger.exception("Fehler beim Abrufen der Batterie-Ereignisse")
        return JSONResponse(
            status_code=500,
            content={"error": "Batterie-Ereignisse konnten nicht geladen werden"},
        )
```

- [ ] **Step 6: Unit-Tests weiterhin grün**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests -v`
Expected: 43 passed

- [ ] **Step 7: Laufzeitprüfung**

PocketBase und Backend starten (`start_pockbetbase.bat`, `start_backend.bat`), ~70 s warten (erster Poll).

Run: `curl.exe -s http://127.0.0.1:8000/api/battery/health`
Expected: JSON mit `"source":"components"` und `"levels":{"temp":"ok","soh":"ok","spread":"ok"}`

Run: `curl.exe -s "http://127.0.0.1:8090/api/collections/battery_health_logs/records?sort=-created&perPage=1"`
Expected: `totalItems` ≥ 1; nach weiteren 2 min unverändert, nach 5 min +1

Run: `curl.exe -s http://127.0.0.1:8000/api/battery/events`
Expected: `[]` (bei normalem Betrieb) – kein Fehler

Im Backend-Log: kein `Fehler beim Verarbeiten des Batterie-Zustands`.

- [ ] **Step 8: Commit**

```bash
git add backend/main.py
git commit -m "Batterie-Zustand im Poller: SSE alle 60 s, DB alle 5 min, Ereignisse sofort

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Frontend-Typen und API-Funktionen

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Typen** – nach `interface TimeRange { ... }`:
```ts
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
```

- [ ] **Step 2: Fetch-Funktionen** – vor `export default api;`:
```ts
export async function fetchBatteryHealth(): Promise<BatteryHealth> {
  const response = await api.get<BatteryHealth>("/api/battery/health");
  return response.data;
}

export async function fetchBatteryEvents(limit: number): Promise<BatteryEvent[]> {
  const response = await api.get<BatteryEvent[]>(`/api/battery/events?limit=${limit}&_t=${Date.now()}`);
  return response.data;
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend; npx tsc -b`
Expected: keine Ausgabe (Exit 0)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "Frontend-API: Typen und Abruf fuer Batterie-Zustand und -Ereignisse

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Komponenten `BatteryHealthCard` und `BatteryEventLog`

**Files:**
- Create: `frontend/src/BatteryHealth.tsx`

Stil entspricht exakt der bestehenden „Modbus Batterie-Steuerung“-Karte (`rounded-2xl bg-slate-800 p-6`, Kacheln `rounded-xl bg-slate-700/50 p-4`) und dem KI-Protokoll (Liste mit farbigem `border-l-2`).

- [ ] **Step 1: Datei anlegen**

`frontend/src/BatteryHealth.tsx`:
```tsx
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { HeartPulse, ScrollText, RefreshCw } from "lucide-react";
import { fetchBatteryEvents } from "./api";
import type { BatteryEvent, BatteryHealth, BatterySource, HealthLevel } from "./api";

const LEVEL_COLOR: Record<HealthLevel, string> = {
  ok: "text-emerald-400",
  warn: "text-amber-400",
  crit: "text-red-400",
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

function Tile({
  label,
  value,
  valueClass = "text-white",
  children,
}: {
  label: string;
  value: ReactNode;
  valueClass?: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-xl bg-slate-700/50 p-4">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-lg font-semibold ${valueClass}`}>{value}</div>
      {children && <div className="text-xs text-slate-400 mt-1 space-y-0.5">{children}</div>}
    </div>
  );
}

export function BatteryHealthCard({ health, timezone }: { health: BatteryHealth; timezone: string }) {
  const modeNormal = health.battery_mode === "normal";
  const usableKwh =
    health.soh != null && health.capacity_wh != null
      ? (health.soh / 100) * (health.capacity_wh / 1000)
      : null;
  const current = health.current_dc;
  const currentHint = current == null || current === 0 ? "" : current > 0 ? " (lädt)" : " (entlädt)";
  const updated = new Date(health.timestamp).toLocaleTimeString("de-AT", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone,
  });

  return (
    <div className="rounded-2xl bg-slate-800 p-6 mt-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <HeartPulse size={20} className="text-emerald-400" />
          <h2 className="text-lg font-semibold text-white">Batterie-Zustand</h2>
        </div>
        <span className="text-xs text-slate-500">Stand {updated}</span>
      </div>

      {health.source !== "components" && (
        <div className="mb-4 rounded-lg bg-amber-400/10 px-3 py-2 text-xs text-amber-300">
          Datenquelle: {SOURCE_LABEL[health.source]} – nicht alle Werte verfügbar.
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-6 gap-4">
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

        <Tile label="Zelltemperatur" value={fmt(health.temp_cell, 1, "°C")} valueClass={LEVEL_COLOR[health.levels.temp]}>
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

        <Tile label="State of Health" value={fmt(health.soh, 1, "%")} valueClass={LEVEL_COLOR[health.levels.soh]}>
          {usableKwh != null && <div>≈ {usableKwh.toFixed(1)} kWh Kapazität</div>}
        </Tile>

        <Tile
          label="Zellspannung Δ"
          value={fmt(health.cell_spread_mv, 0, "mV")}
          valueClass={LEVEL_COLOR[health.levels.spread]}
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
        <span>Quelle: {SOURCE_LABEL[health.source]}</span>
      </div>
    </div>
  );
}

export function BatteryEventLog({ timezone, refreshTrigger }: { timezone: string; refreshTrigger: number }) {
  const [events, setEvents] = useState<BatteryEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [limit, setLimit] = useState(50);

  const load = async (n: number) => {
    setLoading(true);
    try {
      setEvents(await fetchBatteryEvents(n));
    } catch (err) {
      console.error("Batterie-Ereignisse konnten nicht geladen werden:", err);
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
          value={limit}
          onChange={(e) => handleLimitChange(Number(e.target.value))}
          className="rounded-lg bg-slate-700 border border-slate-600 text-white px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500"
        >
          <option value={50}>50 Einträge</option>
          <option value={100}>100 Einträge</option>
          <option value={200}>200 Einträge</option>
        </select>
      </div>

      <div className="max-h-96 overflow-y-auto space-y-2">
        {loading && events.length === 0 && (
          <p className="text-sm text-slate-400 py-4 text-center">Lade Ereignisse…</p>
        )}
        {!loading && events.length === 0 && (
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
            <div className={`text-sm ${SEVERITY_TEXT[e.severity] ?? "text-slate-300"}`}>{e.message}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend; npx tsc -b`
Expected: Exit 0

- [ ] **Step 3: Commit**

```bash
git add frontend/src/BatteryHealth.tsx
git commit -m "Frontend: Karte Batterie-Zustand und Batterie-Ereignisprotokoll

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Einbindung in `App.tsx`

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Imports** – Zeilen 16–17 ersetzen durch:
```tsx
import type { AppSettings, BatteryHealth, ForecastPoint, PowerLog, TimeRange } from "./api";
import { fetchSettings, updateSettings, fetchHistory, fetchLatest, fetchForecast, getTimeRange, setChargeLimit, fetchBatteryHealth } from "./api";
import { BatteryHealthCard, BatteryEventLog } from "./BatteryHealth";
```

- [ ] **Step 2: StatCard-Badge** – in `StatCard` Props um `badge` erweitern:
```tsx
  extra,
  badge,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit: string;
  color: string;
  extra?: string;
  badge?: string;
}) {
```
und die Label-Zeile ersetzen:
```tsx
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <span style={{ color }}>{icon}</span>
        {label}
        {badge && (
          <span className="ml-auto rounded-full bg-amber-400/15 px-2 py-0.5 text-xs text-amber-300 capitalize">
            {badge}
          </span>
        )}
      </div>
```

- [ ] **Step 3: State** – nach `const [logTrigger, setLogTrigger] = useState(0);`:
```tsx
  const [health, setHealth] = useState<BatteryHealth | null>(null);
  const [batteryEventTrigger, setBatteryEventTrigger] = useState(0);
```

- [ ] **Step 4: Initialer Abruf** – direkt nach `init();` im Mount-Effect:
```tsx
    // 503 bis zum ersten Poll ist normal – danach kommt der Wert per SSE
    fetchBatteryHealth().then(setHealth).catch(() => {});
```

- [ ] **Step 5: SSE-Handler** – nach dem `autocontrol_log`-Block in `eventSource.onmessage`:
```tsx

        if (payload.type === "battery_health" && payload.data) {
          setHealth(payload.data as BatteryHealth);
        }

        if (payload.type === "battery_event") {
          setBatteryEventTrigger((prev) => prev + 1);
        }
```

- [ ] **Step 6: Badge an der Batterie-StatCard** – in `<StatCard ... label={battLabel} ...>` nach der `extra`-Zeile:
```tsx
              badge={health?.battery_mode && health.battery_mode !== "normal" ? health.battery_mode : undefined}
```

- [ ] **Step 7: Karte über der Modbus-Steuerung** – direkt vor `{/* Modbus Batterie-Steuerung */}`:
```tsx
          {/* Batterie-Zustand */}
          {health && (
            <BatteryHealthCard health={health} timezone={settings?.timezone ?? "Europe/Berlin"} />
          )}

```

- [ ] **Step 8: Ereignisprotokoll** – nach dem `AutoControlLog`-Block (vor `</>`):
```tsx

          {/* Batterie-Ereignisprotokoll (immer sichtbar) */}
          <BatteryEventLog
            timezone={settings?.timezone ?? "Europe/Berlin"}
            refreshTrigger={batteryEventTrigger}
          />
```

- [ ] **Step 9: Build und Lint**

Run: `cd frontend; npm run build`
Expected: `✓ built in ...`, keine TS-Fehler

Run: `cd frontend; npm run lint`
Expected: wie Baseline vom 2026-10-18 – **2 problems (1 error, 1 warning)**, beide vorbestehend in `App.tsx` (`no-unused-expressions` im `handleLegendClick`). Keine neuen Meldungen in `BatteryHealth.tsx` oder `api.ts`.

- [ ] **Step 10: Sichtprüfung**

PocketBase, Backend, Frontend starten (`start_*.bat`), `http://localhost:5173` öffnen (Skill `run` / Playwright-Screenshot bei 1440 px und 390 px Breite):
- Karte „Batterie-Zustand“ erscheint über „Modbus Batterie-Steuerung“, 6 Kacheln, Modus-Badge grün „normal“.
- Werte plausibel zu `curl.exe -s http://127.0.0.1:8000/api/battery/health`.
- „Stand hh:mm“ springt nach ≤ 60 s weiter (SSE).
- „Batterie-Ereignisse“ sichtbar, auch bei KI-Modus aus.
- Mobil (390 px): 2-spaltiges Kachelraster, kein horizontales Scrollen.

Fallback-Test ohne Eingriff am WR: Backend in einer PowerShell mit `$env:FRONIUS_BASE_URL="http://192.168.123.250"; uvicorn main:app` starten (unerreichbare Adresse; PowerFlow läuft weiter über `FRONIUS_URL`). Erwartet: Karte zeigt den gelben Hinweis „Datenquelle: nicht erreichbar“, Kacheln zeigen „–“, Modus-Badge kommt weiterhin aus PowerFlow. Danach Backend normal neu starten. Hinweis: Ein Ereignis „Datenquelle“ entsteht nur bei einem Wechsel **innerhalb** eines Backend-Laufs (nach Neustart ist der erste Snapshot die Basis); die Ereignis-Logik ist durch die Unit-Tests abgedeckt.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "Dashboard: Batterie-Zustand, Ereignisprotokoll und Modus-Badge einbinden

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Dokumentation

**Files:**
- Modify: `README.md` (Abschnitt „API-Endpoints“, nach der Tabelle „Batterie-Steuerung“)

- [ ] **Step 1: Abschnitt ergänzen**
```markdown
### Batterie-Zustand

| Methode | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/api/battery/health` | Letzter Batterie-Zustand (Modus, SoH, Zelltemperaturen, Zellspannungen, Zyklen, WR-Status) – wird alle 60 s aktualisiert |
| `GET` | `/api/battery/events?limit=50` | Batterie-Ereignisprotokoll (Modus-/Statuswechsel, Schwellwerte) aus PocketBase |

Datenquelle ist die interne, undokumentierte WR-API `/api/components/readable` (ohne Login). Fällt sie aus, nutzt das Backend `GetStorageRealtimeData.cgi` der Solar API (ohne SoH und Zellspannungen). Messwerte landen alle 5 min in `battery_health_logs`, Ereignisse sofort in `battery_events`. Basis-URL über `FRONIUS_BASE_URL` (Default `http://192.168.123.79`).
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "README: Endpunkte und Datenquellen fuer Batterie-Zustand

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Nicht im Umfang

- Reaktion der KI-Steuerung auf Batteriemodus/Temperatur.
- Retention/Aufräumen alter Datensätze.
- Charts aus `battery_health_logs` (Daten liegen ab jetzt vor; später nachrüstbar).
- Docker-Compose: `FRONIUS_BASE_URL` muss dort nur gesetzt werden, wenn der WR nicht unter dem Default erreichbar ist; ob die PocketBase-Container-Konfiguration `pb_migrations` mountet, wurde nicht geprüft.
