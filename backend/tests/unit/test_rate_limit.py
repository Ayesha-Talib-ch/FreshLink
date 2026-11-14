"""The failed-login limiter, with a fake clock so time can be moved forward instantly."""

from app.core.rate_limit import FailedAttemptLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_until_limit_then_blocks() -> None:
    limiter = FailedAttemptLimiter(3, 60, clock=FakeClock())
    for _ in range(2):
        limiter.record_failure("ip:1")
        assert limiter.retry_after("ip:1") == 0
    limiter.record_failure("ip:1")
    assert limiter.retry_after("ip:1") > 0


def test_block_ends_when_failures_age_out() -> None:
    clock = FakeClock()
    limiter = FailedAttemptLimiter(2, 60, clock=clock)
    limiter.record_failure("k")
    clock.now += 30
    limiter.record_failure("k")
    assert 29 <= limiter.retry_after("k") <= 31  # first failure expires in 30 s
    clock.now += 30
    assert limiter.retry_after("k") == 0  # sliding window: one failure left
    limiter.record_failure("k")
    assert limiter.retry_after("k") > 0


def test_any_blocked_key_blocks_the_attempt() -> None:
    limiter = FailedAttemptLimiter(1, 60, clock=FakeClock())
    limiter.record_failure("account:a@example.com")
    assert limiter.retry_after("ip:9", "account:a@example.com") > 0
    assert limiter.retry_after("ip:9", "account:b@example.com") == 0


def test_reset_clears_only_given_key() -> None:
    limiter = FailedAttemptLimiter(1, 60, clock=FakeClock())
    limiter.record_failure("ip:1", "account:x")
    limiter.reset("account:x")
    assert limiter.retry_after("account:x") == 0
    assert limiter.retry_after("ip:1") > 0


def test_expired_keys_are_removed_from_memory() -> None:
    clock = FakeClock()
    limiter = FailedAttemptLimiter(5, 60, clock=clock)
    limiter.record_failure("old")
    clock.now += 61
    limiter.retry_after("old")
    assert "old" not in limiter._failures


def test_login_throttle_has_separate_account_and_ip_limits() -> None:
    from app.core.rate_limit import LoginThrottle

    throttle = LoginThrottle(per_account=5, per_ip=20, window_seconds=60)
    # Many people behind one shared mobile-network IP each mistype once:
    for n in range(19):
        throttle.record_failure(ip="ip:shared", account=f"account:user{n}")
    assert throttle.retry_after(ip="ip:shared", account="account:someone-new") == 0  # not locked out
    throttle.record_failure(ip="ip:shared", account="account:user19")
    assert throttle.retry_after(ip="ip:shared", account="account:someone-new") > 0  # 20 = attack pattern

    # Guessing ONE account is stopped after 5, from any IP:
    for n in range(5):
        throttle.record_failure(ip=f"ip:{n}", account="account:victim")
    assert throttle.retry_after(ip="ip:fresh", account="account:victim") > 0


def test_success_resets_only_the_account_counter() -> None:
    from app.core.rate_limit import LoginThrottle

    throttle = LoginThrottle(per_account=2, per_ip=2, window_seconds=60)
    throttle.record_failure(ip="ip:1", account="account:a")
    throttle.record_failure(ip="ip:1", account="account:a")
    throttle.succeeded(account="account:a")
    assert throttle.accounts.retry_after("account:a") == 0
    assert throttle.ips.retry_after("ip:1") > 0
