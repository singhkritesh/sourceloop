from sourceloop.api.config import load_settings


def test_per_user_limits_default_and_env(monkeypatch):
    for k in ("RATE_LIMIT_CREATE", "RATE_LIMIT_START", "RATE_LIMIT_RESOLVE"):
        monkeypatch.delenv(k, raising=False)
    s = load_settings()
    assert (s.create_limit, s.start_limit, s.resolve_limit) == ("10/hour", "5/hour", "60/minute")
    monkeypatch.setenv("RATE_LIMIT_CREATE", "500/hour")
    monkeypatch.setenv("RATE_LIMIT_START", "500/hour")
    monkeypatch.setenv("RATE_LIMIT_RESOLVE", "600/minute")
    s = load_settings()
    assert (s.create_limit, s.start_limit, s.resolve_limit) == ("500/hour", "500/hour", "600/minute")
