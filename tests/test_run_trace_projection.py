from backend import control_plane


def test_public_trace_projection_redacts_provider_credentials() -> None:
    value = control_plane._public_detail_projection(
        {
            "api_key": "secret-marker",
            "Authorization": "Bearer secret-marker",
            "access_token": "secret-marker",
            "model": "deepseek-chat",
        },
        depth=0,
    )

    assert value["api_key"] == "[redacted]"
    assert value["Authorization"] == "[redacted]"
    assert value["access_token"] == "[redacted]"
    assert value["model"] == "deepseek-chat"
    assert "secret-marker" not in str(value)


def test_model_response_trace_keeps_safe_route_and_separate_cache_evidence() -> None:
    run = {
        "steps": [
            {
                "time": "2026-08-11T01:00:00Z",
                "event": "model_response",
                "data": {
                    "agent": "df-feasibility-analyst",
                    "route": "ds_flash",
                    "deployment": "deepseek-v4-flash",
                    "provider_type": "deepseek",
                    "model_id": "deepseek-v4-flash",
                    "gateway_coverage": "apim_governed",
                    "result_cache": {
                        "state": "miss",
                        "provider": "redis",
                        "eligible": True,
                        "reason": "eligible",
                        "policy_revision": 3,
                    },
                    "provider_cache": {
                        "state": "partial_hit",
                        "hit_tokens": 80,
                        "miss_tokens": 20,
                        "hit_rate_pct": 80,
                        "evidence_state": "observed",
                    },
                },
            }
        ]
    }

    [trace] = control_plane.trace_from_run(run)
    detail = trace["detail"]
    assert detail["provider_type"] == "deepseek"
    assert detail["model_id"] == "deepseek-v4-flash"
    assert detail["gateway_coverage"] == "apim_governed"
    assert detail["result_cache"]["state"] == "miss"
    assert detail["provider_cache"]["hit_tokens"] == 80
