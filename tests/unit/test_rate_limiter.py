"""Tests rate limiter — Fase 7."""

import time

from procurement_platform.security.rate_limiter import RateLimiter, RateLimitExceeded


def test_rate_limiter_allow():
    rl = RateLimiter(limits={"test:key": (2, 60)})
    rl.check_and_hit("test:key")
    rl.check_and_hit("test:key")
    # third should exceed
    try:
        rl.check_and_hit("test:key")
        assert False, "should have raised"
    except RateLimitExceeded as e:
        assert e.key == "test:key"
        assert e.limit == 2


def test_rate_limiter_check():
    rl = RateLimiter(limits={"a:b": (1, 60)})
    allowed, _ = rl.check("a:b")
    assert allowed is True
    rl.hit("a:b")
    allowed2, retry = rl.check("a:b")
    assert allowed2 is False
    assert retry > 0


def test_rate_limiter_reset():
    rl = RateLimiter(limits={"x": (1, 60)})
    rl.hit("x")
    rl.reset("x")
    allowed, _ = rl.check("x")
    assert allowed is True


def test_rate_limiter_window_expiry():
    rl = RateLimiter(limits={"fast": (1, 1)})
    rl.hit("fast")
    time.sleep(1.1)
    allowed, _ = rl.check("fast")
    assert allowed is True


def test_global_limiter():
    from procurement_platform.security.rate_limiter import get_rate_limiter, reset_rate_limiter

    reset_rate_limiter()
    rl = get_rate_limiter()
    assert rl is not None
    rl2 = get_rate_limiter()
    assert rl is rl2
