from __future__ import annotations

import base64
import json
import math
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

import backend.foundry_roi as foundry_roi
from backend.foundry_roi import (
    DiscoveryProof,
    FoundryRoiStatus,
    FoundryRoiTarget,
    ProviderRoiSnapshot,
    VerifiedDiscoveryAttestation,
    discover_foundry_roi,
    public_foundry_integration,
    read_foundry_roi,
    reconcile_foundry_roi,
)


WINDOW = {"from": "2026-07-01T00:00:00+00:00", "to": "2026-07-02T00:00:00+00:00"}
TARGET_ENDPOINT = "https://project.services.ai.azure.com/api/projects/demo"
TARGET_AGENT_ID = "agent-demo"
ATTESTATION_CONTEXT = "dataforge.foundry_roi.discovery_attestation.v2"


def target() -> FoundryRoiTarget:
    return FoundryRoiTarget(project_endpoint=TARGET_ENDPOINT, agent_id=TARGET_AGENT_ID)


def local_snapshot(amount: float = 100.0, *, status: str = "verified") -> dict:
    return {
        "workspace_id": "ws-1",
        "window": WINDOW,
        "generated_at": "2026-07-02T01:00:00+00:00",
        "status": status,
        "business_value": {"total": amount, "currency": "USD", "by_currency": {"USD": amount}, "status": "measured"},
        "observed_run_ids": ["run-1", "run-2"],
        "lineage_complete": True,
        "truncated": False,
        "invalid_run_ids": [],
        "outcome_event_ids": ["outcome-1", "outcome-2"],
        "verified_outcome_event_ids": ["outcome-1", "outcome-2"],
    }


def proof(for_target: FoundryRoiTarget | None = None, *, state: str = "connected", fingerprint: str | None = None) -> DiscoveryProof:
    resolved_target = for_target or target()
    return DiscoveryProof(
        state=state,
        target_fingerprint=fingerprint or resolved_target.target_fingerprint,
        surface_id="roi-surface",
        surface_version="2026-07-preview",
        observed_at="2026-07-02T01:05:00+00:00",
    )


def provider_snapshot(
    amount: float = 90.0,
    *,
    target_fingerprint: str | None = None,
    status: str = "estimated",
    window: dict[str, str] | None = None,
    run_ids: list[str] | None = None,
    outcome_ids: list[str] | None = None,
) -> ProviderRoiSnapshot:
    return ProviderRoiSnapshot(
        target_fingerprint=target_fingerprint or target().target_fingerprint,
        window=window if window is not None else WINDOW,
        observed_at="2026-07-02T01:05:00+00:00",
        provider_version="2026-07-preview",
        status=status,
        business_value={"amount": amount, "currency": "USD", "unit": "currency"},
        mapped_run_ids=run_ids if run_ids is not None else ["run-1", "run-2"],
        mapped_outcome_event_ids=outcome_ids if outcome_ids is not None else ["outcome-1", "outcome-2"],
    )


def raw_provider_snapshot(amount) -> dict:
    snapshot = provider_snapshot().model_dump(mode="json")
    snapshot["business_value"]["amount"] = amount
    return snapshot


def configure_target(monkeypatch) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", TARGET_ENDPOINT)
    monkeypatch.setenv("FOUNDRY_AGENT_ID", TARGET_AGENT_ID)


def configure_trusted_public_key(monkeypatch, signing_key: Ed25519PrivateKey) -> None:
    raw_public_key = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv("DF_FOUNDRY_ROI_ATTESTATION_PUBLIC_KEY", base64.b64encode(raw_public_key).decode("ascii"))


class FakeVerifier:
    def __init__(self, signing_key: Ed25519PrivateKey, *, bind_snapshot: bool = True, signed_snapshot_digest: str | None = None) -> None:
        self.signing_key = signing_key
        self.bind_snapshot = bind_snapshot
        self.signed_snapshot_digest = signed_snapshot_digest

    def verify(self, configured_target, provider_proof, canonical_snapshot_bytes):
        assert canonical_snapshot_bytes is None or isinstance(canonical_snapshot_bytes, bytes)
        attestation = VerifiedDiscoveryAttestation(
            target_fingerprint=configured_target.target_fingerprint,
            surface_id=provider_proof.surface_id,
            surface_version=provider_proof.surface_version,
            observed_at=provider_proof.observed_at,
            observed_source="fake_discovery_verifier",
        )
        snapshot_digest = (
            self.signed_snapshot_digest
            if self.signed_snapshot_digest is not None
            else sha256(canonical_snapshot_bytes).hexdigest()
            if canonical_snapshot_bytes is not None and self.bind_snapshot
            else None
        )
        raw_public_key = self.signing_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        key_id = sha256(raw_public_key).hexdigest()
        payload = json.dumps(
            {
                "context": ATTESTATION_CONTEXT,
                "attestation": attestation.model_dump(mode="json"),
                "key_id": key_id,
                "snapshot_digest": snapshot_digest,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        envelope = {
            "attestation": attestation,
            "key_id": key_id,
            "snapshot_digest": snapshot_digest,
            "signature": base64.b64encode(self.signing_key.sign(payload)).decode("ascii"),
        }
        return envelope


def reconcile_with_provider(monkeypatch, local: dict, snapshot: ProviderRoiSnapshot):
    class Provider:
        def discover(self, configured_target):
            return proof(configured_target)

        def read(self, configured_target, window):
            return snapshot

    configure_target(monkeypatch)
    signing_key = Ed25519PrivateKey.generate()
    configure_trusted_public_key(monkeypatch, signing_key)
    return reconcile_foundry_roi(local=local, provider=Provider(), verifier=FakeVerifier(signing_key))


def test_environment_flag_alone_does_not_mean_configured(monkeypatch) -> None:
    monkeypatch.setenv("DF_FOUNDRY_ROI_ENABLED", "1")

    status = discover_foundry_roi()

    assert status.state == "not_configured"
    assert status.configured is False
    assert "DF_FOUNDRY_ROI_ENABLED" not in status.model_dump_json()


def test_missing_provider_does_not_attempt_network_or_claim_connection(monkeypatch) -> None:
    configure_target(monkeypatch)

    status = discover_foundry_roi()

    assert status.state == "not_configured"
    assert status.configured is False
    assert "provider" in status.reason.lower()


def test_public_integration_maps_missing_provider_to_normal_not_connected_state(monkeypatch) -> None:
    configure_target(monkeypatch)

    integration = public_foundry_integration()

    assert integration["state"] == "not_connected"
    assert integration["official_source"] is False
    assert integration["reason_code"] == "provider_not_installed"
    assert "project.services" not in str(integration)


def test_provider_must_discover_exact_target_before_connected(monkeypatch) -> None:
    class Provider:
        def discover(self, configured_target):
            assert configured_target.target_fingerprint == target().target_fingerprint
            return proof(configured_target)

        def read(self, configured_target, window):
            return provider_snapshot(target_fingerprint=configured_target.target_fingerprint, window=window)

    configure_target(monkeypatch)
    signing_key = Ed25519PrivateKey.generate()
    configure_trusted_public_key(monkeypatch, signing_key)

    result = read_foundry_roi(WINDOW, provider=Provider(), verifier=FakeVerifier(signing_key))

    assert result.status.state == "connected"
    assert result.provider_snapshot is not None
    assert result.provider_snapshot["target_fingerprint"] == target().target_fingerprint
    assert "project.services" not in str(result)
    assert "agent-demo" not in str(result)


def test_request_captures_public_key_and_target_before_provider_can_mutate_environment(monkeypatch) -> None:
    original_signing_key = Ed25519PrivateKey.generate()
    replacement_signing_key = Ed25519PrivateKey.generate()

    class Provider:
        def discover(self, configured_target):
            configure_trusted_public_key(monkeypatch, replacement_signing_key)
            monkeypatch.setenv("FOUNDRY_AGENT_ID", "attacker-agent")
            return proof(configured_target)

        def read(self, configured_target, window):
            assert configured_target.agent_id == TARGET_AGENT_ID
            return provider_snapshot(target_fingerprint=configured_target.target_fingerprint, window=window)

    configure_target(monkeypatch)
    configure_trusted_public_key(monkeypatch, original_signing_key)

    result = read_foundry_roi(WINDOW, provider=Provider(), verifier=FakeVerifier(original_signing_key))

    assert result.status.state == "connected"
    assert result.provider_snapshot is not None


def test_provider_cannot_mutate_request_target_used_by_later_calls(monkeypatch) -> None:
    signing_key = Ed25519PrivateKey.generate()

    class Provider:
        def discover(self, configured_target):
            object.__setattr__(configured_target, "agent_id", "attacker-agent")
            return proof(configured_target)

        def read(self, configured_target, window):
            assert configured_target.agent_id == TARGET_AGENT_ID
            return provider_snapshot(target_fingerprint=configured_target.target_fingerprint, window=window)

    configure_target(monkeypatch)
    configure_trusted_public_key(monkeypatch, signing_key)

    result = read_foundry_roi(WINDOW, provider=Provider(), verifier=FakeVerifier(signing_key))

    assert result.status.state == "connected"
    assert result.provider_snapshot is not None


def test_discovery_only_signature_never_connects_or_reconciles_provider_values(monkeypatch) -> None:
    class Provider:
        def discover(self, configured_target):
            return proof(configured_target)

        def read(self, configured_target, window):
            return provider_snapshot(target_fingerprint=configured_target.target_fingerprint, window=window)

    configure_target(monkeypatch)
    signing_key = Ed25519PrivateKey.generate()
    configure_trusted_public_key(monkeypatch, signing_key)

    discovery = discover_foundry_roi(provider=Provider(), verifier=FakeVerifier(signing_key, bind_snapshot=False))
    read = read_foundry_roi(WINDOW, provider=Provider(), verifier=FakeVerifier(signing_key, bind_snapshot=False))
    reconciliation = reconcile_foundry_roi(local_snapshot(), provider=Provider(), verifier=FakeVerifier(signing_key, bind_snapshot=False))

    assert discovery.state == "discovery_verified"
    assert read.status.state == "discovery_verified"
    assert read.provider_snapshot is None
    assert reconciliation["foundry_status"]["state"] == "discovery_verified"
    assert reconciliation["provider"] is None
    assert reconciliation["difference"] is None


@pytest.mark.parametrize(
    ("name", "mutate_retained_snapshot"),
    [
        ("amount", lambda snapshot: object.__setattr__(snapshot.business_value, "amount", 91.0)),
        ("lineage", lambda snapshot: snapshot.mapped_run_ids.append("run-3")),
        ("window", lambda snapshot: snapshot.window.update({"to": "2026-07-02T00:00:01+00:00"})),
        ("snapshot_after_sign", lambda snapshot: object.__setattr__(snapshot, "status", "verified")),
    ],
)
def test_retained_provider_snapshot_mutation_after_signature_uses_captured_canonical_values(monkeypatch, name, mutate_retained_snapshot) -> None:
    class RetainedProviderSnapshot(ProviderRoiSnapshot):
        pass

    class Provider:
        def __init__(self) -> None:
            self.snapshot = RetainedProviderSnapshot.model_validate(provider_snapshot().model_dump())

        def discover(self, configured_target):
            return proof(configured_target)

        def read(self, configured_target, window):
            return self.snapshot

    class MutatingVerifier(FakeVerifier):
        def __init__(self, signing_key, provider) -> None:
            super().__init__(signing_key)
            self.provider = provider

        def verify(self, configured_target, provider_proof, canonical_snapshot_bytes):
            envelope = super().verify(configured_target, provider_proof, canonical_snapshot_bytes)
            mutate_retained_snapshot(self.provider.snapshot)
            return envelope

    configure_target(monkeypatch)
    signing_key = Ed25519PrivateKey.generate()
    configure_trusted_public_key(monkeypatch, signing_key)
    provider = Provider()

    read = read_foundry_roi(WINDOW, provider=provider, verifier=MutatingVerifier(signing_key, provider))
    provider = Provider()
    reconciliation = reconcile_foundry_roi(
        local_snapshot(),
        provider=provider,
        verifier=MutatingVerifier(signing_key, provider),
    )

    assert name
    assert read.status.state == "connected"
    assert read.provider_snapshot is not None
    assert read.provider_snapshot["business_value"]["amount"] == 90.0
    assert read.provider_snapshot["window"] == WINDOW
    assert read.provider_snapshot["mapped_run_ids"] == ["run-1", "run-2"]
    assert reconciliation["foundry_status"]["state"] == "connected"
    assert reconciliation["provider"]["business_value"]["amount"] == 90.0
    assert reconciliation["provider"]["window"] == WINDOW
    assert reconciliation["provider"]["mapped_run_ids"] == ["run-1", "run-2"]
    assert reconciliation["difference"] == {"amount": -10.0, "currency": "USD", "unit": "currency"}


class _EqualitySpoofingString(str):
    """Simulates provider-controlled scalar subclasses that lie during Python comparisons."""

    _hash_aliases = {
        "attacker-run": "run-2",
        "attacker-outcome": "outcome-2",
    }

    def __eq__(self, other):  # type: ignore[override]
        return True

    def __ne__(self, other):  # type: ignore[override]
        return False

    def __hash__(self):
        return hash(self._hash_aliases.get(str.__str__(self), str.__str__(self)))


@pytest.mark.parametrize(
    ("name", "mutate", "expected_state", "expected_snapshot_value"),
    [
        (
            "target_fingerprint",
            lambda snapshot: object.__setattr__(snapshot, "target_fingerprint", _EqualitySpoofingString("0" * 64)),
            "unavailable",
            None,
        ),
        (
            "currency",
            lambda snapshot: object.__setattr__(snapshot.business_value, "currency", _EqualitySpoofingString("EUR")),
            "connected",
            "EUR",
        ),
        (
            "unit",
            lambda snapshot: object.__setattr__(snapshot.business_value, "unit", _EqualitySpoofingString("attacker-unit")),
            "unavailable",
            None,
        ),
        (
            "mapped_run_ids",
            lambda snapshot: snapshot.mapped_run_ids.__setitem__(1, _EqualitySpoofingString("attacker-run")),
            "connected",
            ["attacker-run", "run-1"],
        ),
        (
            "mapped_outcome_event_ids",
            lambda snapshot: snapshot.mapped_outcome_event_ids.__setitem__(1, _EqualitySpoofingString("attacker-outcome")),
            "connected",
            ["attacker-outcome", "outcome-1"],
        ),
    ],
)
def test_provider_model_and_primitive_subclasses_cannot_bypass_canonical_snapshot_boundary(
    monkeypatch, name, mutate, expected_state, expected_snapshot_value
) -> None:
    class SubclassedProviderSnapshot(ProviderRoiSnapshot):
        pass

    class Provider:
        def __init__(self) -> None:
            self.snapshot = SubclassedProviderSnapshot.model_validate(provider_snapshot().model_dump())
            mutate(self.snapshot)

        def discover(self, configured_target):
            return proof(configured_target)

        def read(self, configured_target, window):
            return self.snapshot

    configure_target(monkeypatch)
    signing_key = Ed25519PrivateKey.generate()
    configure_trusted_public_key(monkeypatch, signing_key)

    read = read_foundry_roi(WINDOW, provider=Provider(), verifier=FakeVerifier(signing_key))
    reconciliation = reconcile_foundry_roi(local_snapshot(), provider=Provider(), verifier=FakeVerifier(signing_key))

    assert name
    assert read.status.state == expected_state
    assert reconciliation["foundry_status"]["state"] == expected_state
    assert reconciliation["difference"] is None
    if expected_snapshot_value is None:
        assert read.provider_snapshot is None
        assert reconciliation["provider"] is None
    elif name == "currency":
        assert read.provider_snapshot is not None
        assert read.provider_snapshot["business_value"]["currency"] == expected_snapshot_value
        assert reconciliation["provider"]["business_value"]["currency"] == expected_snapshot_value
    else:
        assert read.provider_snapshot is not None
        assert read.provider_snapshot[name] == expected_snapshot_value
        assert reconciliation["provider"][name] == expected_snapshot_value


def test_provider_as_verifier_with_only_public_key_cannot_connect(monkeypatch) -> None:
    class Provider:
        def __init__(self, public_key: bytes) -> None:
            self.public_key = public_key

        def discover(self, configured_target):
            return proof(configured_target)

        def verify(self, configured_target, provider_proof, canonical_snapshot_bytes):
            attestation = VerifiedDiscoveryAttestation(
                target_fingerprint=configured_target.target_fingerprint,
                surface_id=provider_proof.surface_id,
                surface_version=provider_proof.surface_version,
                observed_at=provider_proof.observed_at,
                observed_source="provider_public_key_only",
            )
            return {
                "attestation": attestation,
                "key_id": sha256(self.public_key).hexdigest(),
                "snapshot_digest": sha256(canonical_snapshot_bytes).hexdigest(),
                "signature": base64.b64encode(b"0" * 64).decode("ascii"),
            }

        def read(self, configured_target, window):
            return provider_snapshot(target_fingerprint=configured_target.target_fingerprint, window=window)

    configure_target(monkeypatch)
    trusted_signing_key = Ed25519PrivateKey.generate()
    configure_trusted_public_key(monkeypatch, trusted_signing_key)
    provider = Provider(
        trusted_signing_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )

    result = read_foundry_roi(WINDOW, provider=provider, verifier=provider)

    assert result.status.state == "unavailable"
    assert result.provider_snapshot is None


def test_colluding_verifier_without_pinned_private_key_cannot_connect(monkeypatch) -> None:
    class Provider:
        def discover(self, configured_target):
            return proof(configured_target)

        def read(self, configured_target, window):
            return provider_snapshot(target_fingerprint=configured_target.target_fingerprint, window=window)

    configure_target(monkeypatch)
    trusted_signing_key = Ed25519PrivateKey.generate()
    configure_trusted_public_key(monkeypatch, trusted_signing_key)

    result = read_foundry_roi(WINDOW, provider=Provider(), verifier=FakeVerifier(Ed25519PrivateKey.generate()))

    assert result.status.state != "connected"
    assert result.provider_snapshot is None


def test_provider_proof_alone_is_configured_unverified_never_connected(monkeypatch) -> None:
    class Provider:
        def discover(self, configured_target):
            return proof(configured_target)

        def read(self, configured_target, window):  # pragma: no cover - read must not happen
            raise AssertionError("unexpected read")

    configure_target(monkeypatch)

    result = read_foundry_roi(WINDOW, provider=Provider())

    assert result.status.state == "configured_unverified"
    assert result.provider_snapshot is None


def test_lie_about_another_discovery_target_is_unavailable(monkeypatch) -> None:
    class LyingProvider:
        def discover(self, configured_target):
            return proof(configured_target, fingerprint="0" * 64)

        def read(self, configured_target, window):  # pragma: no cover - discovery must prevent read
            raise AssertionError("unexpected read")

    configure_target(monkeypatch)

    result = read_foundry_roi(WINDOW, provider=LyingProvider(), verifier=FakeVerifier(Ed25519PrivateKey.generate()))

    assert result.status.state == "unavailable"
    assert result.provider_snapshot is None


def test_snapshot_for_another_target_is_unavailable(monkeypatch) -> None:
    class LyingProvider:
        def discover(self, configured_target):
            return proof(configured_target)

        def read(self, configured_target, window):
            return provider_snapshot(target_fingerprint="0" * 64, window=window)

    configure_target(monkeypatch)
    signing_key = Ed25519PrivateKey.generate()
    configure_trusted_public_key(monkeypatch, signing_key)

    result = read_foundry_roi(WINDOW, provider=LyingProvider(), verifier=FakeVerifier(signing_key))

    assert result.status.state == "unavailable"
    assert result.provider_snapshot is None


def test_discovery_proof_requires_surface_and_utc_timestamp() -> None:
    with pytest.raises(ValidationError):
        DiscoveryProof(state="connected", target_fingerprint=target().target_fingerprint, surface_id="", surface_version="v1", observed_at="2026-07-02T01:05:00")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://project.services.ai.azure.com/api/projects/demo",
        "https://project.services.ai.azure.com:443/api/projects/demo",
        "https://user@project.services.ai.azure.com/api/projects/demo",
        "https://project.services.ai.azure.com/api/projects/demo?x=1",
        "https://project.services.ai.azure.com/api/projects/demo#x",
        "https://project.services.ai.azure.com/api/projects/demo/extra",
        "https://project.services.ai.azure.com/api/projects/",
        "https://project.services.ai.azure.com/api/projects/demo//",
        "https://project.evil.com/api/projects/demo",
    ],
)
def test_target_rejects_noncanonical_or_attack_endpoints(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        FoundryRoiTarget(project_endpoint=endpoint, agent_id=TARGET_AGENT_ID)


def test_target_only_accepts_strict_project_path_agent_id_and_stable_fingerprint() -> None:
    accepted = FoundryRoiTarget(project_endpoint="https://project.services.ai.azure.com/api/projects/_project", agent_id="agent_01-v2")

    assert len(accepted.target_fingerprint) == 64
    assert accepted.target_fingerprint == FoundryRoiTarget(project_endpoint="https://project.services.ai.azure.com/api/projects/_project", agent_id="agent_01-v2").target_fingerprint
    for agent_id in ("agent:demo", "agent/demo", "agent demo", "a" * 129):
        with pytest.raises(ValidationError):
            FoundryRoiTarget(project_endpoint=TARGET_ENDPOINT, agent_id=agent_id)


def test_provider_failure_is_unavailable_and_local_reconciliation_is_unchanged(monkeypatch) -> None:
    class FailingProvider:
        def discover(self, configured_target):
            raise RuntimeError("token=secret response body")

        def read(self, configured_target, window):  # pragma: no cover - discovery prevents this call
            raise AssertionError("unexpected read")

    configure_target(monkeypatch)

    provider = read_foundry_roi(WINDOW, provider=FailingProvider(), verifier=FakeVerifier(Ed25519PrivateKey.generate()))

    assert provider.status.state == "unavailable"
    assert "secret" not in str(provider)
    assert provider.provider_snapshot is None


def test_provider_values_are_reconciled_only_for_exact_run_and_outcome_sets(monkeypatch) -> None:
    local = local_snapshot(100.0, status="verified")
    exact = reconcile_with_provider(monkeypatch, local, provider_snapshot(90.0))

    assert exact["local"]["business_value"]["total"] == 100.0
    assert exact["local"]["status"] == "verified"
    assert exact["provider"]["business_value"]["amount"] == 90.0
    assert exact["provider"]["status"] == "estimated"
    assert exact["difference"] == {"amount": -10.0, "currency": "USD", "unit": "currency"}
    assert exact["reconciliation"]["reconciled"] is True

    for snapshot, reason in (
        (provider_snapshot(run_ids=["run-1"]), "run lineage"),
        (provider_snapshot(run_ids=["run-1", "run-2", "run-3"]), "run lineage"),
        (provider_snapshot(outcome_ids=["outcome-1"]), "outcome lineage"),
        (provider_snapshot(outcome_ids=["outcome-1", "outcome-2", "outcome-3"]), "outcome lineage"),
    ):
        result = reconcile_with_provider(monkeypatch, local, snapshot)
        assert result["difference"] is None
        assert result["reconciliation"]["reconciled"] is False
        assert reason in result["reconciliation"]["reason"]


def test_nonconnected_discovery_discards_even_a_valid_snapshot(monkeypatch) -> None:
    class Provider:
        def discover(self, configured_target):
            return proof(configured_target, state="not_configured")

        def read(self, configured_target, window):  # pragma: no cover - read must not happen
            raise AssertionError("unexpected read")

    configure_target(monkeypatch)
    result = read_foundry_roi(WINDOW, provider=Provider(), verifier=FakeVerifier(Ed25519PrivateKey.generate()))

    assert result.status.state == "not_configured"
    assert result.provider_snapshot is None


def test_public_api_exposes_no_verified_read_or_two_step_reconciliation_bypass() -> None:
    assert not hasattr(foundry_roi, "VerifiedProviderRead")
    assert not hasattr(foundry_roi, "reconcile_roi")
    assert not hasattr(foundry_roi, "reconcile_foundry_read")
    assert "VerifiedProviderRead" not in foundry_roi.__all__
    assert "reconcile_roi" not in foundry_roi.__all__
    assert "reconcile_foundry_read" not in foundry_roi.__all__


@pytest.mark.parametrize("amount", [math.nan, -1.0])
def test_provider_rejects_non_finite_or_negative_amounts(amount: float) -> None:
    with pytest.raises(ValidationError):
        provider_snapshot(amount)


@pytest.mark.parametrize("amount", [10**400, 10**1000])
def test_provider_amount_beyond_finite_json_boundary_is_unavailable(monkeypatch, amount) -> None:
    class Provider:
        def discover(self, configured_target):
            return proof(configured_target)

        def read(self, configured_target, window):
            return raw_provider_snapshot(amount)

    configure_target(monkeypatch)
    signing_key = Ed25519PrivateKey.generate()
    configure_trusted_public_key(monkeypatch, signing_key)

    read = read_foundry_roi(WINDOW, provider=Provider(), verifier=FakeVerifier(signing_key))
    reconciliation = reconcile_foundry_roi(local_snapshot(), provider=Provider(), verifier=FakeVerifier(signing_key))

    assert read.status.state == "unavailable"
    assert read.provider_snapshot is None
    assert reconciliation["foundry_status"]["state"] == "unavailable"
    assert reconciliation["provider"] is None
    assert reconciliation["difference"] is None
    json.dumps(read.model_dump(mode="json"), allow_nan=False)
    json.dumps(reconciliation, allow_nan=False)


def test_finite_float_boundary_amount_and_difference_remain_json_finite(monkeypatch) -> None:
    max_finite_float = float.fromhex("0x1.fffffffffffffp+1023")

    class Provider:
        def discover(self, configured_target):
            return proof(configured_target)

        def read(self, configured_target, window):
            return raw_provider_snapshot(max_finite_float)

    configure_target(monkeypatch)
    signing_key = Ed25519PrivateKey.generate()
    configure_trusted_public_key(monkeypatch, signing_key)

    read = read_foundry_roi(WINDOW, provider=Provider(), verifier=FakeVerifier(signing_key))
    reconciliation = reconcile_foundry_roi(local_snapshot(0.0), provider=Provider(), verifier=FakeVerifier(signing_key))

    assert read.status.state == "connected"
    assert read.provider_snapshot is not None
    assert math.isfinite(read.provider_snapshot["business_value"]["amount"])
    assert reconciliation["foundry_status"]["state"] == "connected"
    assert reconciliation["difference"] is not None
    assert math.isfinite(reconciliation["difference"]["amount"])
    json.dumps(read.model_dump(mode="json"), allow_nan=False)
    json.dumps(reconciliation, allow_nan=False)


def test_provider_helper_preserves_explicit_empty_lineage_lists() -> None:
    with pytest.raises(ValidationError):
        provider_snapshot(run_ids=[])
    with pytest.raises(ValidationError):
        provider_snapshot(outcome_ids=[])


def test_reconciliation_accepts_utc_equivalent_window_but_rejects_different_window(monkeypatch) -> None:
    equivalent = provider_snapshot(window={"from": "2026-07-01T00:00:00Z", "to": "2026-07-02T00:00:00Z"})
    different = provider_snapshot(window={"from": "2026-07-01T00:00:00Z", "to": "2026-07-02T00:00:01Z"})

    assert reconcile_with_provider(monkeypatch, local_snapshot(), equivalent)["reconciliation"]["reconciled"] is True
    result = reconcile_with_provider(monkeypatch, local_snapshot(), different)
    assert result["difference"] is None
    assert result["reconciliation"]["reconciled"] is False
    assert "window" in result["reconciliation"]["reason"].lower()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda local: local.update({"truncated": True, "lineage_complete": False}),
        lambda local: local.update({"invalid_run_ids": ["sha256:invalid"], "lineage_complete": False}),
        lambda local: local.update({"observed_run_ids": [], "lineage_complete": False}),
        lambda local: local.update({"outcome_event_ids": [], "lineage_complete": False}),
    ],
)
def test_incomplete_or_empty_local_lineage_never_reconciles(monkeypatch, mutate) -> None:
    local = local_snapshot()
    mutate(local)

    result = reconcile_with_provider(monkeypatch, local, provider_snapshot())

    assert result["difference"] is None
    assert result["reconciliation"]["reconciled"] is False
    assert result["reconciliation"]["reason"] == "local_lineage_incomplete"
