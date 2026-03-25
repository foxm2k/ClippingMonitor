import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

SLOT_DURATION_HOURS = 0.25  # 15-Minuten-Slots (Open-Meteo Forecast-Raster)
CHARGE_EFFICIENCY = 0.95  # AC→DC Ladeverlust
HYSTERESIS_PCT = 3.0  # Modbus-Schreib-Schwelle in Prozentpunkten


@dataclass
class ForecastSlot:
    time: str
    pv_w: float
    clipping_w: float
    clipping_kwh: float
    total_kwh: float
    charge_kwh: float = 0.0  # wird durch Allokation befüllt


@dataclass
class AutoControlResult:
    inwrte_pct: float
    should_write: bool
    reason: str
    energy_needed_kwh: float
    total_clipping_kwh: float
    plan_summary: str


class AutoController:
    """Intelligente Batterie-Ladesteuerung basierend auf PV-Forecast."""

    def __init__(self):
        self._current_inwrte: float = 100.0  # Letzter geschriebener Wert

    def update_current_inwrte(self, pct: float):
        """Wird nach erfolgreichem Modbus-Write aufgerufen."""
        self._current_inwrte = pct

    def run_cycle(
        self,
        soc: float,
        grid_power: float,
        forecast: list[dict],  # [{time: str, expected_kw: float}, ...]
        battery_cap_kwh: float,
        wchamax_watt: float,
        export_limit_w: float,
        safety_factor: float = 0.80,
    ) -> AutoControlResult:
        # Edge Case: Kein Batterie-Kapazität konfiguriert
        if battery_cap_kwh <= 0:
            return AutoControlResult(
                inwrte_pct=100.0,
                should_write=abs(100.0 - self._current_inwrte) >= HYSTERESIS_PCT,
                reason="Keine Batteriekapazität konfiguriert → InWRte=100%",
                energy_needed_kwh=0.0,
                total_clipping_kwh=0.0,
                plan_summary="Keine Steuerung (battery_cap_kwh=0)",
            )

        # Schritt 1 — Energiebedarf
        energy_needed_kwh = battery_cap_kwh * (1 - soc / 100)
        logger.info(
            "AutoControl: SOC=%.1f%%, Energiebedarf=%.2f kWh",
            soc, energy_needed_kwh,
        )

        # Edge Case: Batterie bereits voll
        if energy_needed_kwh <= 0:
            target_pct = 0.0
            should_write = abs(target_pct - self._current_inwrte) >= HYSTERESIS_PCT
            return AutoControlResult(
                inwrte_pct=target_pct,
                should_write=should_write,
                reason="SOC=100% → InWRte=0%",
                energy_needed_kwh=0.0,
                total_clipping_kwh=0.0,
                plan_summary="Batterie voll, Ladung gestoppt",
            )

        # Kein Einspeiselimit konfiguriert → kein Clipping möglich
        if export_limit_w <= 0:
            export_limit_w = float('inf')

        # Schritt 2 — Forecast-Slots aufbereiten (nur Zukunft)
        now = datetime.now(timezone.utc)
        future_slots: list[ForecastSlot] = []

        for f in forecast:
            try:
                # Open-Meteo-Format: "2024-03-22T10:00" (ohne Z)
                slot_time = datetime.strptime(f["time"], "%Y-%m-%dT%H:%M").replace(
                    tzinfo=timezone.utc
                )
            except (ValueError, KeyError):
                continue

            slot_end = slot_time + timedelta(minutes=15)
            if slot_end <= now:
                continue

            pv_w = f.get("expected_kw", 0.0) * 1000 * safety_factor
            clipping_w = max(0.0, pv_w - export_limit_w)
            clipping_kwh = clipping_w / 1000 * SLOT_DURATION_HOURS * CHARGE_EFFICIENCY
            total_kwh = pv_w / 1000 * SLOT_DURATION_HOURS * CHARGE_EFFICIENCY

            future_slots.append(
                ForecastSlot(
                    time=f["time"],
                    pv_w=pv_w,
                    clipping_w=clipping_w,
                    clipping_kwh=clipping_kwh,
                    total_kwh=total_kwh,
                )
            )

        total_clipping_kwh = sum(s.clipping_kwh for s in future_slots)

        # Edge Case: Kein Forecast verfügbar (Nacht, API-Fehler, etc.)
        if not future_slots:
            target_pct = 100.0
            should_write = abs(target_pct - self._current_inwrte) >= HYSTERESIS_PCT
            return AutoControlResult(
                inwrte_pct=target_pct,
                should_write=should_write,
                reason="Kein Forecast → InWRte=100%",
                energy_needed_kwh=energy_needed_kwh,
                total_clipping_kwh=0.0,
                plan_summary="Kein Forecast verfügbar, volle Ladung",
            )

        # Schritt 3 — Rückwärts-Allokation: Clipping-Energie zuerst
        remaining = energy_needed_kwh
        for slot in reversed(future_slots):
            alloc = min(remaining, slot.clipping_kwh)
            slot.charge_kwh = alloc
            remaining -= alloc
            if remaining <= 0:
                break

        # Schritt 4 — Falls remaining > 0, zweiter Rückwärts-Durchlauf (normaler Überschuss)
        if remaining > 0:
            for slot in reversed(future_slots):
                headroom = slot.total_kwh - slot.charge_kwh
                additional = min(remaining, headroom)
                slot.charge_kwh += additional
                remaining -= additional
                if remaining <= 0:
                    break

        # Falls remaining > 0: Nicht genug Sonne → alle Slots auf Maximum
        not_enough_sun = remaining > 0
        if not_enough_sun:
            for slot in future_slots:
                slot.charge_kwh = slot.total_kwh

        # Schritt 5 — InWRte für aktuellen Slot bestimmen
        current_slot = self._find_current_slot(future_slots, now)

        if current_slot is None:
            target_pct = 100.0
            plan_detail = "Kein aktiver Slot"
        else:
            if SLOT_DURATION_HOURS > 0 and CHARGE_EFFICIENCY > 0:
                target_w = (
                    current_slot.charge_kwh
                    / SLOT_DURATION_HOURS
                    / CHARGE_EFFICIENCY
                    * 1000
                )
            else:
                target_w = 0.0

            if wchamax_watt > 0:
                target_pct = (target_w / wchamax_watt) * 100
            else:
                target_pct = 100.0

            target_pct = max(0.0, min(100.0, target_pct))
            plan_detail = (
                f"Slot {current_slot.time}: "
                f"charge={current_slot.charge_kwh:.2f}kWh → {target_w:.0f}W → {target_pct:.1f}%"
            )

        plan_pct = target_pct

        # Schritt 6 — Echtzeit-Korrektur (reaktiv)
        boost_pct = 0.0
        actual_export = max(0.0, -grid_power)
        if actual_export > export_limit_w and export_limit_w > 0:
            overshoot_w = actual_export - export_limit_w
            boost_pct = (overshoot_w / wchamax_watt) * 100 if wchamax_watt > 0 else 0.0
            target_pct = min(100.0, target_pct + boost_pct)

        # Schritt 7 — Hysterese
        should_write = abs(target_pct - self._current_inwrte) >= HYSTERESIS_PCT

        # Reason-String zusammenbauen
        reason_parts = [f"Plan: {plan_pct:.1f}%"]
        if boost_pct > 0:
            reason_parts.append(f"Boost: +{boost_pct:.1f}%")
        reason_parts.append(f"Final: {target_pct:.1f}%")
        if not should_write:
            reason_parts.append(f"(keine Änderung, Δ={abs(target_pct - self._current_inwrte):.1f}% < {HYSTERESIS_PCT}%)")
        reason = ", ".join(reason_parts)

        # Plan-Summary
        total_planned = sum(s.charge_kwh for s in future_slots)
        if not_enough_sun:
            plan_summary = (
                f"Nicht genug Sonne! Bedarf={energy_needed_kwh:.1f}kWh, "
                f"verfügbar={total_planned:.1f}kWh (Clipping={total_clipping_kwh:.1f}kWh)"
            )
        else:
            plan_summary = (
                f"Bedarf={energy_needed_kwh:.1f}kWh, "
                f"geplant={total_planned:.1f}kWh (Clipping={total_clipping_kwh:.1f}kWh)"
            )

        logger.info("AutoControl: %s | %s | %s", reason, plan_detail, plan_summary)

        return AutoControlResult(
            inwrte_pct=round(target_pct, 1),
            should_write=should_write,
            reason=reason,
            energy_needed_kwh=energy_needed_kwh,
            total_clipping_kwh=total_clipping_kwh,
            plan_summary=plan_summary,
        )

    @staticmethod
    def _find_current_slot(
        slots: list[ForecastSlot], now: datetime
    ) -> ForecastSlot | None:
        """Findet den Slot, in dessen 15-Minuten-Zeitfenster `now` fällt."""
        slot_duration = timedelta(minutes=15)
        for slot in slots:
            try:
                slot_start = datetime.strptime(slot.time, "%Y-%m-%dT%H:%M").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
            if slot_start <= now < slot_start + slot_duration:
                return slot
        return None
