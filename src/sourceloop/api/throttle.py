"""Failed-login throttling (PRD 12.1: lockout per account + IP).

Three independent counters, so nobody can lock a victim out cheaply and one IP cannot lock many accounts:
  * (account, ip) pair  - `max_failed_logins` failures lock only THAT pair; the real user (another IP) is unaffected;
  * account (any IP)    - the DB counter `user.failed_logins`; locks the account only at the higher
                          `max_failed_logins_account` threshold, i.e. after failures from several distinct IPs;
  * ip (any accounts)   - `max_failed_logins_per_ip` failures per lockout window => 429 + Retry-After for that IP.
State is per process (like the slowapi buckets); the account counter is durable. Bounded: expired entries are pruned and the
tables are size-capped, so the throttle itself cannot be used to exhaust memory.
"""
from __future__ import annotations

import hashlib
from collections import deque
from datetime import datetime, timedelta

_MAX_ENTRIES = 20_000


def _pair_key(email: str, ip: str) -> str:
    return hashlib.sha256(f"{email}|{ip}".encode()).hexdigest()[:24]  # no emails kept in memory


class LoginThrottle:
    def __init__(self, pair_max: int, ip_max: int, window: timedelta) -> None:
        self.pair_max, self.ip_max, self.window = pair_max, ip_max, window
        self._pairs: dict[str, tuple[int, datetime]] = {}  # key -> (failures, lock-or-window expiry)
        self._ips: dict[str, deque[datetime]] = {}

    def _prune(self, now: datetime) -> None:
        if len(self._pairs) > _MAX_ENTRIES:
            self._pairs = {k: v for k, v in self._pairs.items() if v[1] > now}
        if len(self._ips) > _MAX_ENTRIES:
            self._ips = {k: v for k, v in self._ips.items() if v and v[-1] + self.window > now}
        while len(self._pairs) > _MAX_ENTRIES:  # still full of live entries: drop the oldest
            self._pairs.pop(next(iter(self._pairs)))
        while len(self._ips) > _MAX_ENTRIES:
            self._ips.pop(next(iter(self._ips)))

    def ip_retry_after(self, ip: str, now: datetime) -> int | None:
        q = self._ips.get(ip)
        if not q:
            return None
        while q and q[0] + self.window <= now:
            q.popleft()
        if len(q) >= self.ip_max:
            return max(1, int((q[0] + self.window - now).total_seconds()))
        return None

    def pair_locked(self, email: str, ip: str, now: datetime) -> bool:
        n, until = self._pairs.get(_pair_key(email, ip), (0, now))
        return n >= self.pair_max and until > now

    def record_failure(self, email: str, ip: str, now: datetime) -> None:
        self._prune(now)
        k = _pair_key(email, ip)
        n, until = self._pairs.get(k, (0, now))
        n = 1 if until <= now else n + 1  # an expired window / lock starts over
        self._pairs[k] = (n, now + self.window)
        self._ips.setdefault(ip, deque()).append(now)

    def record_ip_failure(self, ip: str, now: datetime) -> None:
        self._ips.setdefault(ip, deque()).append(now)

    def record_success(self, email: str, ip: str) -> None:
        self._pairs.pop(_pair_key(email, ip), None)
