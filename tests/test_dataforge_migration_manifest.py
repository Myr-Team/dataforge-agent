from scripts.azure.dataforge_migration_manifest import (
    assert_no_sensitive_values,
    resource_names_by_group,
    sanitize_resource_inventory,
)


def test_inventory_keeps_only_safe_resource_fields():
    source = [
        {
            "id": "/subscriptions/hidden/resourceGroups/rg-dataforge-dev/providers/Microsoft.App/containerApps/ca-dataforge-web",
            "name": "ca-dataforge-web",
            "type": "Microsoft.App/containerApps",
            "resourceGroup": "rg-dataforge-dev",
            "location": "eastus2",
            "properties": {"secret": "never-copy"},
        }
    ]

    assert sanitize_resource_inventory(source) == [
        {
            "name": "ca-dataforge-web",
            "type": "Microsoft.App/containerApps",
            "resource_group": "rg-dataforge-dev",
            "location": "eastus2",
        }
    ]


def test_manifest_groups_only_explicit_dataforge_resources():
    rows = [
        {
            "name": "ca-dataforge-web",
            "type": "Microsoft.App/containerApps",
            "resource_group": "rg-dataforge-dev",
            "location": "eastus2",
        },
        {
            "name": "unrelated-demo",
            "type": "Microsoft.Storage/storageAccounts",
            "resource_group": "Agent-Demo-Fuzh",
            "location": "eastasia",
        },
    ]

    assert resource_names_by_group(rows, {"rg-dataforge-dev": None}) == {
        "rg-dataforge-dev": ["ca-dataforge-web"]
    }


def test_sensitive_keys_are_rejected():
    try:
        assert_no_sensitive_values({"subscription_id": "hidden"})
    except ValueError as exc:
        assert "sensitive" in str(exc).lower()
    else:
        raise AssertionError("expected sensitive manifest rejection")


def test_identifier_shaped_values_are_rejected():
    synthetic_identifier = "00000000-" + "0000-0000-0000-000000000000"

    try:
        assert_no_sensitive_values({"reference": synthetic_identifier})
    except ValueError as exc:
        assert "sensitive" in str(exc).lower()
    else:
        raise AssertionError("expected identifier-shaped value rejection")
