import logging
from collections import deque
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
    inwrte_pct: int
    should_write: bool
    reason: str
    energy_needed_kwh: float
    total_clipping_kwh: float
    plan_summary: str


@dataclass
class AutoControlLogEntry:
    timestamp: str          # UTC ISO-8601
    soc: float              # SOC zum Zeitpunkt der Entscheidung
    grid_power: float       # Grid-Power zum Zeitpunkt
    inwrte_pct: int          # Berechneter Zielwert (ganzzahlig)
    should_write: bool      # Ob Modbus geschrieben wurde
    reason: str             # Entscheidungsbegründung
    energy_needed_kwh: float
    total_clipping_kwh: float
    plan_summary: str


class AutoController:
    """Intelligente Batterie-Ladesteuerung basierend auf PV-Forecast."""

    def __init__(self):
        self._current_inwrte: int = 100  # Letzter geschriebener Wert (ganzzahlig)
        self._log: deque[AutoControlLogEntry] = deque(maxlen=200)

    def update_current_inwrte(self, pct: int):
        """Wird nach erfolgreichem Modbus-Write aufgerufen."""
        self._current_inwrte = int(pct)

    def get_log(self, limit: int = 120) -> list[dict]:
        """Gibt die letzten `limit` Einträge zurück, neueste zuerst."""
        entries = list(self._log)[-limit:]
        entries.reverse()
        return [
            {
                "timestamp": e.timestamp,
                "soc": e.soc,
                "grid_power": e.grid_power,
                "inwrte_pct": e.inwrte_pct,
                "should_write": e.should_write,
                "reason": e.reason,
                "energy_needed_kwh": e.energy_needed_kwh,
                "total_clipping_kwh": e.total_clipping_kwh,
                "plan_summary": e.plan_summary,
            }
            for e in entries
        ]

    def _log_result(
        self, result: AutoControlResult, soc: float, grid_power: float
    ) -> AutoControlResult:
        """Protokolliert ein AutoControlResult im Ringbuffer und gibt es zurück."""
        self._log.append(AutoControlLogEntry(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            soc=soc,
            grid_power=grid_power,
            inwrte_pct=result.inwrte_pct,
            should_write=result.should_write,
            reason=result.reason,
            energy_needed_kwh=round(result.energy_needed_kwh, 2),
            total_clipping_kwh=round(result.total_clipping_kwh, 2),
            plan_summary=result.plan_summary,
        ))
        return result

    def _check_should_write(self, target_pct: int) -> bool:
        """Prüft, ob der neue Wert geschrieben werden soll (asymmetrische Hysterese)."""
        # Höhere Werte sofort schreiben
        if target_pct > self._current_inwrte:
            return True
        # Bei niedrigeren Werten muss die Hysterese greifen
        return (self._current_inwrte - target_pct) >= HYSTERESIS_PCT

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
            return self._log_result(AutoControlResult(
                inwrte_pct=100,
                should_write=self._check_should_write(100),
                reason="Keine Batteriekapazität konfiguriert — Ladung ohne Einschränkung.",
                energy_needed_kwh=0.0,
                total_clipping_kwh=0.0,
                plan_summary="Keine Steuerung möglich (Kapazität nicht konfiguriert)",
            ), soc, grid_power)

        # Schritt 1 — Energiebedarf
        energy_needed_kwh = battery_cap_kwh * (1 - soc / 100)
        logger.info(
            "AutoControl: SOC=%.1f%%, Energiebedarf=%.2f kWh",
            soc, energy_needed_kwh,
        )

        # Edge Case: Batterie bereits voll
        if energy_needed_kwh <= 0:
            target_pct = 0
            should_write = self._check_should_write(target_pct)
            return self._log_result(AutoControlResult(
                inwrte_pct=target_pct,
                should_write=should_write,
                reason="Batterie ist voll (SOC 100%) — Ladung gestoppt.",
                energy_needed_kwh=0.0,
                total_clipping_kwh=0.0,
                plan_summary="Batterie voll — keine Ladung nötig",
            ), soc, grid_power)

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
            target_pct = 100
            should_write = self._check_should_write(target_pct)
            return self._log_result(AutoControlResult(
                inwrte_pct=target_pct,
                should_write=should_write,
                reason="Kein Forecast verfügbar — Batterie lädt ohne Einschränkung.",
                energy_needed_kwh=energy_needed_kwh,
                total_clipping_kwh=0.0,
                plan_summary="Kein Forecast — volle Ladung als Fallback",
            ), soc, grid_power)

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
            target_pct = 100
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
                target_pct = 100

            target_pct = max(0, min(100, target_pct))
            plan_detail = (
                f"Slot {current_slot.time}: "
                f"charge={current_slot.charge_kwh:.2f}kWh → {target_w:.0f}W → {target_pct:.0f}%"
            )

        plan_pct = target_pct  # bereits ganzzahlig

        # Schritt 6 — Echtzeit-Korrektur (reaktiv)
        boost_pct = 0.0
        actual_export = max(0.0, -grid_power)
        if actual_export > export_limit_w and export_limit_w > 0:
            overshoot_w = actual_export - export_limit_w
            boost_pct = (overshoot_w / wchamax_watt) * 100 if wchamax_watt > 0 else 0.0
            target_pct = min(100, target_pct + boost_pct)

        # Ganzzahlig runden – Fronius WR akzeptiert nur int-Werte
        target_pct = round(target_pct)

        # Schritt 7 — Hysterese (asymmetrisch: hoch → sofort, runter → mit Schwelle)
        should_write = self._check_should_write(target_pct)

        # Reason-String zusammenbauen (menschenlesbar)
        total_planned = sum(s.charge_kwh for s in future_slots)

        if not should_write:
            delta = self._current_inwrte - target_pct
            reason = (
                f"Berechneter Wert ({target_pct}%) ist nicht höher und weicht weniger als "
                f"{HYSTERESIS_PCT:.0f}% vom aktuellen ({self._current_inwrte:.0f}%) ab "
                f"(Δ {delta:.0f}%). Modbus-Schreibbefehl übersprungen."
            )
        elif boost_pct > 0 and plan_pct <= 0:
            reason = (
                f"Laut Plan wäre hier keine Ladung nötig (0%) — "
                f"spätere Zeitfenster reichen aus. "
                f"ABER: Einspeisung liegt gerade ~{actual_export - export_limit_w:.0f} W "
                f"über dem Limit → Sofort-Korrektur um +{round(boost_pct)}%, "
                f"damit der Überschuss in die Batterie fließt statt ins Netz."
            )
        elif boost_pct > 0:
            reason = (
                f"Laut Plan soll die Batterie mit {round(plan_pct)}% laden. "
                f"Zusätzlich liegt die Einspeisung ~{actual_export - export_limit_w:.0f} W "
                f"über dem Limit → Boost um +{round(boost_pct)}% auf insgesamt {target_pct}%."
            )
        elif not_enough_sun:
            reason = (
                f"Nicht genug Sonne für die volle Ladung! "
                f"Nur {total_planned:.1f} kWh verfügbar, "
                f"aber {energy_needed_kwh:.1f} kWh benötigt. "
                f"Batterie lädt mit maximaler Leistung."
            )
        else:
            reason = (
                f"Laut Plan soll die Batterie in diesem Zeitfenster "
                f"mit {round(plan_pct)}% der max. Ladeleistung laden. "
                f"Genug Clipping-Energie vorhanden "
                f"({total_clipping_kwh:.1f} kWh verfügbar, "
                f"{energy_needed_kwh:.1f} kWh benötigt)."
            )

        # Plan-Summary (Kurzfassung)
        if not_enough_sun:
            plan_summary = (
                f"Nicht genug Sonne! Bedarf: {energy_needed_kwh:.1f} kWh, "
                f"verfügbar: {total_planned:.1f} kWh "
                f"(Clipping: {total_clipping_kwh:.1f} kWh)"
            )
        else:
            plan_summary = (
                f"Bedarf: {energy_needed_kwh:.1f} kWh, "
                f"geplant: {total_planned:.1f} kWh "
                f"(Clipping: {total_clipping_kwh:.1f} kWh)"
            )

        logger.info("AutoControl: %s | %s | %s", reason, plan_detail, plan_summary)

        return self._log_result(AutoControlResult(
            inwrte_pct=target_pct if should_write else self._current_inwrte,
            should_write=should_write,
            reason=reason,
            energy_needed_kwh=energy_needed_kwh,
            total_clipping_kwh=total_clipping_kwh,
            plan_summary=plan_summary,
        ), soc, grid_power)

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
