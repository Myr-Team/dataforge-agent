from pathlib import Path
import subprocess


COMPONENTS = Path(__file__).resolve().parents[1] / "web" / "src" / "components.jsx"
MAF_VIEW_MODEL_TEST = COMPONENTS.parent / "mafViewModel.test.mjs"


def test_runs_center_does_not_fake_missing_observability_or_calibration() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")

    assert 'const verdictLabel = r.verdict ? (VERDICT_LABELS[r.verdict] || r.verdict) : "未记录";' in source
    assert 't.app_insights && t.otel_sdk ? "已接入" : "未完整配置"' in source
    assert 'cg ? (cg.passed ? "通过" : "未过") : "未记录"' in source
    assert '不能据此宣称评分已校准' in source
    assert 'cg?.spearman ?? "1.00"' not in source
    assert 'cg?.cases ?? 5' not in source


def test_chat_transport_error_after_terminal_event_does_not_replace_the_result() -> None:
    app_source = (COMPONENTS.parent / "App.jsx").read_text(encoding="utf-8")

    assert "if (terminalEvent) {\n        refreshDashboard(workspaceId);\n        return;\n      }" in app_source


def test_verdict_hero_explains_dimension_only_audit_corrections() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")

    assert 'downgrade?.kind === "dimension"' in source
    assert "score_before" in source
    assert "score_after" in source


def test_settings_summary_does_not_use_stale_demo_status_values() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")

    assert "在线模型 4 个" not in source
    assert "存储用量 68%" not in source
    assert "2024.05.02.1021" not in source
    assert "loadWorkspaceSettings" in source
    assert "systemStatus.release" in source


def test_production_app_has_no_query_string_fake_dashboard() -> None:
    app_source = (COMPONENTS.parent / "App.jsx").read_text(encoding="utf-8")

    assert "DEMO_DASHBOARD" not in app_source
    assert "DEMO_SEED" not in app_source
    assert "消费电子新品机会评估" not in app_source
    assert "health: { ok: true" not in app_source


def test_dynamic_maf_view_model_behavior() -> None:
    result = subprocess.run(
        ["node", "--test", str(MAF_VIEW_MODEL_TEST)],
        cwd=COMPONENTS.parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_governance_ui_separates_estimated_roi_from_observed_outcomes() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")

    assert "roi.outcomes" in source
    assert "真实业务结果" in source
    assert "security.rbac_enforced" in source
    assert "governance?.foundry_monitoring" in source
    assert "估算口径；接入 Azure AI Foundry 原生 ROI 后可切换" not in source


def test_iteration_ui_uses_backend_experiment_deltas_and_source_lineage() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")
    api_source = (COMPONENTS.parent / "api.js").read_text(encoding="utf-8")

    assert "loadExperiments" in source
    assert "compareExperiments" in source
    assert "evidence_delta" in source
    assert "source_ref" in source
    assert "/experiments/compare" in api_source


def test_artifact_generation_uses_refresh_safe_background_jobs() -> None:
    app_source = (COMPONENTS.parent / "App.jsx").read_text(encoding="utf-8")
    api_source = (COMPONENTS.parent / "api.js").read_text(encoding="utf-8")

    assert "createArtifactJob" in app_source
    assert "loadArtifactJobs" in app_source
    assert "waitForArtifactJob" in app_source
    assert "/api/artifact-jobs" in api_source
    assert "/artifact-jobs`" in api_source
