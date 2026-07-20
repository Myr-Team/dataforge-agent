from pathlib import Path
import subprocess


COMPONENTS = Path(__file__).resolve().parents[1] / "web" / "src" / "components.jsx"
MAF_VIEW_MODEL_TEST = COMPONENTS.parent / "mafViewModel.test.mjs"


def test_runs_center_does_not_fake_missing_observability_or_calibration() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")

    assert 'const verdictLabel = r.verdict ? (VERDICT_LABELS[r.verdict] || r.verdict) : "未记录";' in source
    assert 't.app_insights && t.otel_sdk ? "已配置" : "未完整配置"' in source
    assert 'cg ? (cg.passed ? "通过" : "未过") : "未记录"' in source
    assert "本工作区尚未返回 rubric 校准结果，不能据此宣称评分已校准。" in source
    assert 'cg?.spearman ?? "1.00"' not in source
    assert 'cg?.cases ?? 5' not in source


def test_chat_transport_error_after_terminal_event_does_not_replace_the_result() -> None:
    app_source = (COMPONENTS.parent / "App.jsx").read_text(encoding="utf-8")

    assert "if (terminalEvent) {\n        refreshDashboard(workspaceId);\n        return;\n      }" in app_source


def test_conversation_trace_reference_derives_workspace_scope_from_dashboard() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")
    start = source.index("function ConversationStudio(")
    end = source.index("const OUTPUT_PRODUCTS", start)
    conversation = source[start:end]

    assert 'const workspaceId = dashboard?.workspace_id || workspace?.workspace_id || "";' in conversation
    assert "<AnswerPanel workspaceId={workspaceId}" in conversation


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
    api_source = (COMPONENTS.parent / "api.js").read_text(encoding="utf-8")

    assert "CostValuePanel" in source
    assert "成本与价值" in source
    assert "情景估算" in source
    assert 'foundry.official ? "官方来源" : "未用于 ROI 结论"' in source
    assert "loadWorkspaceCostValue" in source
    assert "/governance/cost-value" in api_source
    assert "roi.provider.businessValue.text" not in source
    assert "governancePermissions(" in source


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
    assert "const active = (data?.jobs || []).filter" in app_source
    assert "await Promise.all(active.map" in app_source
    assert "if (isTransientFetchError(error))" in app_source


def test_production_api_uses_authenticated_same_origin_proxy() -> None:
    api_source = (COMPONENTS.parent / "api.js").read_text(encoding="utf-8")
    docker_source = (COMPONENTS.parents[1] / "Dockerfile").read_text(encoding="utf-8")
    nginx_source = (COMPONENTS.parents[1] / "nginx.conf.template").read_text(encoding="utf-8")

    assert 'import.meta.env?.VITE_API_BASE ?? ""' in api_source
    assert 'ARG VITE_API_BASE=""' in docker_source
    assert "ENV DF_BACKEND_UPSTREAM=https://ca-dataforge-backend" in docker_source
    assert "proxy_pass ${DF_BACKEND_UPSTREAM};" in nginx_source
    assert "X-DataForge-Proxy-Secret" in nginx_source
    assert "X-MS-CLIENT-PRINCIPAL" in nginx_source
