# ☀️ PV Smart Control

Intelligentes PV-Monitoring und Batterie-Steuerungssystem für Fronius-Wechselrichter mit automatischer Ladeoptimierung.

![Tech Stack](https://img.shields.io/badge/React-TypeScript-blue) ![Backend](https://img.shields.io/badge/FastAPI-Python-green) ![Database](https://img.shields.io/badge/PocketBase-SQLite-orange) ![Protocol](https://img.shields.io/badge/Modbus_TCP-SunSpec-red)

---

## Was macht das System?

PV Smart Control überwacht eine Photovoltaikanlage in Echtzeit, visualisiert Erzeugung, Verbrauch und Netzeinspeisung, und steuert die Batterie-Ladung intelligent über Modbus TCP. Das Ziel: **Die Batterie möglichst spät am Tag mit der Energie füllen, die sonst über dem Einspeiselimit verloren ginge.**

### Kernfunktionen

**Monitoring** — Live-Daten (PV, Last, Netz, Batterie, SOC) werden alle 60 Sekunden über die Fronius Solar API abgefragt und in PocketBase gespeichert. Neue Werte schickt das Backend per Server-Sent Events (SSE) an alle offenen Browser – das Dashboard muss nicht pollen. Es zeigt Echtzeit-Kacheln und einen Verlaufsgraphen mit den Zeiträumen *Heute*, *Letzte 24h*, *Gestern* und *Morgen*. Der Schalter *Nur Sonnenfenster* (standardmäßig an) beschneidet den gewählten Tag auf die Stunden mit erwarteter PV-Leistung. Einzelne Linien lassen sich ein- und ausblenden.

**Forecast** — PV-Ertragsprognose im 15-Minuten-Raster auf Basis der Open-Meteo-Einstrahlung auf die geneigte Modulfläche (`global_tilted_irradiance`), berechnet aus Standort, Dachneigung, Azimut, Modulleistung und Systemwirkungsgrad, begrenzt auf die maximale Wechselrichterleistung. Die Prognose wird nahtlos mit den Messdaten im selben Graphen dargestellt.

**Energieberechnung** — Das Frontend berechnet per Trapezintegral die erzeugte, erwartete und eingespeiste Energie in kWh sowie den Anteil über dem konfigurierbaren Einspeiselimit.

**Automatische Batterie-Ladesteuerung** — Der Algorithmus plant eine gleichmäßige SOC-Kurve bis kurz vor Sonnenuntergang und fängt Einspeisung über dem Limit mit einem gedämpften Echtzeit-Boost ab. Reicht das erwartete Clipping allein, um die Batterie zu füllen, wird nur noch Clipping geladen. Jede Entscheidung landet im *KI-Entscheidungsprotokoll* im Dashboard.

**Manuelle Steuerung** — Interaktiver Slider für das Batterieladelimit (InWRte) mit Echtzeit-Modbus-Write an den Wechselrichter.

**Batterie-Zustand** — Zelltemperaturen, SoH, Zellspannungen, Zyklen sowie Betriebs- und Wechselrichterstatus mit Warnstufen und einem Ereignisprotokoll (siehe [Batterie-Zustand](#batterie-zustand)).

**PWA** — Das Frontend lässt sich als App installieren (Manifest und Service Worker über `vite-plugin-pwa`).

---

## Architektur

```
┌─────────────────────────────────────────────────────┐
│                   React Frontend                     │
│  Recharts · Tailwind · TypeScript · Vite · PWA       │
│  Live-Dashboard · Forecast-Graph · Batterie-Slider   │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP/REST + SSE (/api/events)
┌──────────────────────┴──────────────────────────────┐
│                  FastAPI Backend                      │
│  Poll-Loop (60s) · Auto-Control · Forecast-Engine    │
│  Batterie-Zustand · Settings-API · SSE-Broadcast     │
└───────┬──────────────┬──────────────┬───────────────┘
        │              │              │
  Modbus TCP +    HTTP/REST       HTTP/REST
  HTTP (Solar API)     │              │
        │              │              │
   ┌────┴────┐   ┌─────┴─────┐  ┌────┴────────┐
   │ Fronius  │   │ PocketBase│  │  Open-Meteo  │
   │ Verto+   │   │  (SQLite) │  │  Weather API │
   │ 15 kW    │   │           │  │              │
   └──────────┘   └───────────┘  └──────────────┘
```

Zum Wechselrichter gehen zwei Kanäle: Modbus TCP für das Lesen und Schreiben der Ladelimits, HTTP für die Messwerte (Solar API) und den Batterie-Zustand (interne API).

---

## Tech-Stack

| Komponente | Technologie |
|---|---|
| Frontend | React 19, TypeScript, Vite 7, Recharts 3, Tailwind CSS 4, Lucide Icons, axios, vite-plugin-pwa |
| Backend | Python 3.11+ (Docker-Image: 3.12), FastAPI, httpx, pymodbus, Pydantic, sse-starlette |
| Datenbank | PocketBase (eingebettetes SQLite), Version im Docker-Image fest: `0.40.5` |
| Wechselrichter | Fronius Verto Plus 15 kW, Modbus TCP (SunSpec Model 124) + Solar API |
| Wetter-API | Open-Meteo (kostenlos, kein API-Key) |

---

## Projektstruktur

```
├── backend/
│   ├── main.py                 # FastAPI-App, Poll-Loop, SSE, Endpoints, Forecast
│   ├── auto_control.py         # Automatische Ladesteuerung
│   ├── battery_health.py       # Batterie-Zustand, Warnstufen, Ereignisse
│   ├── modbus_service.py       # Modbus-TCP-Client (SunSpec)
│   ├── config/
│   │   └── settings.json       # Persistierte Einstellungen
│   ├── tests/                  # pytest (Batterie-Zustand) inkl. WR-Fixtures
│   ├── requirements.txt        # Laufzeit-Abhängigkeiten
│   ├── requirements-dev.txt    # + pytest
│   └── Dockerfile
│
├── frontend/
│   ├── src/
│   │   ├── App.tsx             # Dashboard, Charts, Settings-UI, Auto-Control-Protokoll
│   │   ├── BatteryHealth.tsx   # Batterie-Zustand und Ereignisprotokoll
│   │   └── api.ts              # API-Client, Typen, Helper
│   ├── public/                 # Icons und PWA-Manifest-Grafiken
│   ├── nginx.conf              # Auslieferung + Proxy /api → Backend (Docker)
│   ├── vite.config.ts          # inkl. PWA-Manifest
│   └── Dockerfile
│
├── database/
│   ├── pb_migrations/          # PocketBase-Migrationen (Collections)
│   └── Dockerfile              # PocketBase-Image mit eingebauten Migrationen
│
├── docker-compose.yml          # Stack für Docker / Portainer
├── start_*.bat                 # Lokales Starten unter Windows
└── README.md
```

---

## Setup

### Voraussetzungen

- Python 3.11+
- Node.js 18+ (Docker-Build nutzt Node 22)
- PocketBase ([Download](https://pocketbase.io/docs/)) – Binary nach `database/` legen (`pocketbase.exe` ist per `.gitignore` ausgeschlossen)
- Fronius-Wechselrichter mit aktiviertem Modbus TCP (Port 502)

### PocketBase

```bash
cd database
./pocketbase serve        # Windows: pocketbase.exe serve
```

Die Collections (`power_logs`, `battery_health_logs`, `battery_events`) werden über die Migrationen in `database/pb_migrations` angelegt. PocketBase wendet neue Migrationen beim Start automatisch an. Läuft standardmäßig unter `http://127.0.0.1:8090`.

### Backend

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash); Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Die API läuft unter `http://127.0.0.1:8000`, die interaktive API-Doku unter `http://127.0.0.1:8000/docs`.

Tests:

```bash
pip install -r requirements-dev.txt
pytest
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Läuft unter `http://localhost:5173`. Der Dev-Server hat keinen Proxy – die Backend-Adresse kommt aus `frontend/.env`:

```
VITE_API_BASE_URL=http://localhost:8000
```

Ohne diese Variable ruft das Frontend `/api/...` relativ zur eigenen Adresse auf (so wie im Docker-Betrieb hinter nginx).

Unter Windows starten `start_pockbetbase.bat`, `start_backend.bat` und `start_frontend.bat` die drei Teile (Pfade sind fest auf `C:\Users\Andi\Dev\ClippingManager` eingetragen).

### Umgebungsvariablen (Backend)

| Variable | Beschreibung | Default |
|---|---|---|
| `FRONIUS_URL` | URL von `GetPowerFlowRealtimeData.fcgi` | `http://192.168.123.79/solar_api/v1/GetPowerFlowRealtimeData.fcgi` |
| `FRONIUS_BASE_URL` | Basis-URL des Wechselrichters für Batterie-Zustand | Schema + Host aus `FRONIUS_URL` |
| `POCKETBASE_URL` | Adresse von PocketBase | `http://127.0.0.1:8090` |

### Wechselrichter-Konfiguration

Modbus TCP muss am Fronius-Wechselrichter aktiviert sein:

1. Fronius Web-Interface → **Kommunikation** → **Modbus**
2. **Modbus TCP** aktivieren, Port `502`
3. **SunSpec Model Type**: `int+SF` (das System liest die Scale-Factor-Register und funktioniert mit `float` nicht)
4. **Slave ID**: `1` (Standardwert)

IP-Adresse des Wechselrichters anpassen:

- HTTP: über die Umgebungsvariable `FRONIUS_URL` (siehe oben)
- Modbus: die IP ist in `backend/modbus_service.py` als Default des `host`-Parameters fest eingetragen und muss dort geändert werden

### Deployment (Docker / Portainer)

`docker-compose.yml` baut drei Container:

| Service | Image / Build | Hinweise |
|---|---|---|
| `pocketbase` | `database/Dockerfile` | Daten unter `/opt/clippingmonitor/pb_data` auf dem Host |
| `backend` | `backend/Dockerfile` | Settings unter `/opt/clippingmonitor/config` auf dem Host, `POCKETBASE_URL=http://pocketbase:8090` |
| `frontend` | `frontend/Dockerfile` | Build mit Node, Auslieferung mit nginx; `/api/` und `/api/events` (SSE, ohne Buffering) werden an `backend:8000` weitergereicht |

Es werden keine Ports veröffentlicht. Erwartet wird ein externes Docker-Netzwerk `proxy`, in dem ein Reverse-Proxy den `frontend`-Container erreicht. Die Seite kann hinter einem Login liegen; das PWA-Manifest wird deshalb mit Credentials geladen. `FRONIUS_URL` ist in der Compose-Datei nicht gesetzt – bei anderer WR-IP dort ergänzen.

Der PocketBase-Container wird aus `database/Dockerfile` gebaut, das die Migrationen ins Image kopiert. Ein Bind-Mount auf `./database/pb_migrations` funktioniert unter Portainer nicht, weil Portainer das Repo in sein eigenes Volume klont und der relative Pfad auf dem Host ins Leere zeigt.

Nach neuen Migrationen: Stack in Portainer mit **Pull and redeploy** neu bauen. Die PocketBase-Version ist im Dockerfile fest eingetragen (`0.40.5`) und wird bewusst manuell angehoben.

---

## Konfiguration (Settings)

Alle Einstellungen sind über das Settings-Tab im Dashboard konfigurierbar und werden in `backend/config/settings.json` persistiert (im Docker-Betrieb `/app/config/settings.json`, auf den Host gemountet). Fehlt die Datei, legt das Backend sie mit den Defaults an.

| Einstellung | Beschreibung | Default |
|---|---|---|
| `timezone` | Zeitzone für die Anzeige (Berlin, Wien, London, UTC) | `Europe/Berlin` |
| `export_limit_percent` | Einspeiselimit in % der Modulleistung (`system_capacity_kwp`) | `0` |
| `auto_control_active` | Automatische Batteriesteuerung ein/aus | `false` |
| `location_lat` / `location_lon` | Standort für Wetterprognose | `48.137` / `11.576` |
| `panel_tilt` | Neigungswinkel der Module in Grad | `30` |
| `panel_azimuth` | Ausrichtung (Süd=0, Ost=−90, West=90) | `0` |
| `system_capacity_kwp` | Installierte Modulleistung in kWp | `0.0` |
| `inverter_max_kw` | Max. Wechselrichterleistung in kW (deckelt den Forecast) | `15.0` |
| `battery_capacity_kwh` | Nutzbare Batteriekapazität in kWh | `0.0` |
| `system_efficiency` | Systemwirkungsgrad (0.5–1.0) | `0.85` |
| `safety_factor` | Forecast-Sicherheitsfaktor (0.5–1.0) | `0.80` |

Einspeiselimit in Watt = `system_capacity_kwp × 1000 × export_limit_percent / 100`. Die Auswertungen „über Limit“ im Dashboard erscheinen nur bei einem Wert unter 100 %.

---

## Auto-Control Algorithmus

Die automatische Batteriesteuerung verfolgt drei Ziele:

1. **Batterie voll bekommen** — Wenn genug PV-Ertrag erwartet wird, soll die Batterie vor Sonnenuntergang 100 % SOC erreichen.
2. **Clipping vermeiden** — Energie, die über dem Einspeiselimit ins Netz gehen würde, wird bevorzugt in die Batterie geladen.
3. **Gleichmäßig und spät laden** — Statt sofort mit voller Leistung zu laden, steigt der SOC gleichmäßig über den Tag.

### Ablauf (alle 60 Sekunden)

Der Algorithmus läuft in jedem Poll-Zyklus mit aktuellen Messwerten, solange `auto_control_active` an ist.

**Schritt 1 — Energiebedarf:**
`Bedarf = Kapazität × (1 − SOC/100)`. Ohne konfigurierte Kapazität → 100 %, bei vollem Akku → 0 %.

**Schritt 2 — Forecast aufbereiten:**
Für jeden zukünftigen 15-Minuten-Slot wird die erwartete PV-Leistung mit dem Safety-Factor multipliziert und mit dem Einspeiselimit verglichen. Daraus ergeben sich die Clipping-Energie (über dem Limit) und die gesamte PV-Energie, jeweils mit 95 % Ladewirkungsgrad. Ohne Forecast → 100 % als Fallback.

**Schritt 3 — Vorab-Prüfung:**
- *Nicht genug Sonne:* erwartete PV-Energie < Bedarf → Basis 100 %, jede kWh zählt.
- *Clipping reicht:* erwartete Clipping-Energie ≥ Bedarf → Basis 0 %, nur der Echtzeit-Boost lädt.

**Schritt 4 — SOC-Trajektorie:**
Das Produktionsende wird aus dem Forecast bestimmt (letzter Slot mit PV vor einer Lücke von ≥ 2 h). Mit 1,5 h Puffer davor ergibt sich die verbleibende Zeit und daraus eine gleichmäßige SOC-Steigung (%/h) bis 100 %. Diese wird in eine Basis-Ladeleistung und dann in % von `WChaMax` umgerechnet. Bleiben weniger als 6 Minuten, wird mit 100 % geladen.

**Schritt 5 — Gedämpfte Echtzeit-Korrektur:**
Liegt die tatsächliche Einspeisung über dem Limit, wird ein Boost proportional zur Überschreitung aufgeschlagen. Der Boost wird SOC-abhängig gedämpft: 90 % unter 50 % SOC, linear auf 30 % bei 85 % SOC, dann bis 5 % bei 100 % SOC.

**Schritt 6 — Rundung und Hysterese:**
Der Zielwert wird auf ganze Prozent gerundet (der WR akzeptiert nur Ganzzahlen). Höhere Werte werden sofort geschrieben, niedrigere nur bei einer Differenz von mindestens 3 Prozentpunkten.

Jede Entscheidung samt Begründung wird in einem Ringpuffer (200 Einträge, nur im Speicher) abgelegt und über `/api/auto_control/log` im Dashboard angezeigt.

### Beispiel

```
10:00 Uhr (UTC), SOC=40 %, Kapazität=10 kWh → Bedarf=6 kWh
WChaMax=10 000 W, Produktionsende laut Forecast 17:30 → 7,5 h − 1,5 h Puffer = 6 h

Erwartetes Clipping (nach Safety-Factor) = 4 kWh < 6 kWh Bedarf → Trajektorie
SOC-Steigung: (100 − 40) / 6 h = 10 %/h
Basis-Ladeleistung: 10 kWh × 10 % / 0,95 ≈ 1 053 W → 11 %

Aktuelle Einspeisung 800 W über Limit, SOC 40 % → Dämpfung 90 %
Boost: 800 / 10 000 × 100 × 0,9 ≈ 7 %

Ergebnis: InWRte = 18 % (höher als bisher → wird sofort geschrieben)
```

---

## API-Endpoints

Die vollständige, interaktive Beschreibung liefert FastAPI unter `/docs`.

### Monitoring

| Methode | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/api/events` | SSE-Stream: `live`, `chart`, `battery`, `battery_health`, `battery_event`, `autocontrol_log` |
| `GET` | `/api/latest` | Letzter Datenpunkt (PV, Last, Netz, Batterie, SOC) |
| `GET` | `/api/history?start=...&end=...` | Historische Daten im Zeitraum (ohne Parameter: die letzten 1000 Datenpunkte) |
| `GET` | `/api/powerflow` | Rohdaten direkt vom Wechselrichter (Fronius Solar API) |
| `GET` | `/api/forecast` | PV-Ertragsprognose (15-min-Raster, gestern bis übermorgen, UTC) |

### Batterie-Steuerung

| Methode | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/api/battery/status` | Batterie-Status via Modbus (Limits in % und W, Reserve, Steuerungsmodus, `WChaMax`) |
| `POST` | `/api/battery/charge_limit` | Ladelimit setzen (`{"limit_pct": 50.0}`) |

### Batterie-Zustand

| Methode | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/api/battery/health` | Letzter Batterie-Zustand (Modus, SoH, Zelltemperaturen, Zellspannungen, Zyklen, WR-Status, Warnstufen) – wird alle 60 s aktualisiert, `503` bis zum ersten Poll |
| `GET` | `/api/battery/events?limit=50` | Batterie-Ereignisprotokoll (Modus-/Statuswechsel, Schwellwerte) aus PocketBase, `limit` 1–200 |

Datenquelle ist die interne, undokumentierte WR-API `/api/components/readable` (ohne Login). Fällt sie aus, nutzt das Backend `GetStorageRealtimeData.cgi` der Solar API (ohne SoH und Zellspannungen). Der WR-Status kommt aus `GetInverterInfo.cgi`. Messwerte landen alle 5 min in `battery_health_logs`, Ereignisse sofort in `battery_events`; bei PocketBase-Ausfall werden bis zu 100 Ereignisse gepuffert und nachgetragen (mit `occurred_at` als Zeitpunkt des Auftretens). Basis-URL über `FRONIUS_BASE_URL` (Default: Schema und Host aus `FRONIUS_URL`). Fehlende Werte werden in PocketBase als 0/false gespeichert (die Zahlen- und Bool-Felder kennen kein NULL); das Feld `source` kennzeichnet Datensätze aus dem Fallback.

Warnschwellen (mit Hysterese gegen Flattern):

| Wert | Warnung | Kritisch |
|---|---|---|
| Zelltemperatur | < 5 °C oder > 40 °C | < 0 °C oder > 50 °C |
| SoH | < 80 % | – |
| Zellspannungs-Spreizung | ≥ 50 mV | ≥ 100 mV |

### System

| Methode | Endpoint | Beschreibung |
|---|---|---|
| `GET` | `/api/settings` | Aktuelle Einstellungen |
| `POST` | `/api/settings` | Einstellungen speichern |
| `GET` | `/api/auto_control/status` | Status der automatischen Steuerung (`active`, `current_inwrte`) |
| `GET` | `/api/auto_control/log?limit=120` | Entscheidungsprotokoll der Auto-Control, neueste zuerst, `limit` 1–200 |

---

## Modbus-Register (SunSpec Model 124)

Die Kommunikation mit dem Fronius Verto Plus erfolgt über SunSpec Model 124 (Storage Control). Die Adressen in der Tabelle sind 0-basierte Modbus-Adressen, wie sie an pymodbus übergeben werden; die Fronius-Dokumentation zählt ab 1 (Adresse + 1). Beim Start prüft das Backend den SunSpec-Marker `SunS` an den Adressen 40000–40001.

| Adresse | Offset | Name | Beschreibung | Zugriff |
|---|---|---|---|---|
| 40363 | +0 | Model ID | Muss `124` sein | Read |
| 40365 | +2 | WChaMax | Max. Ladeleistung in Watt (ohne Scale Factor; < 1000 → Fallback 15 000 W) | Read |
| 40368 | +5 | StorCtl_Mod | Steuerungsmodus (Bit 0=Laden, Bit 1=Entladen) | Read |
| 40370 | +7 | MinRsvPct | Mindest-Reserve in % | Read |
| 40375 | +12 | OutWRte | Entladelimit in % | Read |
| 40376 | +13 | InWRte | Ladelimit in % | Read/Write |
| 40384 | +21 | MinRsvPct_SF | Scale Factor für Mindest-Reserve | Read |
| 40388 | +25 | InOutWRte_SF | Scale Factor für Lade-/Entladelimit | Read |

Scale Factor: Der Rohwert wird als `target_pct × 10^(-SF)` berechnet. Typisch ist SF=−1, also wird z.B. 50% als `500` geschrieben. Ist Bit 0 von `StorCtl_Mod` nach dem Schreiben nicht gesetzt, warnt das Backend, dass der WR das Ladelimit möglicherweise nicht anwendet.

---

## Lizenz

MIT
