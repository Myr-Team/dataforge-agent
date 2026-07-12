FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=80

WORKDIR /app
# 中文字体（概念图合成标题用）
# 中文字体 + WeasyPrint(HTML→PDF)运行库
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-wqy-microhei \
        libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 \
        libharfbuzz0b libgdk-pixbuf-2.0-0 libffi8 libjpeg62-turbo shared-mime-info \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY ingest/ ./ingest/
COPY workspaces/ ./workspaces/
COPY agents/build_agents.py ./agents/build_agents.py
COPY agents/tool_schemas.json ./agents/tool_schemas.json
COPY agents/prompts/ ./agents/prompts/
COPY agents/rubrics/ ./agents/rubrics/
RUN python -m backend.import_smoke

EXPOSE 80
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "80"]
