from __future__ import annotations

from pathlib import Path

import pytest

from scripts.azure.dataforge_recovery_manifest import (
    assert_repository_safe,
    build_recovery_manifest,
)


def _raw_snapshot() -> dict[str, object]:
    return {
        "generated_at": "2026-09-10T12:00:00+08:00",
        "subscription_display_name": "Microsoft Azure ai1-1",
        "resource_groups": [
            {
                "name": "rg-dataforge-dev",
                "location": "eastus2",
                "id": "/subscriptions/hidden/resourceGroups/rg-dataforge-dev",
            }
        ],
        "container_apps": [
            {
                "name": "ca-dataforge-backend",
                "resource_group": "rg-dataforge-dev",
                "image": "acrdataforgemyr0807.azurecr.io/backend:release",
                "min_replicas": 1,
                "max_replicas": 3,
                "ingress_external": True,
                "target_port": 8000,
                "environment": [
                    {"name": "DF_API_KEY", "value": "must-not-leak"},
                    {"name": "SQL_CONNECTION", "secret_ref": "sql-connection"},
                    {"name": "DF_FINOPS_SQL_ENABLED", "value": "1"},
                ],
                "secrets": [{"name": "sql-connection", "value": "secret"}],
            }
        ],
        "jobs": [
            {
                "name": "job-dataforge-finops-rollup",
                "resource_group": "rg-dataforge-dev",
                "trigger_type": "Schedule",
                "cron_expression": "2-59/15 * * * *",
                "image": "acrdataforgemyr0807.azurecr.io/backend:release",
                "environment": [
                    {"name": "SQL_CONNECTION", "secret_ref": "sql-connection"},
                ],
            }
        ],
        "sql_databases": [
            {
                "name": "df_lineage",
                "resource_group": "rg-dataforge-dev",
                "server": "dfsqlmyr0807",
                "sku": "Basic",
                "status": "Online",
            }
        ],
        "api_management": [
            {
                "name": "dfmonapim-myr0807",
                "resource_group": "rg-dataforge-dev",
                "sku": "StandardV2",
                "capacity": 1,
                "apis": [{"name": "dataforge-text", "path": "openai"}],
                "subscription_key": "never-copy",
            }
        ],
        "search_services": [
            {
                "name": "srch-dataforge-myr0807",
                "resource_group": "rg-dataforge-dev",
                "sku": "standard",
                "replica_count": 1,
                "partition_count": 1,
            }
        ],
        "foundry": [
            {
                "name": "agent-demo-foundry-ai2-myr0807",
                "resource_group": "Agent-Demo-Fuzh",
                "deployments": ["gpt-5.6-terra", "text-embedding-3-small"],
                "endpoint": "https://example.invalid",
            }
        ],
        "supporting_resources": [
            {
                "name": "kvdfmyr0807",
                "type": "Microsoft.KeyVault/vaults",
                "resource_group": "rg-dataforge-dev",
                "location": "eastus2",
            },
            {
                "name": "acrdataforgemyr0807",
                "type": "Microsoft.ContainerRegistry/registries",
                "resource_group": "rg-dataforge-dev",
                "location": "eastus2",
            },
        ],
    }


def test_manifest_projects_only_recovery_safe_fields() -> None:
    manifest = build_recovery_manifest(_raw_snapshot())

    app = manifest["container_apps"][0]
    assert app["environment"] == [
        {"name": "DF_API_KEY", "source": "configuration"},
        {"name": "DF_FINOPS_SQL_ENABLED", "source": "configuration"},
        {"name": "SQL_CONNECTION", "secret_ref": "sql-connection"},
    ]
    assert "secrets" not in app
    assert "subscription_key" not in manifest["api_management"][0]
    assert "endpoint" not in manifest["foundry"][0]
    assert "id" not in manifest["resource_groups"][0]
    assert_repository_safe(manifest)


def test_manifest_sorts_resources_for_stable_diffs() -> None:
    source = _raw_snapshot()
    source["supporting_resources"] = list(reversed(source["supporting_resources"]))

    manifest = build_recovery_manifest(source)

    assert [item["name"] for item in manifest["supporting_resources"]] == [
        "acrdataforgemyr0807",
        "kvdfmyr0807",
    ]


def test_manifest_omits_platform_generated_guid_resource_names() -> None:
    source = _raw_snapshot()
    generated_name = "00000000-" + "0000-0000-0000-000000000000"
    source["supporting_resources"].append(
        {
            "name": generated_name,
            "type": "microsoft.insights/actionGroups",
            "resource_group": "rg-dataforge-dev",
            "location": "Global",
        }
    )

    manifest = build_recovery_manifest(source)

    assert all(
        item["name"] != generated_name
        for item in manifest["supporting_resources"]
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"tenant_id": "hidden"},
        {"note": "00000000-" + "0000-0000-0000-000000000000"},
        {"secret": "hidden"},
    ],
)
def test_repository_safety_rejects_identifiers_and_secret_fields(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="sensitive"):
        assert_repository_safe(payload)


def test_repository_safety_reports_only_the_field_path() -> None:
    sensitive_value = "00000000-" + "0000-0000-0000-000000000000"

    with pytest.raises(ValueError) as exc_info:
        assert_repository_safe({"apps": [{"secret_ref": sensitive_value}]})

    message = str(exc_info.value)
    assert "apps[0].secret_ref" in message
    assert sensitive_value not in message


def test_exporter_never_reads_secret_values_or_raw_resource_payloads() -> None:
    script = (
        Path(__file__).parents[1]
        / "scripts"
        / "azure"
        / "export_dataforge_recovery.ps1"
    ).read_text(encoding="utf-8")
    lowered = script.casefold()

    assert "list-secrets" not in lowered
    assert "show-secrets" not in lowered
    assert "keyvault secret show" not in lowered
    assert "connection-string" not in lowered
    assert "--query" in lowered
    assert "dataforge_recovery_manifest.py" in script


def test_exporter_accepts_an_initially_empty_target_collection() -> None:
    script = (
        Path(__file__).parents[1]
        / "scripts"
        / "azure"
        / "export_dataforge_recovery.ps1"
    ).read_text(encoding="utf-8")

    assert "[AllowEmptyCollection()]" in script
    assert "[System.Collections.ArrayList]$Target" in script


def test_resume_script_is_dry_run_by_default_and_uses_no_string_eval() -> None:
    script = (
        Path(__file__).parents[1]
        / "scripts"
        / "azure"
        / "resume_dataforge_runtime.ps1"
    ).read_text(encoding="utf-8")
    lowered = script.casefold()

    assert "[switch]$execute" in lowered
    assert "if (-not $execute)" in lowered
    assert "invoke-expression" not in lowered
    assert "current-manifest.json" in lowered
    assert "ca-dataforge-redis" in lowered
    assert "ca-dataforge-web" in lowered
    assert "az containerapp start" not in lowered
    assert "az rest" in lowered
    assert "/start?api-version=2025-07-01" in lowered
