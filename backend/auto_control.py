from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

SLOT_DURATION_HOURS = 0.25  # 15-Minuten-Slots (Open-Meteo Forecast-Raster)
CHARGE_EFFICIENCY = 0.95  # AC→DC Ladeverlust
HYSTERESIS_PCT = 3.0  # Modbus-Schreib-Schwelle in Prozentpunkten
NIGHT_GAP_SLOTS = 8  # 8 × 15 min = 2h Lücke → Sonnenuntergang erkannt


@dataclass
class ForecastSlot:
    time: str
    pv_w: float
    clipping_w: float
    clipping_kwh: float
    total_kwh: float
    charge_kwh: float = 0.0


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
    inwrte_pct: int         # Berechneter Zielwert (ganzzahlig)
    should_write: bool      # Ob Modbus geschrieben wurde
    reason: str             # Entscheidungsbegründung
    energy_needed_kwh: float
    total_clipping_kwh: float
    plan_summary: str


class AutoController:
    """Vorausschauende Batterie-Ladesteuerung basierend auf SOC-Trajektorie.

    Statt reaktiver Rückwärts-Allokation wird eine gleichmäßige SOC-Anstiegskurve
    vom aktuellen SOC auf 100% bis Sonnenuntergang berechnet. Ein SOC-abhängig
    gedämpfter Proportional-Regler fängt Echtzeit-Überschuss sanft ab.
    """

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
        """Prüft, ob der neue Wert geschrieben werden soll (asymmetrische Hysterese).

        Höhere Werte → sofort schreiben.
        Niedrigere Werte → nur bei ≥ HYSTERESIS_PCT Differenz.
        """
        if target_pct > self._current_inwrte:
            return True
        return (self._current_inwrte - target_pct) >= HYSTERESIS_PCT

    def _make_result(self, target_pct: int, should_write: bool, **kwargs) -> AutoControlResult:
        """Erzeugt AutoControlResult mit korrektem inwrte_pct.

        WICHTIG: Wenn should_write=False, wird der alte Wert (_current_inwrte)
        zurückgegeben, damit das Frontend keinen falschen neuen Wert anzeigt.
        """
        return AutoControlResult(
            inwrte_pct=target_pct if should_write else self._current_inwrte,
            should_write=should_write,
            **kwargs,
        )

    @staticmethod
    def _calc_damping(soc: float) -> float:
        """SOC-abhängiger Dämpfungsfaktor für den Echtzeit-Boost.

        SOC < 50%  → aggressiv (0.9): Batterie ist leer, Überschuss schnell abfangen.
        SOC 50-85% → linear abfallend (0.9 → 0.3): moderater werdende Korrektur.
        SOC > 85%  → sanft (0.3 → 0.05): Batterie fast voll, kaum noch nachsteuern.
        """
        if soc < 50:
            return 0.9
        elif soc < 85:
            # Linearer Übergang: 0.9 bei 50% → 0.3 bei 85%
            return 0.9 - (soc - 50) / (85 - 50) * 0.6
        else:
            # Linearer Übergang: 0.3 bei 85% → 0.05 bei 100%
            return max(0.05, 0.3 - (soc - 85) / (100 - 85) * 0.25)

    @staticmethod
    def _find_production_end(future_slots: list[ForecastSlot], now: datetime) -> datetime:
        """Findet das Ende der heutigen PV-Produktion (≈ Sonnenuntergang).

        Erkennt Sonnenuntergang an einer Lücke von ≥ 2h ohne Produktion.
        Gibt den Endzeitpunkt des letzten produktiven Slots zurück.
        """
        last_productive_time = now
        gap_count = 0
        found_production = False

        for slot in future_slots:
            if slot.pv_w > 0:
                try:
                    slot_time = datetime.strptime(slot.time, "%Y-%m-%dT%H:%M").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    continue
                last_productive_time = slot_time + timedelta(minutes=15)
                gap_count = 0
                found_production = True
            else:
                gap_count += 1
                if gap_count >= NIGHT_GAP_SLOTS and found_production:
                    return last_productive_time

        return last_productive_time

    # ------------------------------------------------------------------
    # Hauptlogik
    # ------------------------------------------------------------------

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
        # === Edge Case: Keine Batterie-Kapazität konfiguriert ===
        if battery_cap_kwh <= 0:
            target_pct = 100
            should_write = self._check_should_write(target_pct)
            return self._log_result(self._make_result(
                target_pct, should_write,
                reason="Keine Batteriekapazität konfiguriert — Ladung ohne Einschränkung.",
                energy_needed_kwh=0.0,
                total_clipping_kwh=0.0,
                plan_summary="Keine Steuerung möglich (Kapazität nicht konfiguriert)",
            ), soc, grid_power)

        # === Schritt 1 — Energiebedarf ===
        energy_needed_kwh = battery_cap_kwh * (1 - soc / 100)
        logger.info(
            "AutoControl: SOC=%.1f%%, Energiebedarf=%.2f kWh",
            soc, energy_needed_kwh,
        )

        # === Edge Case: Batterie bereits voll ===
        if energy_needed_kwh <= 0:
            target_pct = 0
            should_write = self._check_should_write(target_pct)
            return self._log_result(self._make_result(
                target_pct, should_write,
                reason="Batterie ist voll (SOC 100%) — Ladung gestoppt.",
                energy_needed_kwh=0.0,
                total_clipping_kwh=0.0,
                plan_summary="Batterie voll — keine Ladung nötig",
            ), soc, grid_power)

        # Kein Einspeiselimit konfiguriert → kein Clipping möglich
        if export_limit_w < 0:
            export_limit_w = float('inf')

        # === Schritt 2 — Forecast-Slots aufbereiten (nur Zukunft) ===
        now = datetime.now(timezone.utc)
        future_slots: list[ForecastSlot] = []

        for f in forecast:
            try:
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

        # === Edge Case: Kein Forecast verfügbar (Nacht, API-Fehler, etc.) ===
        if not future_slots:
            target_pct = 100
            should_write = self._check_should_write(target_pct)
            return self._log_result(self._make_result(
                target_pct, should_write,
                reason="Kein Forecast verfügbar — Batterie lädt ohne Einschränkung.",
                energy_needed_kwh=energy_needed_kwh,
                total_clipping_kwh=0.0,
                plan_summary="Kein Forecast — volle Ladung als Fallback",
            ), soc, grid_power)

        # === Schritt 3 — Vorab-Prüfung: Clipping vs. Bedarf ===
        clipping_covers_need = total_clipping_kwh >= energy_needed_kwh
        total_pv_kwh = sum(s.total_kwh for s in future_slots)
        not_enough_sun = total_pv_kwh < energy_needed_kwh

        # === Schritt 4 — SOC-Trajektorie berechnen ===
        #
        # Idee: Gleichmäßige Linie vom aktuellen SOC zu 100% bei Sonnenuntergang.
        # Daraus ergibt sich die benötigte Lade-Rate (%/h) und die Basis-Ladeleistung.
        #
        # Wenn das Clipping allein den Bedarf deckt, wird die Basis auf 0 gesetzt
        # und nur der gedämpfte Echtzeit-Boost fängt den Überschuss ab.
        #
        production_end = self._find_production_end(future_slots, now)
        hours_remaining = max(0.0, (production_end - now).total_seconds() / 3600)

        if hours_remaining > 0.1 and not not_enough_sun:
            # Gleichmäßige SOC-Steigung: aktueller SOC → 100% bis Sonnenuntergang
            soc_rate_per_hour = (100 - soc) / hours_remaining  # %/h

            # Benötigte AC-Leistung (W) für diese SOC-Rate:
            # DC-Energie/h = battery_cap_kwh × (soc_rate / 100)
            # AC-Leistung  = DC-Energie/h / CHARGE_EFFICIENCY × 1000
            base_power_w = (
                battery_cap_kwh * soc_rate_per_hour / 100
                / CHARGE_EFFICIENCY
                * 1000
            )

            base_pct = (base_power_w / wchamax_watt) * 100 if wchamax_watt > 0 else 100
        elif not_enough_sun:
            # Nicht genug Sonne → maximale Ladung, jede kWh zählt
            base_pct = 100.0
            soc_rate_per_hour = 0.0
        else:
            # Kaum noch Zeit (< 6 min) → maximale Ladung
            base_pct = 100.0
            soc_rate_per_hour = 0.0

        # Clipping deckt den Bedarf → Basis streng auf 0%, nur Überschuss abfangen
        if clipping_covers_need and not not_enough_sun:
            base_pct = 0.0

        base_pct = max(0.0, min(100.0, base_pct))

        # === Schritt 5 — Gedämpfte Echtzeit-Korrektur (Proportional-Regler) ===
        #
        # Wenn die Netzeinspeisung über dem Limit liegt, wird ein Boost aufgerechnet.
        # Der Boost wird mit einem SOC-abhängigen Faktor gedämpft:
        #   - Batterie leer  → aggressiverer Boost (viel Platz)
        #   - Batterie voll  → sanfter Boost (fast kein Platz, Spitzen vermeiden)
        #
        target_pct = base_pct
        boost_pct = 0.0
        damping = self._calc_damping(soc)
        actual_export = max(0.0, -grid_power)

        if actual_export > export_limit_w and export_limit_w > 0 and wchamax_watt > 0:
            overshoot_w = actual_export - export_limit_w
            raw_boost_pct = (overshoot_w / wchamax_watt) * 100
            boost_pct = raw_boost_pct * damping
            target_pct = min(100.0, target_pct + boost_pct)

        # === Ganzzahlig runden — Fronius WR akzeptiert nur int-Werte ===
        target_pct = max(0, min(100, round(target_pct)))

        # === Schritt 6 — Hysterese (hoch → sofort, runter → mit Schwelle) ===
        should_write = self._check_should_write(target_pct)

        # === Reason & Plan-Summary aufbauen ===
        reason = self._build_reason(
            soc=soc,
            target_pct=target_pct,
            base_pct=round(base_pct),
            boost_pct=boost_pct,
            damping=damping,
            actual_export=actual_export,
            export_limit_w=export_limit_w,
            clipping_covers_need=clipping_covers_need,
            not_enough_sun=not_enough_sun,
            hours_remaining=hours_remaining,
            soc_rate_per_hour=soc_rate_per_hour,
            energy_needed_kwh=energy_needed_kwh,
            total_clipping_kwh=total_clipping_kwh,
            total_pv_kwh=total_pv_kwh,
            should_write=should_write,
        )

        plan_summary = self._build_plan_summary(
            soc=soc,
            hours_remaining=hours_remaining,
            soc_rate_per_hour=soc_rate_per_hour,
            energy_needed_kwh=energy_needed_kwh,
            total_clipping_kwh=total_clipping_kwh,
            total_pv_kwh=total_pv_kwh,
            clipping_covers_need=clipping_covers_need,
            not_enough_sun=not_enough_sun,
        )

        logger.info(
            "AutoControl: target=%d%% base=%.0f%% boost=%.1f%% damping=%.0f%% | %s",
            target_pct, base_pct, boost_pct, damping * 100, plan_summary,
        )

        return self._log_result(self._make_result(
            target_pct, should_write,
            reason=reason,
            energy_needed_kwh=energy_needed_kwh,
            total_clipping_kwh=total_clipping_kwh,
            plan_summary=plan_summary,
        ), soc, grid_power)

    # ------------------------------------------------------------------
    # Reason / Plan-Summary Builder
    # ------------------------------------------------------------------

    def _build_reason(
        self, *, soc, target_pct, base_pct, boost_pct, damping,
        actual_export, export_limit_w, clipping_covers_need,
        not_enough_sun, hours_remaining, soc_rate_per_hour,
        energy_needed_kwh, total_clipping_kwh, total_pv_kwh,
        should_write,
    ) -> str:
        # Hysterese hat gegriffen → Schreiben übersprungen
        if not should_write:
            delta = self._current_inwrte - target_pct
            return (
                f"Berechneter Wert ({target_pct}%) ist nicht höher und weicht weniger als "
                f"{HYSTERESIS_PCT:.0f}% vom aktuellen ({self._current_inwrte}%) ab "
                f"(Δ {delta}%). Modbus-Schreibbefehl übersprungen."
            )

        parts = []

        # Hauptgrund: Warum wurde dieser Basiswert gewählt?
        if not_enough_sun:
            parts.append(
                f"Nicht genug Sonne für volle Ladung! "
                f"Nur {total_pv_kwh:.1f} kWh verfügbar, "
                f"aber {energy_needed_kwh:.1f} kWh benötigt "
                f"→ volle Ladeleistung ({target_pct}%)."
            )
        elif clipping_covers_need:
            parts.append(
                f"Clipping-Energie reicht aus ({total_clipping_kwh:.1f} kWh ≥ "
                f"{energy_needed_kwh:.1f} kWh Bedarf) "
                f"→ Basis-Ladung 0%, nur Überschuss wird abgefangen."
            )
        else:
            parts.append(
                f"SOC-Trajektorie: {soc:.0f}% → 100% in {hours_remaining:.1f}h "
                f"(+{soc_rate_per_hour:.1f}%/h) → Basis-Ladung {base_pct}%."
            )

        # Boost-Info (nur wenn tatsächlich aktiv)
        if boost_pct > 0.5:
            overshoot_w = actual_export - export_limit_w
            parts.append(
                f"Einspeisung liegt {overshoot_w:.0f} W über Limit "
                f"→ gedämpfter Boost +{boost_pct:.0f}% "
                f"(Dämpfung {damping * 100:.0f}% bei SOC {soc:.0f}%)."
            )

        return " ".join(parts)

    @staticmethod
    def _build_plan_summary(
        *, soc, hours_remaining, soc_rate_per_hour,
        energy_needed_kwh, total_clipping_kwh, total_pv_kwh,
        clipping_covers_need, not_enough_sun,
    ) -> str:
        if not_enough_sun:
            return (
                f"Nicht genug Sonne! Bedarf: {energy_needed_kwh:.1f} kWh, "
                f"verfügbar: {total_pv_kwh:.1f} kWh"
            )

        mode = "Nur Clipping" if clipping_covers_need else "Trajektorie"
        return (
            f"{mode}: {soc:.0f}% → 100% in {hours_remaining:.1f}h "
            f"(+{soc_rate_per_hour:.1f}%/h) | "
            f"Bedarf: {energy_needed_kwh:.1f} kWh, "
            f"Clipping: {total_clipping_kwh:.1f} kWh"
        )

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

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
