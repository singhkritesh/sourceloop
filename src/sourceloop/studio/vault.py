"""Write-only provider key storage (rule 15d). Fernet-encrypted at rest; the secret is only ever returned by
`resolve()` for server-side use. Nothing here puts a secret in repr, exceptions, logs, exports or diffs."""
from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import MutableMapping
from datetime import datetime
from typing import Protocol

import structlog
from cryptography.fernet import Fernet, InvalidToken

from sourceloop.clock import Clock, SystemClock

ENV_KEY = "SOURCELOOP_VAULT_KEY"
MAX_SECRET_LEN = 4096
log = structlog.get_logger(__name__)


class VaultError(Exception):
    """Carries a short code only; never any key material."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

    def __repr__(self) -> str:
        return f"VaultError({self.code!r})"


class VaultBackend(Protocol):
    """Persistence for ciphertext. The SQL store implements this over the `secret` table."""
    def get(self, provider: str) -> tuple[bytes, str] | None: ...
    def put(self, provider: str, token: bytes, updated_at: str) -> None: ...
    def delete(self, provider: str) -> None: ...


class MemoryVaultBackend:
    def __init__(self) -> None:
        self._d: MutableMapping[str, tuple[bytes, str]] = {}

    def get(self, provider: str) -> tuple[bytes, str] | None:
        return self._d.get(provider)

    def put(self, provider: str, token: bytes, updated_at: str) -> None:
        self._d[provider] = (token, updated_at)

    def delete(self, provider: str) -> None:
        self._d.pop(provider, None)

    def __repr__(self) -> str:
        return f"MemoryVaultBackend(entries={len(self._d)})"


def _fernet_from_env() -> Fernet:
    raw = os.environ.get(ENV_KEY)
    if raw:
        try:
            return Fernet(raw.encode())
        except Exception:  # noqa: BLE001 - never echo the value
            raise VaultError("VAULT_KEY_INVALID") from None
    # DEV FALLBACK ONLY: deterministic key so a local install works without setup. NOT secure for real keys.
    log.warning("vault.dev_master_key_in_use", hint=f"set {ENV_KEY} to a Fernet key (Fernet.generate_key())")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"sourceloop-DEV-ONLY-vault-key").digest()))


class Vault:
    def __init__(self, backend: VaultBackend | None = None, clock: Clock | None = None, fernet: Fernet | None = None):
        self._backend = backend or MemoryVaultBackend()
        self._clock = clock or SystemClock()
        self._f = fernet or _fernet_from_env()

    # ---- write-only surface ----
    def put_key(self, provider: str, secret: str) -> str:
        """Set/rotate. Returns the `updated_at` stamp (ISO) for the UI's 'last updated'."""
        if not provider or not secret or not secret.strip() or len(secret) > MAX_SECRET_LEN:
            raise VaultError("SECRET_INVALID")
        stamp = self._clock.now().isoformat()
        self._backend.put(provider, self._f.encrypt(secret.encode()), stamp)
        return stamp

    def has_key(self, provider: str) -> bool:
        return self._backend.get(provider) is not None

    def updated_at(self, provider: str) -> datetime | None:
        row = self._backend.get(provider)
        return datetime.fromisoformat(row[1]) if row else None

    def delete_key(self, provider: str) -> None:
        self._backend.delete(provider)

    def resolve(self, provider: str) -> str:
        """SERVER-SIDE USE ONLY (building the outbound auth header). Never return this to a client or log it."""
        row = self._backend.get(provider)
        if row is None:
            raise VaultError("KEY_NOT_SET")
        try:
            return self._f.decrypt(row[0]).decode()
        except InvalidToken:
            raise VaultError("KEY_UNREADABLE") from None

    def has_key_map(self, providers: list[str]) -> dict[str, bool]:
        return {p: self.has_key(p) for p in providers}

    # ---- no accidental serialisation ----
    def __repr__(self) -> str:
        return "Vault(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("Vault is not serialisable")

    def __getstate__(self):
        raise TypeError("Vault is not serialisable")
