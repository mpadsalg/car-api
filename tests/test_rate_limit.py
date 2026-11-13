from collections.abc import Callable

from fastapi.testclient import TestClient

from carapi.core.rate_limit import SlidingWindowLimiter
from tests.conftest import OTHER_KEY, TEST_KEY


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_limiter_sliding_window() -> None:
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=3, window=10, clock=clock)
    remaining = []
    for _ in range(3):  # hits at t=0, 2, 4
        remaining.append(lim.hit("a").remaining)
        clock.t += 2
    assert remaining == [2, 1, 0]
    blocked = lim.hit("a")  # t=6
    assert not blocked.allowed
    assert blocked.reset_after == 4  # oldest (t=0) expires at t=10
    clock.t += 4  # t=10: t=0 hit is exactly window-old -> expired; one slot frees
    r = lim.hit("a")
    assert r.allowed and r.remaining == 0
    assert not lim.hit("a").allowed  # t=2 hit still live
    clock.t += 2  # t=12: t=2 expires
    assert lim.hit("a").allowed


def test_limiter_identities_independent() -> None:
    lim = SlidingWindowLimiter(limit=1, window=10, clock=FakeClock())
    assert lim.hit("a").allowed
    assert not lim.hit("a").allowed
    assert lim.hit("b").allowed


def test_limiter_blocked_hits_not_counted() -> None:
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=1, window=10, clock=clock)
    lim.hit("a")
    for _ in range(5):
        lim.hit("a")
    clock.t += 10
    assert lim.hit("a").allowed


def test_limiter_sweeps_idle_identities() -> None:
    clock = FakeClock()
    lim = SlidingWindowLimiter(limit=5, window=10, clock=clock, sweep_every=3)
    lim.hit("a")
    lim.hit("b")
    clock.t += 11
    lim.hit("c")  # 3rd call triggers sweep of a, b
    assert len(lim) == 1


def test_http_headers_and_429(app_client: Callable[..., TestClient]) -> None:
    c = app_client(rate_limit_enabled=True, rate_limit_requests=2, rate_limit_window_seconds=60)
    r1 = c.get("/cars")
    assert r1.headers["x-ratelimit-limit"] == "2"
    assert r1.headers["x-ratelimit-remaining"] == "1"
    assert 1 <= int(r1.headers["x-ratelimit-reset"]) <= 60
    assert c.get("/cars").headers["x-ratelimit-remaining"] == "0"
    r3 = c.get("/cars")
    assert r3.status_code == 429
    assert int(r3.headers["retry-after"]) >= 1
    assert r3.headers["x-request-id"]
    err = r3.json()["error"]
    assert err["code"] == "rate_limited"
    assert err["request_id"] == r3.headers["x-request-id"]


def test_valid_key_gets_own_bucket(app_client: Callable[..., TestClient]) -> None:
    c = app_client(rate_limit_enabled=True, rate_limit_requests=1, api_keys=[TEST_KEY, OTHER_KEY])
    assert c.get("/cars").status_code == 200  # ip bucket
    assert c.get("/cars").status_code == 429
    assert c.get("/cars", headers={"X-API-Key": TEST_KEY}).status_code == 200
    assert c.get("/cars", headers={"X-API-Key": TEST_KEY}).status_code == 429
    assert c.get("/cars", headers={"X-API-Key": OTHER_KEY}).status_code == 200


def test_invalid_key_falls_back_to_ip_bucket(app_client: Callable[..., TestClient]) -> None:
    c = app_client(rate_limit_enabled=True, rate_limit_requests=1, api_keys=[TEST_KEY])
    assert c.get("/cars", headers={"X-API-Key": "bad1"}).status_code == 200
    # Rotating bogus keys doesn't buy fresh buckets.
    assert c.get("/cars", headers={"X-API-Key": "bad2"}).status_code == 429


def test_health_and_docs_exempt(app_client: Callable[..., TestClient]) -> None:
    c = app_client(rate_limit_enabled=True, rate_limit_requests=1)
    for _ in range(3):
        assert c.get("/health").status_code == 200
        assert c.get("/health/db").status_code == 200
        assert c.get("/openapi.json").status_code == 200
    assert "x-ratelimit-limit" not in c.get("/health").headers


def test_disabled(app_client: Callable[..., TestClient]) -> None:
    c = app_client(rate_limit_enabled=False, rate_limit_requests=1)
    assert all(c.get("/cars").status_code == 200 for _ in range(3))
    assert "x-ratelimit-limit" not in c.get("/cars").headers
