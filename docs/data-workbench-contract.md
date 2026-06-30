# Data Workbench Backend Contract

Round3 backend contract for the new DataForge "数据工作台" page. Frontend can treat every response as JSON. Connector credentials are write-only: they are never returned by these APIs.

## Files

### `GET /api/workspaces/{workspace_id}/files`

Returns real workspace files grouped for the left file tree.

```json
{
  "workspace_id": "upload-demo",
  "groups": [
    {
      "label": "数据集",
      "files": [
        {
          "id": "80f33c3bd55fa67f",
          "name": "device_events.csv",
          "type": "csv",
          "bytes": 4096,
          "status": "indexed",
          "updated_at": "2026-06-29T11:53:35Z",
          "source_file": "raw_docs/device_events.csv",
          "record_count": 457,
          "backend_status": "已就绪"
        }
      ]
    },
    { "label": "文档", "files": [] },
    { "label": "临时文件", "files": [] }
  ],
  "storage": { "used_bytes": 13021, "total_bytes": 5368709120 }
}
```

`status` is `"indexed"` or `"needs_review"`.

### `POST /api/workspaces/{workspace_id}/files`

Creates a new markdown/text/table file, saves it through the existing workspace upload pipeline, and runs ingest so analysis can reference it.

Markdown body:

```json
{ "name": "pilot_notes", "type": "md", "text": "# Pilot notes\n\nNew signal." }
```

Table body (`kind:"table"` defaults to CSV; use `type:"xlsx"` for Excel):

```json
{
  "name": "validation_metrics",
  "kind": "table",
  "columns": ["metric", "value"],
  "rows": [["trial_signup", "12"]]
}
```

Response:

```json
{
  "workspace_id": "upload-demo",
  "saved_at": "2026-06-29T11:53:35Z",
  "file": { "id": "80f33c3bd55fa67f", "name": "validation_metrics.csv", "type": "csv", "status": "indexed" },
  "upload": { "ingest_job_id": "job_xxx", "indexed_delta": 3, "documents": [] }
}
```

Validation rejects path separators, oversized markdown/table payloads, too many columns/rows, and overlong cell values.

### `GET /api/workspaces/{workspace_id}/files/{file_id}/content?limit=100&offset=0`

CSV/XLSX response:

```json
{
  "kind": "table",
  "columns": [{ "name": "region" }],
  "rows": [["north"]],
  "total_rows": 457,
  "total_cols": 24,
  "offset": 0,
  "limit": 100,
  "sheet": "events",
  "sheets": ["events"]
}
```

Markdown response:

```json
{ "kind": "markdown", "text": "# Notes", "total_chars": 1200, "offset": 0, "limit": 100 }
```

## Editing

### `PUT /api/workspaces/{workspace_id}/files/{file_id}/cells`

Body uses zero-based data-row indexes. Header row is not counted. `col` can be a zero-based number or a column name.

```json
{ "edits": [{ "row": 0, "col": "score", "value": "11" }] }
```

Response:

```json
{ "saved_at": "2026-06-29T11:53:35Z", "version_id": "20260629T115335-e3a9c46a", "change_summary": "更新了 1 个单元格" }
```

### `PUT /api/workspaces/{workspace_id}/files/{file_id}/content`

Markdown/text only.

```json
{ "text": "# Updated\n\nEvidence note." }
```

Saves a new version under the workspace `versions/` prefix before replacing the current raw file.

## Quality And History

### `GET /api/workspaces/{workspace_id}/files/{file_id}/quality`

Computed from real file content.

```json
{
  "field_mapping": {
    "mapped": 6,
    "total": 6,
    "pct": 100.0,
    "fields": [{ "name": "score", "type": "number", "mapped": true, "missing_pct": 0.3, "type_warning": false, "outlier_count": 2 }]
  },
  "quality": {
    "missing_pct": 0.3,
    "duplicate_pct": 0.0,
    "outlier_count": 2,
    "outlier_pct": 0.4,
    "type_warnings": 0,
    "row_count": 457,
    "column_count": 24
  },
  "validation": { "status": "passed", "checked_at": "2026-06-29T11:53:35Z" }
}
```

`validation.status` is `"passed"`, `"warn"`, or `"failed"`.

### `GET /api/workspaces/{workspace_id}/files/{file_id}/field-mapping`

Returns computed quality mapping plus any user overrides:

```json
{
  "workspace_id": "upload-demo",
  "file_id": "80f33c3bd55fa67f",
  "field_mapping": {
    "mapped": 2,
    "total": 2,
    "pct": 100.0,
    "fields": [{ "name": "metric", "type": "text", "mapped": true, "target": "business_metric", "mapping_source": "user" }]
  },
  "overrides": { "metric": { "target": "business_metric", "notes": "Used to compare pilot outcomes" } },
  "saved_at": "2026-06-29T11:53:35Z",
  "validation": { "status": "passed", "checked_at": "2026-06-29T11:53:35Z" }
}
```

### `PUT /api/workspaces/{workspace_id}/files/{file_id}/field-mapping`

Accepted bodies:

```json
{ "mapping": { "metric": "business_metric" } }
```

or:

```json
{ "mappings": [{ "source": "metric", "target": "business_metric", "notes": "Used to compare pilot outcomes" }] }
```

Targets are stored as a workspace metadata overlay. The backend still recomputes missing/duplicate/outlier/type quality from the real file content on every quality call.

### `GET /api/workspaces/{workspace_id}/files/{file_id}/history`

```json
[{ "user": "DataForge", "email": null, "at": "2026-06-29T11:53:35Z", "change_summary": "更新了 1 个单元格" }]
```

## Send To Analysis

### `POST /api/workspaces/{workspace_id}/files/analyze`

```json
{ "file_ids": ["80f33c3bd55fa67f"], "message": "请分析这些文件里的机会", "conversation_id": null }
```

Returns a completed backend analysis payload plus a frontend jump signal:

```json
{
  "workspace_id": "upload-demo",
  "conversation_id": "conv-id",
  "status": "started",
  "mode": "analysis",
  "selected_files": [{ "id": "80f33c3bd55fa67f", "name": "device_events.csv" }],
  "jump": { "view": "agent_flow", "conversation_id": "conv-id" },
  "final": {},
  "text": "..."
}
```

## Connectors

### `GET /api/workspaces/{workspace_id}/connectors/capabilities`

Azure Data Lake stays demo-only. Manual Blob and SQL are available. Identity discovery is a placeholder until Easy Auth token store, delegated ARM/Storage/SQL permissions, and target RBAC are configured.

### SQL Stage One

`POST /api/workspaces/{workspace_id}/connectors/sql/connect`

```json
{ "server": "server.database.windows.net", "database": "db", "username": "user", "password": "write-only" }
```

or:

```json
{ "connection_string": "ODBC connection string for pyodbc" }
```

Response:

```json
{ "connection_id": "sql_xxx", "status": "connected", "expires_at": "2026-06-29T12:53:35Z", "tables": [{ "schema": "dbo", "name": "Events", "id": "dbo.Events" }], "credential_echo": null }
```

Then:

- `GET /api/workspaces/{workspace_id}/connectors/sql/tables?connection_id=sql_xxx`
- `GET /api/workspaces/{workspace_id}/connectors/sql/preview?connection_id=sql_xxx&table=dbo.Events&limit=100`
- `POST /api/workspaces/{workspace_id}/connectors/sql/import` body `{ "connection_id": "sql_xxx", "table": "dbo.Events", "limit": 1000 }`

### Blob Stage One

`POST /api/workspaces/{workspace_id}/connectors/blob/connect`

```json
{ "connection_string": "write-only" }
```

or:

```json
{ "account": "storageaccount", "sas": "write-only" }
```

Then:

- `GET /api/workspaces/{workspace_id}/connectors/blob/containers?connection_id=blob_xxx`
- `GET /api/workspaces/{workspace_id}/connectors/blob/blobs?connection_id=blob_xxx&container=data&prefix=raw/&limit=100`
- `GET /api/workspaces/{workspace_id}/connectors/blob/preview?connection_id=blob_xxx&container=data&blob=raw/file.csv`
- `POST /api/workspaces/{workspace_id}/connectors/blob/import` body `{ "connection_id": "blob_xxx", "container": "data", "blob": "raw/file.csv" }`
