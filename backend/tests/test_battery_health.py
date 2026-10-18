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
        ("standby", "info", "nein", "ja"),
        ("backup", "warn", "nein", "ja"),
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


def run_fetch(routes: dict, powerflow=None) -> dict:
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(request.url.path, (404, {}))
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    pf = load("powerflow.json") if powerflow is None else powerflow

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_battery_health(client, "http://wr", pf)

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


# ---------------------------------------------------------------------------
# Review-Fixes: Ereignis-Robustheit
# ---------------------------------------------------------------------------

def test_change_across_missing_value_is_not_lost():
    d = BatteryEventDetector()
    d.process(snap())
    assert d.process(snap(inverter_error_code=None)) == []
    assert summary(d.process(snap(inverter_error_code=567))) == [("error", "crit", "0", "567")]


def test_detector_does_not_alias_callers_dict():
    d = BatteryEventDetector()
    first = snap()
    d.process(first)
    first["battery_mode"] = "service"  # nachtraegliche Aenderung darf nichts beeinflussen
    assert summary(d.process(snap(battery_mode="service"))) == [("mode", "warn", "normal", "service")]


def test_key_first_seen_later_is_baseline():
    d = BatteryEventDetector()
    d.process(snap(inverter_error_code=None))
    assert d.process(snap(inverter_error_code=567)) == []
    assert summary(d.process(snap(inverter_error_code=0))) == [("error", "info", "567", "0")]


def test_critical_to_warn_step():
    d = BatteryEventDetector()
    assert summary(d.process(snap(temp_cell_max=51.0))) == [("temperature", "crit", "ok", "crit")]
    assert summary(d.process(snap(temp_cell_max=48.0))) == [("temperature", "warn", "crit", "warn")]


def test_low_side_temperature_hysteresis_in_detector():
    d = BatteryEventDetector()
    assert summary(d.process(snap(temp_cell_min=4.5))) == [("temperature", "warn", "ok", "warn")]
    assert d.process(snap(temp_cell_min=5.5)) == []
    assert summary(d.process(snap(temp_cell_min=6.1))) == [("temperature", "info", "warn", "ok")]


def test_temperature_message_with_only_temp_cell():
    events = BatteryEventDetector().process(
        snap(temp_cell=41.0, temp_cell_min=None, temp_cell_max=None)
    )
    assert events[0].message == "Zelltemperatur 41.0 °C: Warnbereich"


def test_spread_hysteresis_in_detector():
    d = BatteryEventDetector()
    assert summary(d.process(snap(cell_spread_mv=50.0))) == [("cell_spread", "warn", "ok", "warn")]
    assert d.process(snap(cell_spread_mv=49.0)) == []
    assert summary(d.process(snap(cell_spread_mv=44.9))) == [("cell_spread", "info", "warn", "ok")]


def test_soh_hysteresis_in_detector():
    d = BatteryEventDetector()
    assert summary(d.process(snap(soh=79.9))) == [("soh", "warn", "ok", "warn")]
    assert d.process(snap(soh=80.2)) == []
    assert summary(d.process(snap(soh=80.5))) == [("soh", "info", "warn", "ok")]


def test_spread_and_soh_level_hysteresis():
    assert spread_level(49.0, prev="warn") == "warn"
    assert spread_level(44.9, prev="warn") == "ok"
    assert spread_level(96.0, prev="crit") == "crit"
    assert spread_level(94.0, prev="crit") == "warn"
    assert spread_level(120.0, prev="warn") == "crit"
    assert soh_level(80.2, prev="warn") == "warn"
    assert soh_level(80.5, prev="warn") == "ok"


def test_event_is_immutable():
    events = BatteryEventDetector().process(snap(soh=79.0))
    with pytest.raises(Exception):
        events[0].severity = "info"


# ---------------------------------------------------------------------------
# Review-Fixes: Parser-Haertung
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    {"Body": {"Data": {"Site": None, "Inverters": {"1": {"Battery_Mode": "normal"}}}}},
    {"Body": {"Data": {"Site": {"BatteryStandby": True}, "Inverters": {"1": None}}}},
    {"Body": {"Data": {"Site": {"BatteryStandby": True}, "Inverters": None}}},
    {"Body": {"Data": []}},
    {"Body": None},
    [],
    None,
    "kaputt",
])
def test_parse_powerflow_battery_survives_malformed_input(raw):
    result = parse_powerflow_battery(raw)
    assert set(result) == {"battery_mode", "battery_standby", "backup_mode"}


def test_parse_powerflow_battery_partial_values_survive():
    raw = {"Body": {"Data": {"Site": None, "Inverters": {"1": {"Battery_Mode": "normal"}}}}}
    assert parse_powerflow_battery(raw)["battery_mode"] == "normal"


@pytest.mark.parametrize("parser", [parse_components, parse_storage_realtime, parse_inverter_info])
@pytest.mark.parametrize("raw", [[], None, "x", {"Body": []}, {"Body": {"Data": "x"}}])
def test_other_parsers_raise_valueerror_on_garbage(parser, raw):
    with pytest.raises(ValueError):
        parser(raw)


def test_storage_parser_rejects_null_entries():
    with pytest.raises(ValueError):
        parse_storage_realtime({"Body": {"Data": {"0": None}}})
    with pytest.raises(ValueError):
        parse_storage_realtime({"Body": {"Data": {"0": {"Controller": None}}}})


def test_inverter_parser_rejects_null_entry():
    with pytest.raises(ValueError):
        parse_inverter_info({"Body": {"Data": {"1": None}}})


def components_with(**storage_overrides) -> dict:
    raw = load("components_readable.json")
    raw["Body"]["Data"]["Fronius_Storage_test"]["channels"].update(storage_overrides)
    return raw


def test_nameplate_as_dict():
    raw = load("components_readable.json")
    attrs = raw["Body"]["Data"]["Fronius_Storage_test"]["attributes"]
    attrs["nameplate"] = {"capacity_wh": 15795, "tCellMax": 56.0, "tCellMin": -20.0}
    h = parse_components(raw)
    assert h["capacity_wh"] == 15795
    assert h["temp_limit_max"] == 56.0


@pytest.mark.parametrize("nameplate", ["null", "[1, 2]", "kein json", "", None, 42, "{bad"])
def test_nameplate_unusable_gives_empty_limits(nameplate):
    raw = load("components_readable.json")
    raw["Body"]["Data"]["Fronius_Storage_test"]["attributes"]["nameplate"] = nameplate
    h = parse_components(raw)
    assert h["capacity_wh"] is None
    assert h["temp_limit_min"] is None and h["temp_limit_max"] is None
    assert h["cycles"] is None
    assert h["soc"] == 15.8  # Rest wird weiter geparst


def test_non_numeric_channel_value_only_affects_that_field():
    h = parse_components(components_with(BAT_TEMPERATURE_CELL_F64="N/A"))
    assert h["temp_cell"] is None
    assert h["temp_cell_min"] == 19.3
    assert h["soc"] == 15.8


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_become_none(bad):
    h = parse_components(components_with(
        BAT_VOLTAGE_DC_INTERNAL_F64=bad,
        BAT_MODE_CELL_STATE_U16=bad,
        BAT_ENERGYACTIVE_LIFETIME_DISCHARGED_F64=bad,
    ))
    assert h["voltage_dc"] is None
    assert h["cell_state"] is None
    assert h["lifetime_discharged_kwh"] is None
    assert h["cycles"] is None
    assert h["soc"] == 15.8


def test_non_numeric_module_voltage_is_ignored():
    raw = load("components_readable.json")
    raw["Body"]["Data"]["Fronius_BatteryModule_a"]["channels"]["BATMODULE_VOLTAGE_CELL_MIN_F32"] = "N/A"
    h = parse_components(raw)
    assert h["cell_voltage_min"] == 3.316
    assert h["cell_voltage_max"] == 3.322


def test_enable_attribute_may_be_boolean():
    raw = load("components_readable.json")
    for comp in raw["Body"]["Data"].values():
        attrs = comp.get("attributes", {})
        if attrs.get("[ENABLE]") == "true":
            attrs["[ENABLE]"] = True
        elif attrs.get("[ENABLE]") == "false":
            attrs["[ENABLE]"] = False
    h = parse_components(raw)
    assert h["module_count"] == 2
    assert h["soh"] == 99.7


def test_capacity_as_string_is_numeric_and_consistent():
    raw = load("components_readable.json")
    attrs = raw["Body"]["Data"]["Fronius_Storage_test"]["attributes"]
    attrs["nameplate"] = json.dumps({"capacity_wh": "15795"})
    h = parse_components(raw)
    assert h["capacity_wh"] == 15795.0
    assert isinstance(h["capacity_wh"], float)
    assert h["cycles"] == 191.6
    fallback = parse_storage_realtime(load("storage_realtime.json"))
    assert isinstance(fallback["capacity_wh"], float)


@pytest.mark.parametrize("capacity", [0, None, "0"])
def test_zero_or_missing_capacity_gives_no_cycles(capacity):
    raw = load("components_readable.json")
    attrs = raw["Body"]["Data"]["Fronius_Storage_test"]["attributes"]
    attrs["nameplate"] = json.dumps({"capacity_wh": capacity})
    assert parse_components(raw)["cycles"] is None


def test_soh_falls_back_when_stack_channel_is_null():
    raw = load("components_readable.json")
    raw["Body"]["Data"]["Fronius_BatteryStack_test"]["channels"]["BATSTACK_PERCENT_SOH_F32"] = None
    assert parse_components(raw)["soh"] == 99.0


# ---------------------------------------------------------------------------
# Review-Fixes: Abruf
# ---------------------------------------------------------------------------

def test_fetch_falls_back_when_components_returns_garbage():
    s = run_fetch({
        COMPONENTS_PATH: (200, "<html>Login erforderlich</html>"),
        STORAGE_PATH: (200, load("storage_realtime.json")),
    })
    assert s["source"] == "solar_api"
    assert s["temp_cell"] == 19.5


@pytest.mark.parametrize("pf", [
    {"Body": {"Data": {"Site": None}}},
    {"Body": {"Data": {"Inverters": {"1": None}}}},
    [],
])
def test_fetch_never_raises_on_malformed_powerflow(pf):
    s = run_fetch({COMPONENTS_PATH: (200, load("components_readable.json"))}, powerflow=pf)
    assert s["source"] == "components"
    assert s["battery_mode"] is None
    assert set(DB_FIELDS) <= set(s)
