FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Startet den Uvicorn-Server so, wie in deiner Start-Datei beschrieben, aber für externe Zugriffe geöffnet
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]