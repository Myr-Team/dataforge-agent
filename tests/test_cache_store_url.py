from backend import cache_store


def test_redis_host_override_preserves_secret_port_and_database(monkeypatch):
    monkeypatch.setenv(
        "DF_REDIS_URL",
        "redis://:p%40ss@old.internal.example:6379/2?socket_keepalive=true",
    )
    monkeypatch.setenv("DF_REDIS_HOST_OVERRIDE", "ca-dataforge-redis")

    assert cache_store._redis_connection_url() == (
        "redis://:p%40ss@ca-dataforge-redis:6379/2?socket_keepalive=true"
    )


def test_redis_host_override_rejects_a_url_or_credentials(monkeypatch):
    original = "redis://:secret@old.internal.example:6379/0"
    monkeypatch.setenv("DF_REDIS_URL", original)
    monkeypatch.setenv(
        "DF_REDIS_HOST_OVERRIDE",
        "redis://attacker:secret@outside.example:6380/9",
    )

    assert cache_store._redis_connection_url() == original
