# PV Monitoring Backend

## Entwicklungsserver starten

```bash
cd backend
source .venv/Scripts/activate   # Windows (Git Bash)
# source .venv/bin/activate     # Linux / macOS
uvicorn main:app --reload
```

Der Server läuft dann unter http://127.0.0.1:8000. Die API-Docs sind erreichbar unter http://127.0.0.1:8000/docs.
