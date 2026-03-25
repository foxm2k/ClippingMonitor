# ☀️ PV Smart Control

Intelligentes PV-Monitoring und Batterie-Steuerungssystem für Fronius-Wechselrichter mit automatischer Ladeoptimierung.

![Tech Stack](https://img.shields.io/badge/React-TypeScript-blue) ![Backend](https://img.shields.io/badge/FastAPI-Python-green) ![Database](https://img.shields.io/badge/PocketBase-SQLite-orange) ![Protocol](https://img.shields.io/badge/Modbus_TCP-SunSpec-red)

---

## Was macht das System?

PV Smart Control überwacht eine Photovoltaikanlage in Echtzeit, visualisiert Erzeugung, Verbrauch und Netzeinspeisung, und steuert die Batterie-Ladung intelligent über Modbus TCP. Das Ziel: **Die Batterie möglichst spät am Tag mit der Energie füllen, die sonst über dem Einspeiselimit verloren ginge.**

### Kernfunktionen

**Monitoring** — Live-Daten (PV, Last, Netz, Batterie, SOC) werden alle 60 Sekunden vom Wechselrichter abgefragt und in einer Zeitreihendatenbank gespeichert. Das Dashboard zeigt Echtzeit-Kacheln und einen interaktiven Verlaufsgraphen mit frei wählbaren Zeitfenstern.

**Forecast** — Stündliche PV-Ertragsprognose basierend auf Open-Meteo Wetterdaten, berechnet aus Standort, Dachneigung, Azimut, Modulleistung und Systemwirkungsgrad. Die Prognose wird nahtlos mit den Messdaten im selben Graphen dargestellt.

**Energieberechnung** — Das Frontend berechnet per Trapezintegral die erzeugte, erwartete und eingespeiste Energie in kWh sowie den Anteil über dem konfigurierbaren Einspeiselimit.

**Automatische Batterie-Ladesteuerung** — Der Algorithmus analysiert den PV-Forecast, identifiziert erwartetes Clipping (Erzeugung über dem Einspeiselimit) und berechnet ein optimales Ladeprofil. Die Batterie wird gezielt spät am Tag geladen, bevorzugt mit Energie die sonst gekappt würde.

**Manuelle Steuerung** — Interaktiver Slider für das Batterieladelimit (InWRte) mit Echtzeit-Modbus-Write an den Wechselrichter.

---

## Architektur

```
┌─────────────────────────────────────────────────────┐
│                   React Frontend                     │
│  Recharts · Tailwind · TypeScript · Vite             │
│  Live-Dashboard · Forecast-Graph · Batterie-Slider   │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP/REST
┌──────────────────────┴──────────────────────────────┐
│                  FastAPI Backend                      │
│  Poll-Loop (60s) · Auto-Control · Forecast-Engine    │
│  Settings-API · Forecast-Cache · Battery-Cache       │
└───────┬──────────────┬──────────────┬───────────────┘
        │              │              │
   Modbus TCP     HTTP/REST       HTTP/REST
        │              │              │
   ┌────┴────┐   ┌─────┴─────┐  ┌────┴────────┐
   │ Fronius  │   │ PocketBase│  │  Open-Meteo  │
   │ Verto+   │   │  (SQLite) │  │  Weather API │
   │ 15 kW    │   │           │  │              │
   └──────────┘   └───────────┘  └──────────────┘
```

---

## Tech-Stack

| Komponente | Technologie |
|---|---|
| Frontend | React 18, TypeScript, Vite, Recharts, Tailwind CSS, Lucide Icons |
| Backend | Python 3.11+, FastAPI, httpx, pymodbus, Pydantic |
| Datenbank | PocketBase (eingebettetes SQLite) |
| Wechselrichter | Fronius Verto Plus 15 kW, Modbus TCP (SunSpec Model 124) |
| Wetter-API | Open-Meteo (kostenlos, kein API-Key) |

---

## Projektstruktur

```
├── backend/
│   ├── main.py                 # FastAPI-App, Poll-Loop, Endpoints
│   ├── auto_control.py         # Intelligente Ladesteuerung
│   ├── modbus_service.py       # Modbus-TCP-Client (SunSpec)
│   └── config/
│       └── settings.json       # Persistierte Einstellungen
│
├── frontend/
│   └── src/
│       ├── App.tsx             # Dashboard, Charts, Settings-UI
│       └── api.ts              # API-Client, Typen, Helper
│
└── README.md
```

---

## Setup

### Voraussetzungen

- Python 3.11+
- Node.js 18+
- PocketBase ([Download](https://pocketbase.io/docs/))
- Fronius-Wechselrichter mit aktiviertem Modbus TCP (Port 502)

### PocketBase

```bash
./pocketbase serve
```

Collection `power_logs` anlegen mit folgenden Feldern:

| Feld | Typ |
|---|---|
| `pv_power` | number |
| `load_power` | number |
| `grid_power` | number |
| `battery_power` | number |
| `battery_soc` | number |

### Backend

```bash
cd backend
pip install fastapi uvicorn httpx pymodbus pocketbase pydantic
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Wechselrichter-Konfiguration

Modbus TCP muss am Fronius-Wechselrichter aktiviert sein:

1. Fronius Web-Interface → **Kommunikation** → **Modbus**
2. **Modbus TCP** aktivieren, Port `502`
3. **SunSpec Model Type**: `float` oder `int+SF` (das System verwendet int+SF)
4. **Slave ID**: `1` (Standardwert)

Die IP-Adresse des Wechselrichters in `main.py` und `modbus_service.py` anpassen (`FRONIUS_URL` bzw. `host`-Parameter).

---

## Konfiguration (Settings)

Alle Einstellungen sind über das Settings-Tab im Dashboard konfigurierbar und werden in `config/settings.json` persistiert.

| Einstellung | Beschreibung | Default |
|---|---|---|
| `timezone` | Zeitzone für die Anzeige | `Europe/Berlin` |
| `export_limit_percent` | Einspeiselimit in % der Modulleistung (0 = kein Limit) | `0` |
| `auto_control_active` | KI-Batteriesteuerung ein/aus | `false` |
| `location_lat` / `location_lon` | Standort für Wetterprognose | `48.137` / `11.576` |
| `panel_tilt` | Neigungswinkel der Module in Grad | `30` |
| `panel_azimuth` | Ausrichtung (Süd=0, Ost=−90, West=90) | `0` |
| `system_capacity_kwp` | Installierte Modulleistung in kWp | `0.0` |
| `inverter_max_kw` | Max. Wechselrichterleistung in kW | `15.0` |
| `battery_capacity_kwh` | Nutzbare Batteriekapazität in kWh | `0.0` |
| `system_efficiency` | Systemwirkungsgrad (0.5–1.0) | `0.85` |
| `safety_factor` | Forecast-Sicherheitsfaktor (0.5–1.0) | `0.80` |

---

## Auto-Control Algorithmus

Die automatische Batteriesteuerung verfolgt drei priorisierte Ziele:

1. **Batterie voll bekommen** — Wenn genug PV-Ertrag erwartet wird, soll die Batterie bis Sonnenuntergang 100% SOC erreichen.
2. **Clipping vermeiden** — Bevorzugt die Energie nutzen, die über dem Einspeiselimit ins Netz gehen würde.
3. **Spät laden** — Die Batterie so spät wie möglich am Tag füllen (fördert die Batterielebensdauer).

### Ablauf (jede 60 Sekunden)

Der Algorithmus läuft in jedem Poll-Zyklus mit aktuellen Messwerten und passt sich automatisch an:

**Schritt 1 — Energiebedarf berechnen:**
Aus aktuellem SOC und Batteriekapazität ergibt sich, wie viel kWh noch geladen werden müssen.

**Schritt 2 — Forecast aufbereiten:**
Für jeden verbleibenden 15-Minuten-Slot wird die erwartete PV-Leistung (×Safety-Factor) mit dem Einspeiselimit verglichen. Daraus ergeben sich zwei Kategorien: Clipping-Energie (über dem Limit) und normaler Überschuss.

**Schritt 3 — Rückwärts-Allokation Clipping:**
Vom Sonnenuntergang rückwärts wird Clipping-Energie auf die Batterie verteilt, bis der Bedarf gedeckt ist. So werden die spätesten Clipping-Stunden zuerst genutzt.

**Schritt 4 — Rückwärts-Allokation Überschuss:**
Falls Clipping nicht reicht, wird zusätzlich normaler Überschuss verteilt — ebenfalls von hinten nach vorn.

**Schritt 5 — InWRte berechnen:**
Aus der allokierten Energie des aktuellen Slots wird die nötige Laderate in % der maximalen Ladeleistung berechnet.

**Schritt 6 — Echtzeit-Korrektur:**
Falls die tatsächliche Netzeinspeisung gerade über dem Limit liegt, wird das Ladelimit sofort angehoben (Boost), unabhängig vom Plan.

**Schritt 7 — Hysterese:**
Der Modbus-Write wird nur ausgeführt, wenn sich der Wert um ≥3 Prozentpunkte geändert hat.

### Beispiel

```
10:00 Uhr, SOC=20%, Kapazität=10 kWh → Bedarf=8 kWh
Export-Limit=7000 W, Safety=0.80

Forecast (nach Safety): 11:00=9kW, 12:00=11kW, 13:00=10kW, 14:00=8kW
Clipping:               11:00=2kW, 12:00=4kW,  13:00=3kW,  14:00=1kW
Total Clipping = 10 kWh > 8 kWh Bedarf → Nur Clipping reicht!

Rückwärts-Allokation:
  14:00 → 1 kWh absorbieren, verbleibend: 7 kWh
  13:00 → 3 kWh absorbieren, verbleibend: 4 kWh
  12:00 → 4 kWh absorbieren, verbleibend: 0 kWh ✓
  11:00 → nicht nötig → InWRte=0% (Clipping wird akzeptiert)

Ergebnis: Batterie lädt erst ab 12:00 statt sofort → spät geladen ✓
```

---

## API-Endpoints

### Monitoring

| Methode | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/api/latest` | Letzter Datenpunkt (PV, Last, Netz, Batterie, SOC) |
| `GET` | `/api/history?start=...&end=...` | Historische Daten im Zeitraum |
| `GET` | `/api/powerflow` | Rohdaten direkt vom Wechselrichter (Fronius Solar API) |
| `GET` | `/api/forecast` | PV-Ertragsprognose (15-min-Raster) |

### Batterie-Steuerung

| Methode | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/api/battery/status` | Batterie-Status via Modbus (SOC, Limits, Modus) |
| `POST` | `/api/battery/charge_limit` | Ladelimit setzen (`{"limit_pct": 50.0}`) |

### System

| Methode | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/api/settings` | Aktuelle Einstellungen |
| `POST` | `/api/settings` | Einstellungen speichern |
| `GET` | `/api/auto_control/status` | Status der automatischen Steuerung |

---

## Modbus-Register (SunSpec Model 124)

Die Kommunikation mit dem Fronius Verto Plus erfolgt über SunSpec Model 124 (Storage Control). Basisadresse: `40362` (0-basiert).

| Register | Offset | Name | Beschreibung | Zugriff |
|---|---|---|---|---|
| 40363 | +0 | Model ID | Muss `124` sein | Read |
| 40365 | +2 | WChaMax | Max. Ladeleistung in Watt | Read |
| 40368 | +5 | StorCtl_Mod | Steuerungsmodus (Bit 0=Laden, Bit 1=Entladen) | Read |
| 40370 | +7 | MinRsvPct | Mindest-Reserve in % | Read |
| 40375 | +12 | OutWRte | Entladelimit in % | Read |
| 40376 | +13 | InWRte | Ladelimit in % | Read/Write |
| 40388 | +25 | InOutWRte_SF | Scale Factor für Lade-/Entladelimit | Read |

Scale Factor: Der Rohwert wird als `target_pct × 10^(-SF)` berechnet. Typisch ist SF=−1, also wird z.B. 50% als `500` geschrieben.

---

## Lizenz

MIT
