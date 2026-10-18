import json
from pathlib import Path

import pytest

from battery_health import (
    HEALTH_KEYS,
    parse_components,
    parse_inverter_info,
    parse_powerflow_battery,
    parse_storage_realtime,
    soh_level,
    spread_level,
    temp_level,
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
