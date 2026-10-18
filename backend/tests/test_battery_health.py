import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from battery_health import (
    COMPONENTS_PATH,
    DB_FIELDS,
    HEALTH_KEYS,
    INVERTER_INFO_PATH,
    STORAGE_PATH,
    BatteryEventDetector,
    PersistThrottle,
    db_record,
    fetch_battery_health,
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
