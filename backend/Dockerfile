FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=80

WORKDIR /app
# 中文字体（概念图合成标题用）
# 中文字体 + WeasyPrint(HTML→PDF)运行库
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl \
        fonts-wqy-microhei \
        libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 \
        libharfbuzz0b libgdk-pixbuf-2.0-0 libffi8 libjpeg62-turbo shared-mime-info \
    && . /etc/os-release \
    && curl -sSL "https://packages.microsoft.com/config/debian/${VERSION_ID%%.*}/packages-microsoft-prod.deb" \
        -o /tmp/packages-microsoft-prod.deb \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 libgssapi-krb5-2 \
    && apt-get purge -y --auto-remove curl packages-microsoft-prod \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY scripts/verify_lineage_sql.py ./scripts/verify_lineage_sql.py
COPY ingest/ ./ingest/
COPY workspaces/ ./workspaces/
COPY agents/build_agents.py ./agents/build_agents.py
COPY agents/tool_schemas.json ./agents/tool_schemas.json
COPY agents/prompts/ ./agents/prompts/
COPY agents/rubrics/ ./agents/rubrics/
RUN python -c "import pyodbc; assert 'ODBC Driver 18 for SQL Server' in pyodbc.drivers(), pyodbc.drivers()" \
    && python scripts/verify_lineage_sql.py --check-prerequisites \
    && python -m backend.import_smoke

EXPOSE 80
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "80"]
