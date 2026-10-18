from datetime import datetime, timedelta, timezone

from auto_control import AutoController

CAPACITY_KWH = 10.0
WCHAMAX_W = 10_000


def make_forecast(pv_kw: float, productive_hours: int = 8) -> list[dict]:
    """Forecast ab dem aktuellen 15-min-Slot: konstante PV-Leistung, danach Nacht."""
    now = datetime.now(timezone.utc)
    start = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    slots = []
    for i in range(productive_hours * 4 + 12):  # 12 leere Slots = Sonnenuntergang
        t = start + timedelta(minutes=15 * i)
        kw = pv_kw if i < productive_hours * 4 else 0.0
        slots.append({"time": t.strftime("%Y-%m-%dT%H:%M"), "expected_kw": kw})
    return slots


def run(export_limit_w: float, grid_power: float = 0.0, pv_kw: float = 10.0, soc: float = 50.0):
    return AutoController().run_cycle(
        soc=soc,
        grid_power=grid_power,
        forecast=make_forecast(pv_kw),
        battery_cap_kwh=CAPACITY_KWH,
        wchamax_watt=WCHAMAX_W,
        export_limit_w=export_limit_w,
        safety_factor=0.8,
    )


def test_zero_limit_means_no_clipping():
    result = run(export_limit_w=0)

    assert result.total_clipping_kwh == 0.0
    assert "SOC-Trajektorie" in result.reason
    assert 0 < result.inwrte_pct < 100


def test_zero_limit_ignores_grid_export():
    quiet = run(export_limit_w=0, grid_power=0)
    exporting = run(export_limit_w=0, grid_power=-8000)

    assert exporting.inwrte_pct == quiet.inwrte_pct


def test_clipping_covers_need_sets_base_to_zero():
    result = run(export_limit_w=3000)

    assert "Clipping-Energie reicht" in result.reason
    assert result.inwrte_pct == 0


def test_export_over_limit_adds_damped_boost():
    # 2000 W über Limit → 20 % von WChaMax, gedämpft mit 0.9 bei SOC 50 %
    result = run(export_limit_w=3000, grid_power=-5000)

    assert result.inwrte_pct == 18


def test_not_enough_sun_charges_fully():
    result = run(export_limit_w=3000, pv_kw=0.1)

    assert "Nicht genug Sonne" in result.plan_summary
    assert result.inwrte_pct == 100
