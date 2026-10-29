"""Runtime settings. Bootstrap secrets are env-only; user-changeable values (llm_mode, token budget) are read from the
`app_setting` row first (see `service.effective_settings`) with these env values as the fallback."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


def _csv(v: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in v.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    app_env: str = "dev"
    database_url: str = "sqlite+aiosqlite:///./data/sourceloop.db"
    jwt_secret: str = field(default="", repr=False)
    org_signing_secret: str = field(default="", repr=False)
    config_encryption_key: str = field(default="", repr=False)
    cors_origins: tuple[str, ...] = ("http://localhost:3010",)
    llm_mode: str = "replay"
    token_budget_per_project: int = 400_000
    default_limit: str = "600/minute"
    login_limit: str = "20/minute"
    max_failed_logins: int = 5  # failures of one (account, IP) pair before THAT pair is locked
    max_failed_logins_account: int = 25  # failures from ANY IP before the account itself is locked (needs several IPs)
    max_failed_logins_per_ip: int = 30  # failed logins from one IP (any accounts) per lockout window before it gets 429
    lockout_minutes: int = 15
    access_ttl_minutes: int = 15
    refresh_ttl_hours: int = 8
    registration_enabled: bool = True
    demo_role_login: bool = True  # test bed: pick a seeded demo role instead of typing a password; never honoured in prod
    argon_time_cost: int = 3
    argon_memory_cost: int = 65536
    argon_parallelism: int = 4
    max_concurrent_runs: int = 4
    max_streams_per_user: int = 3
    create_limit: str = "10/hour"  # PRD 12.1, per user
    start_limit: str = "5/hour"  # per user (start + resume)
    resolve_limit: str = "60/minute"  # per user
    max_live_handles: int = 200  # global cap on in-memory run handles (CAPACITY_EXCEEDED beyond it)
    draft_ttl_minutes: int = 30  # idle DRAFT handles older than this are evicted (the row stays; start rebuilds the runtime)
    max_active_projects_per_org: int = 20  # non-terminal projects per buyer org (QUOTA_EXCEEDED beyond this)
    trusted_proxy_hops: int = 0  # number of reverse proxies in front of the API (X-Forwarded-For is honoured only when > 0)
    baseline_timeout_s: float = 60.0  # bound for the scenario baseline run done while creating a project
    max_baseline_runs: int = 2
    log_level: str = "INFO"
    audit_anchor_path: str = ""  # external append-only anchor file (empty = off). Compose / start.sh --local set it
    cookie_secure: bool | None = None  # None => dev: Secure unless the request host is localhost; prod: always true (config, not Host)

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


def load_settings(**overrides: object) -> Settings:
    e = os.environ
    env = e.get("APP_ENV", "dev")
    jwt = e.get("JWT_SECRET", "")
    if len(jwt.encode()) < 32:
        if env == "prod":
            raise RuntimeError("JWT_SECRET must be at least 32 bytes in prod")
        jwt = secrets.token_urlsafe(48)  # dev: per-process secret (sessions reset on restart)
    sign = e.get("ORG_SIGNING_SECRET", "")
    if env == "prod":
        if len(sign.encode()) < 32:
            raise RuntimeError("ORG_SIGNING_SECRET must be set (>= 32 bytes) in prod; it never defaults to JWT_SECRET")
    elif not sign:
        sign = jwt  # dev only
    vault_key = e.get("SOURCELOOP_VAULT_KEY", "") or e.get("CONFIG_ENCRYPTION_KEY", "")  # canonical name + legacy alias
    if env == "prod":
        try:
            from cryptography.fernet import Fernet

            Fernet(vault_key.encode())
        except Exception:  # noqa: BLE001 - never echo the value
            raise RuntimeError("SOURCELOOP_VAULT_KEY must be a valid Fernet key in prod") from None
    origins = _csv(e.get("CORS_ORIGINS", "" if env == "prod" else "http://localhost:3010"))
    if env == "prod":
        if not origins or any(o == "*" or not o.startswith("https://") for o in origins):
            raise RuntimeError("CORS_ORIGINS must list explicit https:// origins in prod")
        if e.get("COOKIE_SECURE", "true").lower() != "true":
            raise RuntimeError("COOKIE_SECURE must be true in prod")
    cookie_secure = True if env == "prod" else (None if "COOKIE_SECURE" not in e else e["COOKIE_SECURE"].lower() == "true")
    s = Settings(
        app_env=env,
        cookie_secure=cookie_secure,
        database_url=e.get("DATABASE_URL", Settings.database_url),
        jwt_secret=jwt,
        org_signing_secret=sign,
        config_encryption_key=vault_key,
        cors_origins=origins,
        llm_mode=e.get("LLM_MODE", "replay"),
        token_budget_per_project=int(e.get("TOKEN_BUDGET_PER_PROJECT", "400000")),
        default_limit=e.get("RATE_LIMIT_DEFAULT", Settings.default_limit),
        login_limit=e.get("RATE_LIMIT_LOGIN", Settings.login_limit),
        create_limit=e.get("RATE_LIMIT_CREATE", Settings.create_limit),
        start_limit=e.get("RATE_LIMIT_START", Settings.start_limit),
        resolve_limit=e.get("RATE_LIMIT_RESOLVE", Settings.resolve_limit),
        log_level=e.get("LOG_LEVEL", "INFO").upper(),
        audit_anchor_path=e.get("AUDIT_ANCHOR_PATH", ""),
        demo_role_login=env != "prod" and e.get("DEMO_ROLE_LOGIN", "true").lower() == "true",
        registration_enabled=e.get("REGISTRATION_ENABLED", "false" if env == "prod" else "true").lower() == "true",
        max_active_projects_per_org=int(e.get("MAX_ACTIVE_PROJECTS_PER_ORG", "20")),
        trusted_proxy_hops=max(0, int(e.get("TRUSTED_PROXY_HOPS", "0"))),
        max_live_handles=int(e.get("MAX_LIVE_HANDLES", "200")),
        draft_ttl_minutes=int(e.get("DRAFT_TTL_MINUTES", "30")),
    )
    if overrides:
        from dataclasses import replace

        s = replace(s, **overrides)  # type: ignore[arg-type]
    return s
