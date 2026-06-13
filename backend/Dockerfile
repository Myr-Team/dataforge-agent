FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=80

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY ingest/ ./ingest/
COPY workspaces/ ./workspaces/
COPY agents/prompts/ ./agents/prompts/
COPY agents/rubrics/ ./agents/rubrics/

EXPOSE 80
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "80"]
