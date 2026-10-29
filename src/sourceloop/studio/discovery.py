"""Provider connectivity test + model discovery + live provider construction. All egress goes through `net.safe_http`
(host allowlist, private ranges blocked unless the provider is `is_local`, no redirects, 2 MB cap). Results carry short
codes only: never upstream text, never a key."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from sourceloop.data.enums import ProviderKindEnum as K
from sourceloop.llm.providers import AnthropicProvider, OllamaProvider, OpenAICompatProvider, StubProvider
from sourceloop.net.safe_http import EgressBlocked, SafeHttp
from sourceloop.studio.schema import ProviderDef, StudioConfig
from sourceloop.studio.validator import classify_host
from sourceloop.studio.vault import Vault, VaultError

ANTHROPIC_URL = "https://api.anthropic.com"


class ProviderConfigError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def safe_http_for(d: ProviderDef, timeout_s: float = 10.0) -> SafeHttp:
    base = d.base_url or (ANTHROPIC_URL if d.kind is K.ANTHROPIC else "")
    u = urlparse(base)
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host or u.username or u.password:
        raise ProviderConfigError("URL_INVALID")
    kind = classify_host(host)
    if kind in ("metadata", "invalid"):
        raise ProviderConfigError("URL_FORBIDDEN_HOST")
    if kind == "private" and not d.is_local:
        raise ProviderConfigError("URL_PRIVATE_NOT_LOCAL")
    local = d.is_local and kind == "private"  # is_local never widens egress to a public host
    return SafeHttp(set() if local else {host}, {host} if local else set(), timeout_s)


def _key_fn(vault: Vault | None, name: str) -> Callable[[], str]:
    if vault is None:
        raise ProviderConfigError("KEY_MISSING")
    return lambda: vault.resolve(name)


async def probe(name: str, d: ProviderDef, vault: Vault | None) -> dict[str, Any]:
    """-> {ok, models: [str], error?: code}. Ollama: GET /api/tags."""
    try:
        if d.kind is K.STUB:
            return {"ok": True, "models": ["stub-1"]}
        http = safe_http_for(d, 5.0)
        if d.kind is K.OLLAMA:
            models = [m["name"] for m in await OllamaProvider(d.base_url, http).list_models()]
        else:
            if vault is None or not vault.has_key(name):
                if d.kind is K.ANTHROPIC:
                    return {"ok": False, "models": [], "error": "KEY_MISSING"}
            base = (d.base_url or ANTHROPIC_URL).rstrip("/")
            headers: dict[str, str] = {}
            if vault is not None and vault.has_key(name):
                k = vault.resolve(name)
                headers = {"x-api-key": k, "anthropic-version": "2023-06-01"} if d.kind is K.ANTHROPIC else {"Authorization": f"Bearer {k}"}
            r = await http.get(f"{base}/v1/models", headers=headers)
            if r.status_code in (401, 403):
                return {"ok": False, "models": [], "error": "AUTH_FAILED"}
            if r.status_code >= 400:
                return {"ok": False, "models": [], "error": "UPSTREAM_ERROR"}
            models = [m["id"] for m in r.json().get("data", [])]
        return {"ok": True, "models": sorted(models)}
    except ProviderConfigError as e:
        return {"ok": False, "models": [], "error": e.code}
    except EgressBlocked as e:
        return {"ok": False, "models": [], "error": f"BLOCKED_{e.code}"}
    except VaultError as e:
        return {"ok": False, "models": [], "error": e.code}
    except (ValueError, KeyError, TypeError):
        return {"ok": False, "models": [], "error": "BAD_RESPONSE"}
    except Exception as e:  # noqa: BLE001 - transport / HTTP-status errors from the provider client; codes only, no text
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status is not None:
            return {"ok": False, "models": [], "error": "AUTH_FAILED" if status in (401, 403) else "UPSTREAM_ERROR"}
        return {"ok": False, "models": [], "error": "UNREACHABLE"}


def used_providers(cfg: StudioConfig) -> set[str]:
    names = {m.provider for m in cfg.llm.roles.values()}
    names |= {a.model_override.provider for a in cfg.agents.values() if a.model_override is not None}
    return names


def build_providers(cfg: StudioConfig, vault: Vault | None, timeout_s: float = 180.0) -> dict[str, Any]:
    """Provider objects for the providers the config actually uses (live runs)."""
    out: dict[str, Any] = {}
    for name in used_providers(cfg):
        d = cfg.providers.get(name)
        if d is None:
            raise ProviderConfigError("PROVIDER_UNDEFINED")
        if d.kind is K.STUB:
            out[name] = StubProvider()
            continue
        http = safe_http_for(d, timeout_s)
        if d.kind is K.OLLAMA:
            out[name] = OllamaProvider(d.base_url, http)
        elif d.kind is K.ANTHROPIC:
            out[name] = AnthropicProvider(http, _key_fn(vault, name), d.base_url or ANTHROPIC_URL)
        else:
            out[name] = OpenAICompatProvider(d.base_url, http, _key_fn(vault, name) if vault and vault.has_key(name) else None)
    return out
