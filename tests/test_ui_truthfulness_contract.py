from pathlib import Path


COMPONENTS = Path(__file__).resolve().parents[1] / "web" / "src" / "components.jsx"


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


def test_agent_flow_reads_dynamic_maf_events() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")

    for event in (
        "maf_plan",
        "maf_agent_started",
        "maf_agent_completed",
        "maf_agent_failed",
        "maf_branch_started",
        "maf_branch_joined",
        "maf_handoff",
        "maf_review",
        "maf_fallback",
    ):
        assert event in source


def test_maf_ui_does_not_render_fixed_participant_success() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")

    assert "function deriveMafViewModel(" in source
    assert "selected_agents" in source
    assert "skipped_agents" in source
    assert "summary?.maf" in source
    for mode in ("direct", "concurrent_research", "specialist_handoff", "bounded_review"):
        assert mode in source


def test_dynamic_maf_preserves_legacy_workflow_rendering() -> None:
    source = COMPONENTS.read_text(encoding="utf-8")

    assert 'item.event === "maf_workflow"' in source
    assert 'className="maf-panel"' in source
    assert "mafTimeline" in source
