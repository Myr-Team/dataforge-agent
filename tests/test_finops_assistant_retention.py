from __future__ import annotations

import json

import backend.finops.assistant_retention as assistant_retention


class _Store:
    def __init__(self, purged: int) -> None:
        self._purged = purged
        self.called = False

    def purge_expired(self, now=None) -> int:
        self.called = True
        return self._purged


def test_assistant_retention_job_reports_purged_count(monkeypatch, capsys) -> None:
    store = _Store(3)
    monkeypatch.setattr(assistant_retention, "_store", lambda: store)

    assert assistant_retention.main() == 0
    assert store.called is True
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload == {"status": "completed", "purged_conversations": 3}


def test_assistant_retention_job_hides_error_detail(monkeypatch, capsys) -> None:
    class _Failing:
        def purge_expired(self, now=None) -> int:
            raise RuntimeError("connection string secret leak")

    monkeypatch.setattr(assistant_retention, "_store", lambda: _Failing())

    assert assistant_retention.main() == 1
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "failed"
    assert "secret" not in json.dumps(payload)
