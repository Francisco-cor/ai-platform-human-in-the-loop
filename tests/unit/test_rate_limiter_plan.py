"""Per-tenant plan limits — F3-5."""

from procurement_platform.security.rate_limiter import (
    RateLimiter,
    get_rate_limiter,
    set_tenant_plan,
    reset_rate_limiter,
)


def test_plan_limits():
    reset_rate_limiter()
    rl = RateLimiter()
    # free tenant 60
    set_tenant_plan("tenant_demo", "free")
    # pro tenant 600
    set_tenant_plan("tenant_pro", "pro")
    # check limits via _get_limit
    assert rl._get_limit("api:create_execution:tenant_demo") == (60, 60)
    assert rl._get_limit("api:create_execution:tenant_pro") == (600, 60)
    # unknown tenant defaults to 60 via base
    assert rl._get_limit("api:create_execution:tenant_unknown") == (60, 60)
    # enterprise
    set_tenant_plan("tenant_ent", "enterprise")
    assert rl._get_limit("api:create_execution:tenant_ent") == (6000, 60)
    reset_rate_limiter()


def test_rate_limit_enforced_per_plan():
    reset_rate_limiter()
    from procurement_platform.security.rate_limiter import _PLAN_LIMITS

    # temporarily set free to 2 for test
    orig_free = _PLAN_LIMITS["free"]["api:create_execution"]
    _PLAN_LIMITS["free"]["api:create_execution"] = (2, 60)
    rl = get_rate_limiter()
    set_tenant_plan("tenant_demo", "free")
    rl.hit("api:create_execution:tenant_demo")
    rl.hit("api:create_execution:tenant_demo")
    try:
        rl.hit("api:create_execution:tenant_demo")
        assert False, "should have rate limited"
    except Exception as e:
        assert "rate_limited" in str(e)
    # restore
    _PLAN_LIMITS["free"]["api:create_execution"] = orig_free
    # pro tenant should have 600, so 3 hits not limited
    set_tenant_plan("tenant_pro", "pro")
    rl.reset("api:create_execution:tenant_pro")
    for _ in range(3):
        rl.hit("api:create_execution:tenant_pro")  # should not raise
    reset_rate_limiter()
