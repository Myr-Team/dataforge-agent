# DataForge Feasibility Agent

DataForge turns an enterprise workspace of unstructured documents into grounded product feasibility analysis, project proposal artifacts, concept imagery, and an audio executive summary.

This repository is intentionally independent from the source proposal-agent project. Reused primitives are copied and adapted into this codebase, while cloud resources are created in the dedicated `rg-dataforge-dev` resource group and existing AI Foundry deployments are reused.

## Work Packages

Progress is tracked in `PROGRESS.md`; hard external blockers are tracked in `BLOCKERS.md`; autonomous choices are tracked in `docs/DECISIONS.md`.

## Local Smoke Path

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt -r mcp-market\requirements.txt
.\.venv\Scripts\python -m pytest
```

The web app can run as a static surface during offline development. The backend exposes mock-safe fallbacks where Azure resources are not yet available.
