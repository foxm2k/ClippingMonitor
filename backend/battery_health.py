from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

WS_PER_KWH = 3_600_000  # Fronius-Energiezaehler liefern Wattsekunden

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
