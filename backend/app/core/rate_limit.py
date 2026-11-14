"""Login brute-force protection (Technical Plan section 5.1).

Counts FAILED login attempts per key (the IP address, and separately the
email/phone being tried) in a sliding time window. Once a key reaches the
limit, every attempt for it is refused with 429 until old failures age out,
even with the right password. Normal users who type correctly are never slowed down.

Why not the slowapi library named in the plan? slowapi keys limits on the
request only (e.g. IP); limiting per ACCOUNT needs the email from the request
body, and counting only failures needs the login result. This small class does
both. Logged as decision D12.

Limitation (documented for deployment): counts live in this process's memory,
so they reset on restart and aren't shared if several API processes run.
That's fine for one Uvicorn process; a shared store (e.g. Redis) would be
needed for several.
"""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class FailedAttemptLimiter:
    def __init__(
        self, max_failures: int, window_seconds: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()  # FastAPI runs sync endpoints in several threads

    def _prune(self, key: str, now: float) -> deque[float]:
        attempts = self._failures[key]
        while attempts and now - attempts[0] >= self.window_seconds:
            attempts.popleft()
        if not attempts:
            del self._failures[key]  # keep memory small
            return deque()
        return attempts

    def retry_after(self, *keys: str) -> int:
        """0 if allowed, otherwise the seconds until the oldest failure expires."""
        now = self._clock()
        wait = 0.0
        with self._lock:
            for key in keys:
                attempts = self._prune(key, now)
                if len(attempts) >= self.max_failures:
                    wait = max(wait, self.window_seconds - (now - attempts[0]))
        return int(wait) + 1 if wait > 0 else 0

    def record_failure(self, *keys: str) -> None:
        now = self._clock()
        with self._lock:
            for key in keys:
                self._failures[key].append(now)

    def reset(self, *keys: str) -> None:
        """Called after a successful login: that account starts fresh."""
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)


class LoginThrottle:
    """Two separate limits on failed logins:

    * per ACCOUNT (default 5/minute): stops password guessing on one account
    * per IP      (default 20/minute): stops one attacker trying many accounts,
      while not locking out the many innocent users who share one mobile-network IP
    """

    def __init__(self, *, per_account: int, per_ip: int, window_seconds: int) -> None:
        self.accounts = FailedAttemptLimiter(per_account, window_seconds)
        self.ips = FailedAttemptLimiter(per_ip, window_seconds)

    def retry_after(self, *, ip: str, account: str) -> int:
        return max(self.ips.retry_after(ip), self.accounts.retry_after(account))

    def record_failure(self, *, ip: str, account: str) -> None:
        self.ips.record_failure(ip)
        self.accounts.record_failure(account)

    def succeeded(self, *, account: str) -> None:
        # Only the ACCOUNT counter is reset. Resetting the IP counter would let an
        # attacker clear it by logging in to their own account between guesses.
        self.accounts.reset(account)
